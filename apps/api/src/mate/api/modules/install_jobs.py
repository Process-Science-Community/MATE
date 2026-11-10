"""Module install pipeline (§7.6.2).

Modules are installed from an uploaded archive only, implemented as a
`@job`-style handler on the platform's `JobRuntime` so progress flows to the
bottom-left dock and the jobs drawer just like an event-log import:

- ``module.install.upload`` - operator uploaded a zip / tar.gz from the UI
  (`POST /api/v1/modules/install`). The route writes the bytes to a staging
  tmpdir and submits a job carrying the file path.

The handler normally ends in `install_staged`, which builds and import-checks the
upload in `uploaded_modules/.staging/<id>`, swaps it in - keeping the version it
replaces in `uploaded_modules/.prev/<id>` - and loads it with `loader.load_one`, so
the module becomes available without a restart; a load that fails after the swap
restores `.prev` automatically. Each successful install is appended to
`uploaded_modules/.history/<id>.json`, and `rollback_module` (admin) reinstalls
`.prev` the same way. Dot-prefixed dirs are invisible to discovery and the
uploaded-module GC. When another user already owns a byte-identical module under
the same id, the handler instead reuses the loaded code and just records this
user's ownership (content-addressed sharing - see `_stage_validated_upload`).
"""

from __future__ import annotations

import asyncio
import contextlib
import json
import shutil
import subprocess
import sys
import tarfile
import tempfile
import zipfile
from datetime import UTC, datetime
from pathlib import Path
from typing import TYPE_CHECKING, Any, cast

import structlog

from mate.api.db.engine import get_sessionmaker
from mate.api.jobs.runtime import JobHandle, JobRuntime
from mate.api.modules.content_hash import (
    module_content_hash,
    read_content_hash,
    write_content_hash,
)
from mate.api.modules.finder import module_namespace
from mate.api.modules.installer import ModuleInstallError
from mate.api.modules.installs import module_owned_by_other, record_install
from mate.api.modules.runtimes import runtime_for
from mate.api.storage.module_archive import archive_module_sync
from mate.sdk.errors import ModuleManifestError
from mate.sdk.guide import require_guide
from mate.sdk.manifest import Manifest

if TYPE_CHECKING:
    from mate.api.modules.loader import ModuleLoader

log = structlog.get_logger(__name__)


JOB_TYPE_UPLOAD = "module.install.upload"

# Under `uploaded_modules/`; the leading dot keeps them out of discovery and the GC.
STAGING_DIR = ".staging"
PREV_DIR = ".prev"
HISTORY_DIR = ".history"
_IMPORT_CHECK_TIMEOUT_S = 300


async def _record_owner(user_id: str, module_id: str, source: str) -> None:
    """Mark *module_id* as installed for the user who ran the install job, so
    it shows up in their (per-user) module list and only they can uninstall it.
    """
    sm = get_sessionmaker()
    async with sm() as session:
        await record_install(session, user_id, module_id, source)
        await session.commit()


def register_module_install_handlers(runtime: JobRuntime, loader: ModuleLoader) -> None:
    """Wire the module-install job type onto the runtime.

    Called from `main.py` lifespan after the loader and runtime are built.
    Idempotent - re-registration of the same type would raise, so we skip
    silently if it is already registered (helpful for hot-reload tests).
    """
    if JOB_TYPE_UPLOAD in runtime._handlers:  # type: ignore[attr-defined]
        return
    runtime.register(JOB_TYPE_UPLOAD, _install_from_upload(loader))


# ---------------------------------------------------------------------------
# Handlers
# ---------------------------------------------------------------------------


