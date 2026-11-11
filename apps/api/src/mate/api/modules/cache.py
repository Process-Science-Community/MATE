"""Per-(user_id, log_id, module_id) result cache (§5.5).

Stores under ``data/users/{user_id}/module_results/{log_id}/{module_id}/{key}.{ext}``.
We serialise dicts/lists/scalars to JSON; pandas DataFrames to Parquet; bytes
verbatim. Module authors decide what to cache; the SDK only mediates the
filesystem layout.

Two kinds of sub-namespace live inside a module's directory: ``_v_<hash>`` - a
dashboard's ephemeral filter view, wiped with the module's result - and
``_r_<key>`` - a *run variant* (``mate.api.modules.variants``), a separately
requested run of the module's precompute with its own spec and ``__meta.json``,
which outlives reruns of the default result.
"""

from __future__ import annotations

import asyncio
import contextlib
import hashlib
import json
import os
import re
import shutil
import threading
import time
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, cast

from mate.api.config import get_settings
from mate.api.modules.log_config import normalised
from mate.api.path_segments import safe_segment
from mate.api.storage import eviction
from mate.api.storage import sync as storage_sync
from mate.sdk.errors import ReadOnlyContextError

RUN_PREFIX = storage_sync.RUN_VARIANT_PREFIX
# A run variant's state file (`variants.VariantMeta`), a reserved `__` key.
META_KEY = "__meta"
_RUN_KEY = re.compile(r"[0-9a-f]{16}")
_TOUCH_EVERY_S = 60.0
# Serialises read-modify-writes of `__meta.json` (a read touching `last_used_at` vs
# the coordinator settling the run) so neither overwrites the other's change.
_meta_lock = threading.Lock()
_touched: dict[str, float] = {}
# One per module dir (its filter views and run variants included), never pruned: a
# write's persist landing between `clear`'s S3 delete and its local wipe would put the
# old outputs back in the mirror, so the two never interleave.
_dir_locks: dict[str, threading.Lock] = {}


def run_variant_key(spec: dict[str, Any]) -> str:
    """The variant key of a canonical run spec: 16 hex chars, stable across processes.
    Param numbers hash like the run fingerprint's: ``1`` and ``1.0`` are one variant."""
    spec = {**spec, "params": normalised(spec.get("params") or {})}
    canonical = json.dumps(spec, sort_keys=True, separators=(",", ":"), default=str)
    return hashlib.blake2b(canonical.encode(), digest_size=8).hexdigest()


def is_run_variant(key: str) -> bool:
    """Whether *key* is a well-formed variant key - it becomes a path segment."""
    return _RUN_KEY.fullmatch(key) is not None


