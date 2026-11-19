"""On-disk layout of dataset revisions - the ONLY writer under ``data/datasets/``.

::

    data/datasets/{dataset_id}/r{n}/
    ├── data/
    │   ├── events.parquet
    │   └── cases.parquet
    └── module_results/{module_id}/{key}.{json,parquet,bin}

A revision is immutable once written: :func:`snapshot_into_revision` builds it in
a sibling tmp dir and swaps it in with ``os.replace``; only :func:`purge_revision`
removes it. Student actions never resolve to this tree - :func:`copy_into_user`
reads from it and writes under ``data/users/{uid}/`` only.

Revisions sit outside ``users/*``, so cache eviction and user deletion never
touch them, and ``local_cache_bytes`` does not count them. In S3 mode they mirror
like any other dir under ``data_dir`` (``storage_sync.rel_key``).
"""

from __future__ import annotations

import hashlib
import os
import shutil
from collections.abc import Iterable
from pathlib import Path

import structlog

from mate.api.config import get_settings
from mate.api.ingest.storage import LogPaths, log_paths
from mate.api.modules.cache import ResultCache
from mate.api.path_segments import safe_segment
from mate.api.storage import sync as storage_sync
from mate.api.uuid7 import uuid7_str

log = structlog.get_logger(__name__)

RESULT_SUFFIXES = (".json", ".parquet", ".bin")


def datasets_root() -> Path:
    return get_settings().datasets_dir


def revision_dir(dataset_id: str, number: int) -> Path:
    root = datasets_root()
    p = root / safe_segment(dataset_id, "dataset id") / f"r{int(number)}"
    if not p.resolve().is_relative_to(root.resolve()):  # pragma: no cover - safe_segment
        raise ValueError(f"Revision path escapes the datasets root: {p}")
    return p


def revision_data(rev_dir: Path) -> Path:
    return rev_dir / "data"


def revision_results(rev_dir: Path, module_id: str) -> Path:
    return rev_dir / "module_results" / safe_segment(module_id, "module id")


def result_files(dir_: Path) -> list[Path]:
    """Canonical result files of one module dir.

    Regular files directly in ``dir_`` - not ``__*`` (the platform's own entries,
    e.g. stored AI guidance) and no subdirectory (``_v_*`` dashboard-filter
    variants among them).
    """
    if not dir_.is_dir():
        return []
    return sorted(
        p
        for p in dir_.iterdir()
        if p.is_file() and not p.name.startswith("__") and p.suffix in RESULT_SUFFIXES
    )


def _sha256(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def snapshot_into_revision(
    src: LogPaths, src_results: dict[str, Path], dataset_id: str, number: int
) -> tuple[Path, int, str]:
    """Write revision ``r{number}`` from a log's Parquet + chosen module results.

    Returns ``(rev_dir, bytes of events+cases, sha256 of events.parquet)``. Copies
    only ``events.parquet`` and ``cases.parquet`` - never ``meta.json``, the
    original upload, ``ocel/`` or any module sidecar dir in the log root.
    """
    final = revision_dir(dataset_id, number)
    if final.exists():
        raise FileExistsError(f"Revision already exists: {final}")
    final.parent.mkdir(parents=True, exist_ok=True)
    tmp = final.parent / f"r{int(number)}.tmp-{uuid7_str()}"
    try:
        data = revision_data(tmp)
        data.mkdir(parents=True)
        shutil.copy2(src.events, data / "events.parquet")
        shutil.copy2(src.cases, data / "cases.parquet")
        nbytes = (data / "events.parquet").stat().st_size + (data / "cases.parquet").stat().st_size
        sha = _sha256(data / "events.parquet")
        for module_id, dir_ in src_results.items():
            dst = revision_results(tmp, module_id)
            dst.mkdir(parents=True, exist_ok=True)
            for f in result_files(dir_):
                shutil.copyfile(f, dst / f.name)
        os.replace(tmp, final)
    except BaseException:
        shutil.rmtree(tmp, ignore_errors=True)
        raise
    storage_sync.persist_dir_sync(revision_data(final))
    for module_id in src_results:
        storage_sync.persist_dir_sync(revision_results(final, module_id))
    return final, nbytes, sha


def hydrate_revision(rev_dir: Path, module_ids: Iterable[str]) -> None:
    """S3 mode: pull a revision's dirs down before reading them (no-op locally)."""
    storage_sync.hydrate_dir_sync(revision_data(rev_dir))
    for module_id in module_ids:
        storage_sync.hydrate_dir_sync(revision_results(rev_dir, module_id))


def copy_into_user(rev_dir: Path, user_id: str, new_log_id: str, module_ids: Iterable[str]) -> None:
    """Copy a revision into a fresh log dir + module-results dir of ``user_id``.

    Parquet first (``copy2``), then results with ``copyfile`` so every result gets
    a fresh mtime *after* the Parquet: ``result mtime > events.parquet mtime`` -
    the freshness invariant modules check (e.g. complexity's ``_cache_is_fresh``)
    - holds by construction, whatever the source mtimes were (S3 hydrate does not
    keep them).
    """
    dst = log_paths(new_log_id, user_id)
    dst.ensure()
    data = revision_data(rev_dir)
    shutil.copy2(data / "events.parquet", dst.events)
    shutil.copy2(data / "cases.parquet", dst.cases)
    for module_id in module_ids:
        target = ResultCache(new_log_id, module_id, user_id).dir
        for f in result_files(revision_results(rev_dir, module_id)):
            shutil.copyfile(f, target / f.name)


def remove_user_copy(user_id: str, log_id: str) -> None:
    """Undo a half-done :func:`copy_into_user` (best effort)."""
    shutil.rmtree(log_paths(log_id, user_id).root, ignore_errors=True)
    results = get_settings().module_results_dir_for(user_id) / safe_segment(log_id, "log id")
    shutil.rmtree(results, ignore_errors=True)


def purge_revision(dataset_id: str, number: int, *, strict: bool = False) -> None:
    """Delete a revision tree, locally and in the bucket.

    ``strict`` (the takedown purge) raises when anything is left behind, so the
    caller never records a purge that did not happen. Cleanup after a failed
    publish passes ``strict=False``: it must not mask the original error.
    """
    rev = revision_dir(dataset_id, number)
    try:
        if rev.exists():
            shutil.rmtree(rev)
        storage_sync.delete_dir_remote_sync(rev)
    except Exception as exc:
        log.warning("hub.purge_failed", dataset_id=dataset_id, revision=number, error=str(exc))
        if strict:
            raise
    if strict and rev.exists():
        raise OSError(f"Revision still on disk after purge: {rev}")