def _install_from_upload(loader: ModuleLoader):
    async def handler(handle: JobHandle) -> None:
        archive_path = Path(handle.payload["archive_path"])
        original_name = handle.payload.get("original_name", archive_path.name)
        try:
            await handle.progress(5, 100, stage="extracting", message=f"Extracting {original_name}")
            staging = Path(tempfile.mkdtemp(prefix="ff-install-"))
            try:
                await asyncio.to_thread(_extract_archive, archive_path, staging)
                await handle.progress(35, 100, stage="validating", message="Validating manifest")
                # Read the manifest up front so we can serialise the whole
                # stage->load->record section behind this id's lock (two uploads
                # of the same new id would otherwise race the folder/venv/mount).
                inner, manifest = _read_staged_manifest(staging)
                lock = await loader.id_lock(manifest.id)
                async with lock:
                    try:
                        folder, reused = await _stage_validated_upload(
                            loader, handle.user_id, inner, manifest
                        )
                        await handle.progress(
                            60, 100, stage="installing", message="Resolving dependencies"
                        )
                        # `reused` means the identical module is already loaded (and
                        # already archived) under this id by another owner - skip the
                        # disruptive reload + re-archive and only grant ownership.
                        if not reused:
                            await install_staged(loader, manifest, handle.user_id)
                        await _record_owner(handle.user_id, manifest.id, "upload")
                        if not reused:
                            # S3 mode: archive the source so a fresh VM can
                            # re-materialise it (best-effort + local-mode no-op).
                            await asyncio.to_thread(archive_module_sync, folder, manifest.id)
                    finally:
                        # What a failed install left staged (a success moved it out).
                        shutil.rmtree(_staged(loader, manifest.id), ignore_errors=True)
                await handle.progress(100, 100, stage="ready", message="Module installed")
                handle.payload["module_id"] = manifest.id
                await handle.bus.publish(
                    "module.installed",
                    {"id": manifest.id, "source": "upload", "user_id": handle.user_id},
                )
            finally:
                shutil.rmtree(staging, ignore_errors=True)
        finally:
            # Always clean up the upload temp file regardless of outcome.
            archive_path.unlink(missing_ok=True)

    return handler


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _extract_archive(archive_path: Path, dest: Path) -> None:
    suffix = "".join(archive_path.suffixes[-2:]).lower()
    if archive_path.suffix.lower() == ".zip":
        with zipfile.ZipFile(archive_path) as zf:
            _safe_extract(zf, dest)
        return
    if suffix in (".tar.gz", ".tgz") or archive_path.suffix.lower() in (".tar",):
        mode = "r:gz" if suffix in (".tar.gz", ".tgz") else "r"
        with tarfile.open(archive_path, mode=mode) as tf:
            _safe_extract_tar(tf, dest)
        return
    raise ValueError(
        f"Unsupported archive format {archive_path.name!r} - accept .zip, .tar, .tar.gz, .tgz."
    )


def _safe_extract(zf: zipfile.ZipFile, dest: Path) -> None:
    dest_resolved = dest.resolve()
    for name in zf.namelist():
        target = (dest / name).resolve()
        try:
            target.relative_to(dest_resolved)
        except ValueError as exc:
            raise ValueError(f"Refusing zip path traversal: {name!r}") from exc
    zf.extractall(dest)


def _safe_extract_tar(tf: tarfile.TarFile, dest: Path) -> None:
    dest_resolved = dest.resolve()
    for member in tf.getmembers():
        target = (dest / member.name).resolve()
        try:
            target.relative_to(dest_resolved)
        except ValueError as exc:
            raise ValueError(f"Refusing tar path traversal: {member.name!r}") from exc
    # `filter="data"` strips device files, absolute paths, and the like -
    # available since Python 3.12, which is our minimum.
    tf.extractall(dest, filter="data")


def _read_staged_manifest(staging: Path) -> tuple[Path, Manifest]:
    """Locate + parse the manifest in a staged extraction *without* moving it,
    and require the module's ``HOW_TO_USE.md`` guide next to it.

    Many archives wrap their content in a single top-level folder (e.g.
    GitHub's tarball auto-names with a SHA). We unwrap one level if there's a
    single child directory containing the manifest.
    """
    inner = _resolve_archive_root(staging)
    manifest_path = inner / "manifest.yaml"
    if not manifest_path.exists():
        raise ModuleManifestError(
            f"Archive is missing manifest.yaml at the top level (looked in {inner})."
        )
    manifest = Manifest.load_yaml(manifest_path)
    # A new module must ship its how-to-use guide. This is the one hard gate:
    # nothing has been written to disk yet, and modules already installed (which
    # predate the rule) keep loading with only a warning (`modules.guides`).
    require_guide(inner, manifest.name)
    return inner, manifest


