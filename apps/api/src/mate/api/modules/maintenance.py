"""Uploaded-module disk GC (S3_OFFLOAD.md Phase 2.4).

An uploaded module's ``.venv`` (100 MB-2 GB) + ``.dist`` + source live under
``data/uploaded_modules/{mid}``. The uninstall route removes the dir when the
last owner uninstalls, but a crash mid-uninstall (or a restored-but-unowned dir)
can leak it. This boot-time sweep removes any uploaded-module dir with zero
install rows, reclaiming the venv + bundle + source.

Boot-only by design: it must not race a concurrent upload (which extracts to the
dir before recording its install row). Repo-default modules live under
``modules/`` and are never touched.
"""

from __future__ import annotations

import shutil
from pathlib import Path

import structlog

from mate.api.modules.content_hash import read_content_hash
from mate.api.modules.install_jobs import (
    HISTORY_DIR,
    PREV_DIR,
    STAGING_DIR,
    forget_versions,
    read_history,
)

log = structlog.get_logger(__name__)


def recover_install_staging(uploaded_modules_dir: Path) -> None:
    """Undo what an install or rollback (`install_staged`) that died mid-swap left in
    ``.staging/``. Boot-only, before discovery: no install runs yet.

    - ``.staging/<id>.live`` (the version being replaced) goes back to ``<id>`` when
      that is missing; when both exist it is logged and kept (the next install of the
      id clears it).
    - ``.staging/<id>`` - an upload or rollback that never finished - is removed,
      unless it is the version a rollback took out of ``.prev/<id>`` (an earlier
      install of this id, not the current one, and ``.prev/<id>`` is gone): it goes
      back there.
    """
    staging = uploaded_modules_dir / STAGING_DIR
    if not staging.is_dir():
        return
    for live in staging.glob("*.live"):
        target = uploaded_modules_dir / live.name.removesuffix(".live")
        if target.exists():
            log.warning("modules.staging.live_kept", module_id=target.name, path=str(live))
        else:
            shutil.move(live, target)
            log.warning("modules.staging.live_restored", module_id=target.name)
    for entry in staging.iterdir():
        if entry.name.endswith(".live"):
            continue
        mid, prev = entry.name, uploaded_modules_dir / PREV_DIR / entry.name
        installed = {h.get("content_hash") for h in read_history(uploaded_modules_dir, mid)}
        installed -= {None, read_content_hash(uploaded_modules_dir / mid)}
        if entry.is_dir() and not prev.exists() and read_content_hash(entry) in installed:
            prev.parent.mkdir(parents=True, exist_ok=True)
            shutil.move(entry, prev)
            log.warning("modules.staging.prev_restored", module_id=mid)
        elif entry.is_dir():
            shutil.rmtree(entry, ignore_errors=True)
        else:
            entry.unlink(missing_ok=True)


def gc_orphaned_uploaded_modules(
    uploaded_modules_dir: Path, known_install_ids: set[str]
) -> list[str]:
    """Remove uploaded-module dirs not referenced by any install row, with their
    kept previous version (``.prev/<id>``) and install history (``.history/<id>.json``)
    - also when only those are left.

    ``known_install_ids`` is the set of module ids with at least one
    ``module_installs`` row. Returns the ids removed. Safe to call when the dir
    doesn't exist yet (returns ``[]``).
    """
    if not uploaded_modules_dir.exists():
        return []
    ids = {c.name for c in uploaded_modules_dir.iterdir() if c.is_dir() and c.name[0] != "."}
    prev = uploaded_modules_dir / PREV_DIR
    ids |= {c.name for c in prev.iterdir() if c.is_dir()} if prev.is_dir() else set()
    ids |= {p.stem for p in (uploaded_modules_dir / HISTORY_DIR).glob("*.json")}
    removed = sorted(ids - known_install_ids)
    for mid in removed:
        shutil.rmtree(uploaded_modules_dir / mid, ignore_errors=True)
        forget_versions(uploaded_modules_dir, mid)
    if removed:
        log.info("modules.gc.removed_orphans", ids=removed)
    return removed