class ResultCache:
    def __init__(
        self,
        log_id: str,
        module_id: str,
        user_id: str,
        root: Path | None = None,
        variant: str | None = None,
        *,
        run_variant: str | None = None,
        read_only: bool = False,
    ) -> None:
        base = root or get_settings().module_results_dir_for(user_id)
        self.dir = base / safe_segment(log_id, "log id") / safe_segment(module_id, "module id")
        # A shared-dashboard viewer's cache (`ctx.read_only`): it still caches computed
        # results here (read routes compute-and-cache on a miss), but never touches the
        # owner's reserved `__*` entries - uploads and other state kept across re-runs.
        self.read_only = read_only
        self._dir_lock = _dir_locks.setdefault(str(self.dir), threading.Lock())
        # The module dir is the unit the S3 reaper evicts; a run variant leases it so a
        # read of the variant pins the whole unit (its own dir is hydrated separately).
        self._unit = self.dir
        self.run_variant = run_variant
        # ``variant`` isolates an ephemeral dataset view (a dashboard's
        # column/time filter) into its own sub-namespace. Without it a
        # filtered request would collide with - and serve - the unfiltered
        # cache, since the freshness check only watches the (unchanged)
        # parquet mtime. ``None`` keeps the canonical, committed-filter cache
        # at the module root, and ``clear()``'s rmtree still wipes variants.
        if variant:
            self.dir = self.dir / f"_v_{variant}"
            self._unit = self.dir
        elif run_variant is not None:
            if not is_run_variant(run_variant):
                raise ValueError(f"Invalid run variant {run_variant!r}.")
            self.dir = self.dir / f"{RUN_PREFIX}{run_variant}"
        # A run variant's dir appears with its first write, so probing for one (an
        # input ref, a dataset read) never leaves an empty variant behind.
        if run_variant is None:
            self.dir.mkdir(parents=True, exist_ok=True)

    def _check_writable(self, key: str) -> None:
        if self.read_only and key.startswith("__"):
            raise ReadOnlyContextError(
                f"Cache entry {key!r} belongs to the log's owner; this view is read-only."
            )

    def _candidate(self, key: str) -> Path:
        if "/" in key or ".." in key:
            raise ValueError(f"Invalid cache key {key!r} - must be a flat name.")
        return self.dir / key

    async def get(self, key: str) -> Any:
        return await asyncio.to_thread(self._get_sync, key)

    def _get_sync(self, key: str) -> Any:
        # Lease the dir so the S3 cache reaper can't evict it mid-read; in S3
        # mode pull this module's outputs down if the cache is cold (once per dir
        # per process). No-op in local mode / warm cache.
        with eviction.LeaseDir(self._unit):
            storage_sync.hydrate_dir_sync(self.dir)
            path = self._find(key)
            if path is None:
                return None
            if not key.startswith("__"):
                self.touch()
            if path.suffix == ".json":
                return json.loads(path.read_text())
            if path.suffix == ".parquet":
                import pandas as pd

                return pd.read_parquet(path)
            return path.read_bytes()

    def _find(self, key: str) -> Path | None:
        for ext in ("json", "parquet", "bin"):
            path = self._candidate(f"{key}.{ext}")
            if path.exists():
                return path
        return None

    def path(self, key: str) -> Path | None:
        """The local file holding *key* (hydrated from S3 first, like ``get``), for a
        worker process that reads it off the shared filesystem; None when unset."""
        with eviction.LeaseDir(self._unit):
            storage_sync.hydrate_dir_sync(self.dir)
            return self._find(key)

    async def set(self, key: str, value: Any) -> None:
        self._check_writable(key)
        await asyncio.to_thread(self._set_and_persist, key, value)

    def _set_and_persist(self, key: str, value: Any) -> None:
        # One lease spans write + upload so the reaper can't reclaim the dir
        # between the local write and its S3 persist.
        with eviction.LeaseDir(self._unit), self._dir_lock:
            self._set_sync(key, value)
            storage_sync.persist_dir_sync(self.dir, skip=_unsynced)

    def _set_sync(self, key: str, value: Any) -> None:
        self.dir.mkdir(parents=True, exist_ok=True)
        # Pick the encoding by type.
        try:
            import pandas as pd
        except ModuleNotFoundError:  # pragma: no cover - pandas is a platform dep
            pd = None  # type: ignore[assignment]

        if pd is not None and hasattr(pd, "DataFrame") and isinstance(value, pd.DataFrame):
            path = self._candidate(f"{key}.parquet")
            value.to_parquet(path, engine="pyarrow", compression="zstd", index=False)
            return
        if isinstance(value, (bytes, bytearray)):
            path = self._candidate(f"{key}.bin")
            path.write_bytes(bytes(value))
            return
        path = self._candidate(f"{key}.json")
        path.write_text(json.dumps(value, default=str))

    async def exists(self, key: str) -> bool:
        return any(self._candidate(f"{key}.{ext}").exists() for ext in ("json", "parquet", "bin"))

    async def delete(self, key: str) -> None:
        self._check_writable(key)
        for ext in ("json", "parquet", "bin"):
            path = self._candidate(f"{key}.{ext}")
            if path.exists():
                path.unlink()

    def clear(self, *, keep_reserved: bool = False) -> None:
        """Drop every cached result in this namespace, filter variants included.

        The module's run variants (``_r_*``) always stay: they are separate runs with
        their own freshness, removed only through their own namespace.
        ``keep_reserved`` also leaves the platform's own ``__*`` entries in place - the
        stored AI guidance (``__ai_guidance``) shares this directory but is not a
        module result, and it re-validates itself against a hash of the module's
        output, so a recompute never needs to throw it away.
        """
        # Drop the S3 copy first (no-op in local mode): wiped locally first, a read in
        # between would re-hydrate the old outputs - and the next write re-upload them.
        # What is kept stays in the mirror - deleted around, so a run variant this host
        # has no local copy of survives too.
        if self.read_only:
            raise ReadOnlyContextError("A read-only view cannot clear the log owner's results.")
        keep = _kept if keep_reserved else storage_sync.in_run_variant
        with self._dir_lock:  # a write's persist lands before the S3 delete or after the wipe
            storage_sync.delete_dir_remote_sync(self.dir, keep=keep)
            with _meta_lock:  # a run variant's `touch` must not write into a half-cleared dir
                for path in list(self.dir.iterdir()) if self.dir.exists() else []:
                    if path.is_dir() and path.name.startswith(RUN_PREFIX):
                        continue
                    if keep_reserved and path.is_file() and path.name.startswith("__"):
                        continue
                    if path.is_dir():
                        shutil.rmtree(path)
                    else:
                        path.unlink()
                self.dir.mkdir(parents=True, exist_ok=True)

    def remove(self, *, remote: bool = True) -> None:
        """Delete this namespace for good, locally and (unless not *remote*) in the S3
        mirror."""
        with eviction.LeaseDir(self._unit):
            with _meta_lock:
                shutil.rmtree(self.dir, ignore_errors=True)
            if remote:
                storage_sync.delete_dir_remote_sync(self.dir)
            else:
                storage_sync.forget(self.dir)  # the mirror kept it: the next read hydrates

    # -- run variants ------------------------------------------------------------

    def read_meta(self) -> dict[str, Any] | None:
        """A run variant's ``__meta.json`` - in S3 mode fetched alone when this host has
        no copy, never with the variant's outputs; None when unset."""
        path = self.dir / f"{META_KEY}.json"
        with eviction.LeaseDir(self._unit):
            if not path.exists():
                storage_sync.hydrate_missing_sync(self.dir, lambda rel: rel == path.name)
            try:
                meta = json.loads(path.read_text())
            except (OSError, ValueError):
                return None
        return cast(dict[str, Any], meta) if isinstance(meta, dict) else None

    def write_meta(self, meta: dict[str, Any]) -> None:
        with _meta_lock, eviction.LeaseDir(self._unit):
            self.dir.mkdir(parents=True, exist_ok=True)
            _write_atomic(self.dir / f"{META_KEY}.json", json.dumps(meta, default=str))
            storage_sync.persist_dir_sync(self.dir, skip=_unsynced)

    def touch(self) -> None:
        """Stamp a run variant's ``last_used_at`` (the GC's LRU key) - at most once a
        minute per variant, and locally only."""
        if self.run_variant is None:
            return
        now = time.monotonic()
        marker = str(self.dir)
        if now - _touched.get(marker, -_TOUCH_EVERY_S) < _TOUCH_EVERY_S:
            return
        _touched[marker] = now
        path = self.dir / f"{META_KEY}.json"
        with _meta_lock:
            try:
                meta = json.loads(path.read_text())
            except (OSError, ValueError):
                return
            # shortcut: not persisted to S3 (a write per read would re-upload the dir); a
            # re-hydrated variant falls back to its last persisted stamp.
            meta["last_used_at"] = datetime.now(UTC).replace(tzinfo=None).isoformat()
            # Plain opens: never recreates a dir an eviction removed since the read.
            with contextlib.suppress(FileNotFoundError):
                _write_atomic(path, json.dumps(meta, default=str))