async def _stage_validated_upload(
    loader: ModuleLoader, user_id: str, inner: Path, manifest: Manifest
) -> tuple[Path, bool]:
    """Validate a staged upload and decide reuse vs (re)stage.

    Returns ``(folder, reused)``: the module's folder under the uploads root, and
    whether the byte-identical module is already loaded under this id by another
    owner - the caller then shares it (skips ``install_staged`` + archiving) and only
    records this user's ownership row. Otherwise the upload now sits in
    ``.staging/<id>`` for ``install_staged``. This is content-addressed sharing:
    module code is shared in-process under a single id, so two users can co-own the
    *same* code, but two *different* code bodies can't coexist under one id.

    Rejections (before mutating disk):
    - an id that collides with a built-in default (uploads must never shadow repo
      code);
    - an id owned by another user whose on-disk content *differs* from this upload
      (or can't be verified) - the uploader must rename. Once an id is co-owned,
      this also blocks the original owner from changing its code out from under the
      co-owner (they must have the other owner(s) uninstall first).

    Re-uploading an id the same user solely owns replaces it (a new version, staged
    with rollback). Bundled defaults are never upgraded this way.
    """
    if manifest.id in loader.default_module_ids:
        raise ModuleManifestError(
            f"Module id {manifest.id!r} is a built-in default module and cannot be "
            "overwritten by an upload. Choose a different id."
        )
    target = loader.uploaded_modules_dir / manifest.id
    # Hash the *pristine* staging tree, before any move/install synthesises a
    # pyproject.toml into it - the persisted sidecar is pristine too, so every
    # comparison is pristine-vs-pristine.
    staged_hash = await asyncio.to_thread(module_content_hash, inner)

    sm = get_sessionmaker()
    async with sm() as session:
        owned_by_other = await module_owned_by_other(session, user_id, manifest.id)

    if owned_by_other:
        existing_hash = read_content_hash(target)
        if existing_hash is None or existing_hash != staged_hash:
            raise ModuleManifestError(
                f"Module id {manifest.id!r} is already used by a different module owned by "
                "another user. Shared module code can't diverge under one id - rename yours "
                "(change `id` in manifest.yaml), or have the other owner(s) uninstall it first."
            )
        # Verified-identical content owned by someone else.
        if manifest.id in loader.loaded:
            return target, True  # already loaded - just share it (caller records owner)
        # Identical but not currently loaded (failed import, or missing after an
        # S3 restore): materialise this user's copy to repair the shared install.
        _restage(inner, _staged(loader, manifest.id), staged_hash)
        return target, False

    # Not owned by another user: a brand-new id, or this user uploading a new
    # version of their own module. Stage it; `install_staged` swaps it in.
    _restage(inner, _staged(loader, manifest.id), staged_hash)
    return target, False


def _restage(inner: Path, target: Path, content_hash: str) -> None:
    """Move a validated staged module to *target*, replacing any prior copy, and
    persist its pristine content hash alongside for later comparisons."""
    if target.exists():
        shutil.rmtree(target, ignore_errors=True)
    target.parent.mkdir(parents=True, exist_ok=True)
    shutil.move(str(inner), str(target))
    write_content_hash(target, content_hash)


# ---------------------------------------------------------------------------
# Staged install, rollback, version history
# ---------------------------------------------------------------------------


def _staged(loader: ModuleLoader, module_id: str) -> Path:
    return loader.uploaded_modules_dir / STAGING_DIR / module_id


def _previous(loader: ModuleLoader, module_id: str) -> Path:
    return loader.uploaded_modules_dir / PREV_DIR / module_id


def _history_file(uploaded_modules_dir: Path, module_id: str) -> Path:
    return uploaded_modules_dir / HISTORY_DIR / f"{module_id}.json"


class DependentConflictError(ModuleInstallError):
    """A ``requirements.modules`` specifier between the version to install and a
    loaded module excludes the other side's version."""


def _check_dependents(loader: ModuleLoader, manifest: Manifest) -> None:
    """Refuse *manifest*'s version when a loaded module's ``requirements.modules``
    specifier on this id excludes it, or when its own specifier excludes a loaded
    module's version - either would be dropped at the next boot (`topo_sort`)."""
    broken = sorted(
        f"{dep.id} needs {req}"
        for dep in loader.loaded.values()
        if dep.id != manifest.id
        for req in dep.manifest.requirements.module_requirements
        if req.id == manifest.id and not req.allows(manifest.version)
    )
    if broken:
        raise DependentConflictError(
            f"Module {manifest.id!r} {manifest.version} would break the modules that "
            f"depend on it: {', '.join(broken)}. Update them first."
        )
    unmet = [
        f"{req} (loaded: {loader.loaded[req.id].manifest.version})"
        for req in manifest.requirements.module_requirements
        if req.id in loader.loaded and not req.allows(loader.loaded[req.id].manifest.version)
    ]
    if unmet:
        raise DependentConflictError(
            f"Module {manifest.id!r} {manifest.version} needs {', '.join(unmet)}. "
            "Install a matching version of it first."
        )


