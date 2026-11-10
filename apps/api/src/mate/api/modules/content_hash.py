"""Content-addressed hashing of a *pristine* module source tree.

The install pipeline uses this to decide whether two uploads of the same module
id are the *same* module - in which case the second uploader shares the already
loaded code and just gains an ownership row (`module_installs`) - or a genuine
conflict, which is rejected. See `install_jobs._stage_validated_upload`.

The hash MUST be taken on the pristine staged tree, *before* `install_module`
synthesises a `pyproject.toml` into the folder (`installer._synthesise_pyproject`)
for deps-bearing / subprocess modules. Hashing a post-install folder would make a
deps-bearing module hash differently pre- vs post-install, so two byte-identical
uploads would wrongly diverge and re-manifest the very bug this fixes. We persist
the pristine hash in a sidecar (`.content-hash`) and always compare pristine vs
pristine.
"""

from __future__ import annotations

import hashlib
import os
from pathlib import Path

from mate.api.storage.module_archive import BUILD_ARTIFACT_NAMES

# Sidecar holding a module's pristine content hash, written next to its source.
# Excluded from the hash (it must not hash itself) but deliberately NOT part of
# `BUILD_ARTIFACT_NAMES`, so it rides along in the S3 source archive and survives
# a fresh-VM re-materialise.
CONTENT_HASH_FILE = ".content-hash"

# Derived build artifacts + the sidecar are never part of a module's identity.
# Reuse the archive's exclusion set as the single source of truth.
_HASH_EXCLUDE = BUILD_ARTIFACT_NAMES | {CONTENT_HASH_FILE}

# Only code and the manifest (deps, config defaults) decide what a module computes.
# An allowlist, so panels, docs, OS litter and runtime data never count - cv4cdd
# keeps ~0.5 GB uploaded models under its own folder.
_COMPUTE_SUFFIXES = (".py", ".jar")
_COMPUTE_EXCLUDE = _HASH_EXCLUDE | {"tests"}


def module_content_hash(folder: Path) -> str:
    """Deterministic hash of the regular files under *folder*.

    Files are hashed in sorted POSIX-relpath order, each entry length-framed
    (`len(relpath) | relpath | kind | payload`) so distinct trees can't collide
    (e.g. `("ab", "c")` vs `("a", "bc")`). Symlinks hash their target and are not
    followed - this sidesteps the zip-vs-tar asymmetry (the tar `data` filter
    strips escaping links) and avoids reading through a broken link. File mode /
    exec bit is intentionally ignored so the same content packed as `.zip` and
    `.tar.gz` matches; empty directories are ignored. Build artifacts (`.venv`,
    `.dist`, ...) and the `.content-hash` sidecar are skipped.
    """
    folder = Path(folder)
    return _hash_files(folder, _iter_hashable_files(folder))


def module_compute_hash(folder: Path) -> str:
    """Hash of the part of *folder* that can change what the module computes.

    Same walk and framing as :func:`module_content_hash`, restricted to `.py` and
    `.jar` files plus `manifest.yaml`, outside a `tests` dir - so editing a panel
    or the guide does not turn stored results stale. Feeds the run fingerprint
    (`modules.processing`), never install identity.
    """
    folder = Path(folder)
    files = _iter_hashable_files(folder, _COMPUTE_EXCLUDE)
    return _hash_files(
        folder, [p for p in files if p.suffix in _COMPUTE_SUFFIXES or p.name == "manifest.yaml"]
    )


def _hash_files(folder: Path, files: list[Path]) -> str:
    """Length-framed blake2b over *files* (relpaths under *folder*, in order)."""
    digest = hashlib.blake2b(digest_size=32)
    for path in files:
        rel = path.relative_to(folder).as_posix().encode("utf-8")
        digest.update(len(rel).to_bytes(4, "big"))
        digest.update(rel)
        if path.is_symlink():
            target = os.readlink(path).encode("utf-8")
            digest.update(b"L")
            digest.update(len(target).to_bytes(8, "big"))
            digest.update(target)
        else:
            try:
                payload = hashlib.blake2b(path.read_bytes(), digest_size=32).digest()
            except OSError:
                # Unreadable regular file - contribute a stable sentinel rather
                # than raise, keeping the hash deterministic.
                payload = b"\x00" * 32
            digest.update(b"F")
            digest.update(payload)
    return digest.hexdigest()


def _iter_hashable_files(folder: Path, exclude: set[str] = _HASH_EXCLUDE) -> list[Path]:
    """All non-excluded regular files/symlinks under *folder*, sorted by relpath."""
    results: list[Path] = []
    for root, dirnames, filenames in os.walk(folder):
        # Prune excluded directories in place so os.walk never descends them.
        dirnames[:] = [d for d in dirnames if d not in exclude]
        root_path = Path(root)
        for name in filenames:
            if name in exclude:
                continue
            results.append(root_path / name)
    results.sort(key=lambda p: p.relative_to(folder).as_posix())
    return results


def read_content_hash(folder: Path) -> str | None:
    """Return the persisted pristine content hash for a module, or None if the
    sidecar is missing/empty (an older install, or an unverifiable state)."""
    try:
        text = (Path(folder) / CONTENT_HASH_FILE).read_text().strip()
    except OSError:
        return None
    return text or None


def write_content_hash(folder: Path, digest: str) -> None:
    """Persist a module's pristine content hash next to its source."""
    (Path(folder) / CONTENT_HASH_FILE).write_text(digest)