def hydrate_run_metas(module_dir: Path) -> None:
    """S3 mode: fetch the ``__meta.json`` of every run variant in a module dir's mirror
    that this host has no copy of - never their outputs - so a listing of the local
    ``_r_*`` dirs sees them all. Blocking."""

    def _is_run_meta(rel: str) -> bool:
        head, _, name = rel.partition("/")
        key = head[len(RUN_PREFIX) :]
        return name == f"{META_KEY}.json" and head.startswith(RUN_PREFIX) and is_run_variant(key)

    storage_sync.hydrate_missing_sync(module_dir, _is_run_meta)


def _write_atomic(path: Path, text: str) -> None:
    """Write via a temp file in the same dir + rename: a concurrent reader (the GC's
    scan) sees the old or the new file, never a truncated one."""
    tmp = path.with_name(f"{path.name}.{os.getpid()}.{threading.get_ident()}.tmp")
    try:
        tmp.write_text(text)
        os.replace(tmp, path)
    finally:
        tmp.unlink(missing_ok=True)


def _unsynced(rel: str) -> bool:
    """What a dir's upload leaves out: its run variants (persisted on their own writes,
    never with the default result) and an in-progress ``_write_atomic`` temp file."""
    return storage_sync.in_run_variant(rel) or rel.endswith(".tmp")


def _kept(rel: str) -> bool:
    """The S3 objects ``clear(keep_reserved=True)`` keeps: ``__*`` files and run variants."""
    return ("/" not in rel and rel.startswith("__")) or storage_sync.in_run_variant(rel)