async def install_staged(loader: ModuleLoader, manifest: Manifest, user_id: str) -> Path:
    """Install the module waiting in ``.staging/<id>`` and return its folder.

    Refuses a version a loaded dependent's specifier excludes, or whose own
    specifier excludes a loaded module's version (`DependentConflictError`), then
    builds its toolchain and import-checks ``module.py`` there, so a broken
    version fails before it replaces anything. Then swaps it in - the live folder
    moves aside to ``.staging/<id>.live`` - and loads it. A load that fails moves the
    new folder back to ``.staging/<id>``, restores and reloads the live one, and raises
    with what happened; ``.prev/<id>`` is untouched. On success the replaced folder
    becomes ``.prev/<id>`` (unless it is the same code, which keeps the older
    ``.prev``) and the install is appended to ``.history/<id>.json``. The caller holds
    ``loader.id_lock`` and disposes of whatever is left in ``.staging/<id>``.
    """
    staged, prev = _staged(loader, manifest.id), _previous(loader, manifest.id)
    target = loader.uploaded_modules_dir / manifest.id
    live = staged.with_name(f"{manifest.id}.live")
    _check_dependents(loader, manifest)
    site = await runtime_for(manifest).materialize(staged, manifest)
    await _import_check(staged, manifest, site)
    replaced = target.exists()
    current_hash = read_content_hash(target) if replaced else None
    same_code = current_hash is not None and current_hash == read_content_hash(staged)
    if replaced:
        shutil.rmtree(live, ignore_errors=True)
        shutil.move(target, live)
    shutil.move(staged, target)
    try:
        await loader.load_one(target, manifest)
    except Exception as exc:
        shutil.move(target, staged)
        outcome = "nothing to restore"
        if replaced:
            shutil.move(live, target)
            try:
                restored = Manifest.load_yaml(target / "manifest.yaml", lenient=True)
                await loader.load_one(target, restored)
                outcome = f"restored {restored.version}"
            except Exception:
                log.exception("modules.install.restore_failed", module_id=manifest.id)
                outcome = "the previous version failed to load too"
        raise ModuleInstallError(
            f"Module {manifest.id!r} {manifest.version} failed to load ({exc}); {outcome}."
        ) from exc
    if same_code:
        # A re-upload of the same code: `.prev` must stay a different version.
        shutil.rmtree(live, ignore_errors=True)
    elif replaced:
        shutil.rmtree(prev, ignore_errors=True)
        prev.parent.mkdir(parents=True, exist_ok=True)
        shutil.move(live, prev)
    _append_history(loader.uploaded_modules_dir, manifest, user_id)
    return target


async def rollback_module(loader: ModuleLoader, module_id: str, user_id: str) -> Manifest:
    """Reinstall ``.prev/<id>`` through ``install_staged`` and return its manifest.

    The version it replaces becomes ``.prev/<id>``, so rolling back again goes
    forward. Works when the current version is not loaded (it failed to load at
    boot). Any failure leaves the current version as it was and ``.prev/<id>`` where
    it was. LookupError when there is no previous version, `DependentConflictError`
    when a loaded dependent's specifier excludes it or its own excludes a loaded module.
    """
    staged, prev = _staged(loader, module_id), _previous(loader, module_id)
    async with await loader.id_lock(module_id):
        manifest = previous_manifest(loader.uploaded_modules_dir, module_id)
        if manifest is None:
            raise LookupError(f"Module {module_id!r} has no previous version to roll back to.")
        shutil.rmtree(staged, ignore_errors=True)
        staged.parent.mkdir(parents=True, exist_ok=True)
        shutil.move(prev, staged)
        try:
            folder = await install_staged(loader, manifest, user_id)
        except BaseException:
            if staged.exists() and not prev.exists():
                shutil.move(staged, prev)
            raise
    await asyncio.to_thread(archive_module_sync, folder, module_id)
    return manifest


def previous_manifest(uploaded_modules_dir: Path, module_id: str) -> Manifest | None:
    """The manifest of the version kept in ``.prev/<id>``, if any (and valid). Read
    leniently: it ran here before, maybe from before versions had to be SemVer."""
    with contextlib.suppress(ModuleManifestError, OSError):
        return Manifest.load_yaml(
            uploaded_modules_dir / PREV_DIR / module_id / "manifest.yaml", lenient=True
        )
    return None


def read_history(uploaded_modules_dir: Path, module_id: str) -> list[dict[str, Any]]:
    """``.history/<id>.json``: ``[{version, content_hash, installed_at, user_id}]``,
    oldest first; ``[]`` when absent or unreadable."""
    try:
        data = json.loads(_history_file(uploaded_modules_dir, module_id).read_text())
    except (OSError, ValueError):
        return []
    return cast(list[dict[str, Any]], data) if isinstance(data, list) else []


def forget_versions(uploaded_modules_dir: Path, module_id: str) -> None:
    """Drop a module's ``.prev`` folder and history (its last owner uninstalled it)."""
    shutil.rmtree(uploaded_modules_dir / PREV_DIR / module_id, ignore_errors=True)
    _history_file(uploaded_modules_dir, module_id).unlink(missing_ok=True)


def _append_history(uploaded_modules_dir: Path, manifest: Manifest, user_id: str) -> None:
    entries = read_history(uploaded_modules_dir, manifest.id)
    entries.append(
        {
            "version": manifest.version,
            "content_hash": read_content_hash(uploaded_modules_dir / manifest.id),
            "installed_at": datetime.now(UTC).isoformat(),
            "user_id": user_id,
        }
    )
    path = _history_file(uploaded_modules_dir, manifest.id)
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(".tmp")
    tmp.write_text(json.dumps(entries, indent=2))
    tmp.replace(path)


# Imports the staged module.py the way `ModuleLoader._import_module_class` does.
_IMPORT_CHECK = """\
import importlib.util, inspect, sys
folder, module_id, namespace, site = sys.argv[1:]
if site:
    sys.path.insert(0, site)
from mate.sdk import Module
spec = importlib.util.spec_from_file_location(
    namespace, folder + "/module.py", submodule_search_locations=[folder]
)
module = importlib.util.module_from_spec(spec)
sys.modules[namespace] = module
spec.loader.exec_module(module)
ids = [
    v.id for v in vars(module).values()
    if inspect.isclass(v) and issubclass(v, Module) and v is not Module
    and v.__module__ == namespace
]
if ids[:1] != [module_id]:
    sys.exit(f"module.py must define a Module subclass with id {module_id!r} (found {ids}).")
"""


async def _import_check(folder: Path, manifest: Manifest, site: Path | None) -> None:
    """Import *folder*'s ``module.py`` in a throwaway interpreter - the platform's
    for an in_process module (with its venv first on the path), its own venv's for
    a subprocess one - so the live process never imports a version it may reject.
    Non-Python runtimes have nothing to import."""
    if manifest.runtime.kind != "python":
        return
    if manifest.dependencies.python.isolation == "subprocess":
        python, site_arg = folder / ".venv" / "bin" / "python3", ""
        if not python.exists():
            raise ModuleInstallError(
                f"Module {manifest.id!r}: its dependencies could not be installed."
            )
    else:
        python, site_arg = Path(sys.executable), str(site or "")
    proc = await asyncio.create_subprocess_exec(
        str(python),
        "-c",
        _IMPORT_CHECK,
        str(folder),
        manifest.id,
        module_namespace(manifest.id),
        site_arg,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
    )
    try:
        out, _ = await asyncio.wait_for(proc.communicate(), _IMPORT_CHECK_TIMEOUT_S)
    except TimeoutError:
        proc.kill()
        await proc.wait()
        raise ModuleInstallError(
            f"Module {manifest.id!r}: importing module.py took over {_IMPORT_CHECK_TIMEOUT_S}s."
        ) from None
    if proc.returncode != 0:
        # The exception line only: the traceback above it names server paths.
        lines = out.decode("utf-8", errors="replace").strip().splitlines()
        raise ModuleInstallError(
            f"Module {manifest.id!r}: module.py does not import: {lines[-1] if lines else '?'}"
        )


def _resolve_archive_root(staging: Path) -> Path:
    """If the archive contained one wrapper directory, descend into it."""
    # `__MACOSX` is metadata cruft macOS adds to zips alongside the real folder.
    entries = [p for p in staging.iterdir() if not p.name.startswith(".") and p.name != "__MACOSX"]
    if len(entries) == 1 and entries[0].is_dir() and not (staging / "manifest.yaml").exists():
        return entries[0]
    return staging
