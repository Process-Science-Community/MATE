"""The artifact workspace: an index of a user's artifacts (``Artifact`` rows).

A ``run`` row points at a module's result-cache entry - one per typed, cached output
(``datasets[]`` with ``artifact_type`` + ``cache_key``) whose entry exists when a
default run (``modules.processing``) or a run variant (``modules.variants``) settles
ready, or when a list finds one no run indexed (``discover``: an entry a module route
wrote, a run older than the index or than an uninstall). The file stays where the
producer wrote it: this is an index, never a copy. A run's row whose entry is missing
is hidden, never deleted - a rerun empties the cache (or the variant's dir) first, and
the row keeps the user's name and pin; a pinned variant's stays listed while its rerun
is under way or after it failed (``state``), so the user can retry or delete it. A run
variant's row goes once its dir is gone (a deleted or evicted variant): pruned lazily
when listed or read, and eagerly when a variant is deleted or GC'd. An ``upload`` row
owns ``data/users/{uid}/artifacts/{id}.json`` (``routes/artifacts.py``), addressed
``{log_id or "_"}/_workspace/{id}``, and is pruned once that file is gone. ``backfill``
indexes, once, what was computed before the index existed.
"""

from __future__ import annotations

import asyncio
import contextlib
import heapq
import json
import os
from collections.abc import Iterable, Mapping, Sequence
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Literal, cast

import structlog
from sqlalchemy import delete, select
from sqlalchemy.ext.asyncio import AsyncSession

from mate.api.config import get_settings
from mate.api.db.engine import get_sessionmaker
from mate.api.db.models import Artifact, EventLog, SystemSetting
from mate.api.modules.cache import META_KEY, RUN_PREFIX, ResultCache, is_run_variant
from mate.api.modules.inputs import ModuleInputs, workspace_file, workspace_path
from mate.api.modules.installs import user_module_ids
from mate.api.storage import sync as storage_sync
from mate.api.uuid7 import uuid7_str
from mate.sdk.artifacts import WORKSPACE_MODULE_ID, ArtifactRef, ArtifactType, get_artifact_type
from mate.sdk.manifest import DatasetEntry, Manifest

log = structlog.get_logger(__name__)

PREVIEW_ROWS = 200
# A list without `log_id` looks for unindexed outputs on this many of the newest logs,
# and on this many of the logs whose results changed last.
DISCOVER_LOGS = 50
# A cache entry's file suffix by its artifact type's storage.
_EXT = {"json": "json", "parquet": "parquet", "bytes": "bin"}
# A run `discover` found: (log_id, module_id, run variant or None).
_Run = tuple[str, str, str | None]
# `ready`: the file is there. `missing`: it isn't (a default rerun, an output the run
# did not write). `running` / `failed`: a run variant's rerun is under way / failed or
# was cancelled.
ArtifactState = Literal["ready", "missing", "failed", "running"]
# `system_settings` key: the one-shot `backfill` has run.
BACKFILL_KEY = "artifacts_backfill_v1"


class ArtifactTooLargeError(Exception):
    """The artifact is larger than ``ARTIFACT_UPLOAD_MAX_BYTES``."""


class NotPreviewableError(Exception):
    """A bytes artifact: there is nothing to render as JSON."""


def _utcnow() -> datetime:
    return datetime.now(UTC).replace(tzinfo=None)


def workspace_ref(log_id: str | None, artifact_id: str) -> str:
    return str(ArtifactRef(log_id or "_", WORKSPACE_MODULE_ID, artifact_id))


# -- registration ---------------------------------------------------------------


async def register_run(
    session: AsyncSession,
    *,
    user_id: str,
    log_id: str,
    manifest: Manifest,
    owned_producers: Mapping[str, Manifest],
    variant: str | None = None,
    spec: dict[str, Any] | None = None,
    producer_hash: str | None = None,
    only_new: bool = False,
) -> None:
    """Upsert a row for each typed, cached output of *manifest* that the run (default,
    or *variant*) produced for *log_id*; the rows of outputs it did not produce stay,
    hidden while missing. Lineage is what ``ctx.inputs`` bound for it: the default
    binding over *owned_producers*, a variant's *spec* input refs on top (ValueError
    for a malformed one). Keeps a row's user-set ``name`` and ``pinned``; *only_new*
    leaves existing rows alone (``discover``)."""
    # By ref: two datasets on one cache key would be one row (`check_contract` flags it).
    outputs: dict[str, tuple[DatasetEntry, ArtifactType]] = {}
    for d in manifest.datasets:
        t = get_artifact_type(d.artifact_type or "")
        if d.cache_key is None or t is None:
            continue
        ref = str(ArtifactRef(log_id, manifest.id, d.cache_key, variant))
        if ref in outputs:
            log.warning(
                "modules.artifacts.duplicate_cache_key",
                module_id=manifest.id,
                cache_key=d.cache_key,
                dataset_id=d.id,
                kept=outputs[ref][0].id,
            )
            continue
        outputs[ref] = (d, t)
    if not outputs:
        return
    explicit = {
        port: ArtifactRef.parse(str(text))
        for port, text in cast(dict[str, Any], (spec or {}).get("inputs") or {}).items()
    }
    bound = ModuleInputs.bind(manifest.inputs, owned_producers, log_id, user_id, explicit)
    lineage = [str(r) for p in bound.ports if (r := bound.ref(p)) is not None]

    def _sizes() -> dict[str, int | None]:
        cache = ResultCache(log_id, manifest.id, user_id, run_variant=variant)
        out: dict[str, int | None] = {}
        for ref, (d, _t) in outputs.items():
            path = cache.path(d.cache_key or "")
            out[ref] = path.stat().st_size if path is not None else None
        return out

    sizes = await asyncio.to_thread(_sizes)
    existing = {
        r.ref: r
        for r in (
            await session.execute(
                select(Artifact).where(Artifact.user_id == user_id, Artifact.ref.in_(list(outputs)))
            )
        ).scalars()
    }
    for ref, (d, t) in outputs.items():
        row = existing.get(ref)
        if sizes[ref] is None or (row is not None and only_new):
            continue
        if row is None:
            row = Artifact(
                id=uuid7_str(),
                user_id=user_id,
                ref=ref,
                source="run",
                name=d.title or d.id,
                pinned=False,
            )
            session.add(row)
        row.log_id = log_id
        row.artifact_type = t.id
        row.type_version = t.version
        row.module_id = manifest.id
        row.dataset_id = d.id
        row.variant = variant
        row.key = d.cache_key or ""
        row.producer_version = manifest.version
        row.producer_hash = producer_hash
        row.inputs = lineage
        row.spec = spec
        row.size_bytes = sizes[ref]
        row.updated_at = _utcnow()
    await session.commit()


def _active_logs(user_id: str, module_ids: Iterable[str]) -> list[str]:
    """The ``DISCOVER_LOGS`` logs whose result dirs of *module_ids* changed last (an
    entry added: a run's output, an import, a run variant). Stats only; blocking."""
    root = get_settings().module_results_dir_for(user_id)
    newest: dict[str, float] = {}
    with contextlib.suppress(OSError):
        for d in root.iterdir():
            for mid in module_ids:
                with contextlib.suppress(OSError):
                    newest[d.name] = max(newest.get(d.name, 0.0), (d / mid).stat().st_mtime)
    return heapq.nlargest(DISCOVER_LOGS, newest, key=newest.__getitem__)


async def discover(
    session: AsyncSession,
    user_id: str,
    log_id: str | None,
    owned_producers: Mapping[str, Manifest],
) -> None:
    """Bring the index of *owned_producers*' outputs on *log_id* in line with the result
    cache - without one, on the newest logs and on those whose results changed last.
    Indexes a default result or a ready run variant's that has no row (an entry a
    module route wrote - Model Interop's imports -, a run that settled before the index
    or before an uninstall dropped its rows), and refreshes a default row whose file
    changed since (a re-import). Cheap: two queries, a stat per output and a listing per
    module dir; a variant's meta is read only while none of its outputs has a row. Best
    effort: never fails the list."""
    keys = _output_keys(owned_producers)
    if not keys:
        return
    logs = select(EventLog.id).where(EventLog.user_id == user_id, EventLog.deleted_at.is_(None))
    if log_id is not None:
        log_ids = set((await session.execute(logs.where(EventLog.id == log_id))).scalars())
    else:
        active = await asyncio.to_thread(_active_logs, user_id, keys)
        newest = logs.order_by(EventLog.created_at.desc()).limit(DISCOVER_LOGS)
        log_ids = {
            *(await session.execute(newest)).scalars(),
            *(await session.execute(logs.where(EventLog.id.in_(active)))).scalars(),
        }
    await _index(session, user_id, log_ids, owned_producers, keys)


def _output_keys(owned_producers: Mapping[str, Manifest]) -> dict[str, set[str]]:
    """The cache keys of each producer's typed, cached outputs (producers with none
    left out)."""
    return {
        mid: ks
        for mid, m in owned_producers.items()
        if (
            ks := {
                d.cache_key
                for d in m.datasets
                if d.cache_key is not None and get_artifact_type(d.artifact_type or "") is not None
            }
        )
    }


async def _index(
    session: AsyncSession,
    user_id: str,
    log_ids: set[str],
    owned_producers: Mapping[str, Manifest],
    keys: dict[str, set[str]],
) -> None:
    """``discover`` on *log_ids*."""
    if not log_ids:
        return
    root = get_settings().module_results_dir_for(user_id)

    # shortcut: local files only - an S3-mode entry evicted from the working cache is
    # indexed (or refreshed) once a read hydrates it again.
    def _scan() -> tuple[dict[str, os.stat_result], dict[str, _Run]]:
        """The stat of each default output's file, and the run of every default output
        on disk and of every run variant's output (by ref)."""
        stats: dict[str, os.stat_result] = {}
        found: dict[str, _Run] = {}
        for lg in log_ids:
            for mid, ks in keys.items():
                d = root / lg / mid
                for key in ks:
                    if (st := _stat(d, key)) is not None:
                        ref = str(ArtifactRef(lg, mid, key))
                        stats[ref], found[ref] = st, (lg, mid, None)
                with contextlib.suppress(OSError):  # removed while listed
                    for vd in d.glob(f"{RUN_PREFIX}*"):
                        var = vd.name[len(RUN_PREFIX) :]
                        for key in ks if is_run_variant(var) else ():
                            found[str(ArtifactRef(lg, mid, key, var))] = (lg, mid, var)
        return stats, found

    stats, found = await asyncio.to_thread(_scan)
    rows = {
        r.ref: r
        for r in (
            await session.execute(
                select(Artifact).where(
                    Artifact.user_id == user_id,
                    Artifact.log_id.in_(log_ids),
                    Artifact.module_id.in_(list(keys)),
                )
            )
        ).scalars()
    }
    # A re-import overwrites the entry in place: show its new size and time. In S3 mode a
    # hydrate rewrites the mtime of unchanged content: there only a new size counts.
    # shortcut: an S3-mode re-import of the very same size keeps the old time; compare a
    # content hash if that matters.
    local = not storage_sync.is_s3()
    changed = [
        (row, st)
        for ref, st in stats.items()
        if (row := rows.get(ref)) is not None
        and (row.size_bytes != st.st_size or (local and _mtime(st) > row.updated_at))
    ]
    if changed:
        try:
            for row, st in changed:
                row.size_bytes = st.st_size
                row.updated_at = _utcnow()
            await session.commit()
        except Exception:  # never fails the list
            await session.rollback()
            log.warning("modules.artifacts.refresh_failed", user_id=user_id, exc_info=True)
    # A variant with a row for any of its outputs is indexed: one that never writes some
    # of them (an interop variant's `imported_*`) is not read again on every list.
    indexed = {(r.log_id, r.module_id, r.variant) for r in rows.values() if r.variant}
    unseen = {run for ref, run in found.items() if ref not in rows and run not in indexed}

    def _todo() -> list[tuple[_Run, dict[str, Any] | None]]:
        """The unseen runs to index, with a variant's meta: a default result, and a
        ready variant with an output on disk (a failed or rerunning one has none)."""
        out: list[tuple[_Run, dict[str, Any] | None]] = []
        for lg, mid, var in sorted(unseen, key=str):
            if var is None:
                out.append(((lg, mid, var), None))
                continue
            if not any(_stat(root / lg / mid / f"{RUN_PREFIX}{var}", k) for k in keys[mid]):
                continue
            meta = ResultCache(lg, mid, user_id, run_variant=var).read_meta()
            if meta is not None and meta.get("status") == "ready":
                out.append(((lg, mid, var), meta))
        return out

    for (lg, mid, var), meta in await asyncio.to_thread(_todo):
        try:
            await register_run(
                session,
                user_id=user_id,
                log_id=lg,
                manifest=owned_producers[mid],
                owned_producers=owned_producers,
                variant=var,
                spec=None if meta is None else cast(dict[str, Any], meta.get("spec") or {}),
                producer_hash=None if meta is None else meta.get("fingerprint"),
                only_new=True,
            )
        except Exception:  # best effort, never fails the list (a race with the run's own
            # registration is an IntegrityError: its row is there either way)
            await session.rollback()
            log.warning(
                "modules.artifacts.discover_failed",
                log_id=lg,
                module_id=mid,
                variant=var,
                exc_info=True,
            )


async def backfill(manifests: Mapping[str, Manifest]) -> None:
    """Index, once, the results computed before the workspace existed: ``discover`` on
    every log of every user, ``DISCOVER_LOGS`` at a time, over the producers each user
    owns of *manifests* (the loaded modules). A background task at startup, after the
    modules loaded (off with ``ARTIFACTS_BACKFILL=false``); ``system_settings`` keeps the
    modules a clean pass covered, so a later boot only does modules that were not loaded
    then (a failed load) - a shutdown or a failed user midway records nothing. Never
    raises: a user it fails for is logged and left to the lists' own discovery."""
    if not get_settings().artifacts_backfill:
        return
    sessionmaker = get_sessionmaker()
    try:
        async with sessionmaker() as session:
            marker = await session.get(SystemSetting, BACKFILL_KEY)
            value = marker.value_json if marker is not None else None
            done = set[str](value.get("modules", [])) if isinstance(value, dict) else set[str]()
            todo = {m: man for m, man in manifests.items() if m not in done}
            if marker is not None and not todo:
                return
            live = select(EventLog.id).where(EventLog.deleted_at.is_(None))
            users = list(
                (
                    await session.execute(
                        select(EventLog.user_id).where(EventLog.deleted_at.is_(None)).distinct()
                    )
                ).scalars()
            )
        complete = True
        for user_id in users:
            try:
                async with sessionmaker() as session:
                    owned = await user_module_ids(session, user_id)
                    producers = {m: man for m, man in todo.items() if m in owned}
                    keys = _output_keys(producers)
                    mine = live.where(EventLog.user_id == user_id)
                    log_ids = list((await session.execute(mine)).scalars()) if keys else []
                    for i in range(0, len(log_ids), DISCOVER_LOGS):
                        batch = set(log_ids[i : i + DISCOVER_LOGS])
                        await asyncio.to_thread(_hydrate_outputs, user_id, batch, keys)
                        await _index(session, user_id, batch, producers, keys)
                        await asyncio.sleep(0.1)  # low priority: let requests through
            except Exception:
                complete = False
                log.warning("modules.artifacts.backfill_failed", user_id=user_id, exc_info=True)
        if not complete:
            log.info("modules.artifacts.backfill_incomplete", users=len(users))
            return
        async with sessionmaker() as session:
            value = {"at": _utcnow().isoformat(), "modules": sorted(done | todo.keys())}
            row = await session.get(SystemSetting, BACKFILL_KEY)
            if row is None:
                session.add(SystemSetting(key=BACKFILL_KEY, value_json=value))
            else:
                row.value_json = value
            await session.commit()
        log.info("modules.artifacts.backfilled", users=len(users), modules=sorted(todo))
    except Exception:
        log.warning("modules.artifacts.backfill_failed", exc_info=True)


def _hydrate_outputs(user_id: str, log_ids: set[str], keys: dict[str, set[str]]) -> None:
    """S3 mode: pull just the typed outputs (default and run variants) and the variants'
    meta files a fresh or evicted host lacks, so ``_index`` - which scans local files -
    sees what lives only in the bucket. No-op in local mode. Blocking."""
    root = get_settings().module_results_dir_for(user_id)
    for lg in log_ids:
        for mid, ks in keys.items():
            names = {f"{k}.{ext}" for k in ks for ext in _EXT.values()} | {f"{META_KEY}.json"}

            def want(rel: str, names: set[str] = names) -> bool:
                return rel.rsplit("/", 1)[-1] in names

            storage_sync.hydrate_missing_sync(root / lg / mid, want)


async def forget_variant(user_id: str, log_id: str, module_id: str, variant: str) -> None:
    """Drop the rows of a deleted or evicted run variant."""
    async with get_sessionmaker()() as session:
        await session.execute(
            delete(Artifact).where(
                Artifact.user_id == user_id,
                Artifact.log_id == log_id,
                Artifact.module_id == module_id,
                Artifact.variant == variant,
            )
        )
        await session.commit()


async def pinned_variant_dirs(user_id: str) -> set[Path]:
    """The run-variant dirs holding a pinned artifact - the variant GC keeps them,
    unless their run failed or was cancelled."""
    async with get_sessionmaker()() as session:
        rows = (
            await session.execute(
                select(Artifact.log_id, Artifact.module_id, Artifact.variant).where(
                    Artifact.user_id == user_id,
                    Artifact.pinned.is_(True),
                    Artifact.variant.is_not(None),
                )
            )
        ).all()
    root = get_settings().module_results_dir_for(user_id)
    return {root / str(log) / str(mod) / f"{RUN_PREFIX}{var}" for log, mod, var in rows}


# -- pruning ----------------------------------------------------------------------


def _stat(d: Path, key: str) -> os.stat_result | None:
    """The stat of *key*'s cache entry in *d*, None when there is none."""
    for ext in _EXT.values():
        with contextlib.suppress(OSError):
            return (d / f"{key}.{ext}").stat()
    return None


def _mtime(st: os.stat_result) -> datetime:
    return datetime.fromtimestamp(st.st_mtime, UTC).replace(tzinfo=None)


def _dir(row: Artifact) -> Path:
    d = get_settings().module_results_dir_for(row.user_id) / str(row.log_id) / str(row.module_id)
    return d if row.variant is None else d / f"{RUN_PREFIX}{row.variant}"


def _exists(row: Artifact) -> bool:
    """Whether the row's file is still there - locally, else (S3 mode) in the mirror.
    Blocking; never downloads."""
    if row.source == "upload":
        path = workspace_file(row.user_id, row.id)
        return path.exists() or storage_sync.remote_has_sync(path.parent, path.name)
    d = _dir(row)
    if _stat(d, row.key) is not None:
        return True
    artifact_type = get_artifact_type(row.artifact_type)
    ext = _EXT.get(artifact_type.storage if artifact_type is not None else "", "json")
    return storage_sync.remote_has_sync(d, f"{row.key}.{ext}")


def _variant_gone(row: Artifact) -> bool:
    """Whether a run variant's dir is gone (deleted or evicted) - locally and from the
    S3 mirror. A rerun only empties it, and writes its queued meta right after."""
    d = _dir(row)
    return not d.is_dir() and not storage_sync.remote_has_sync(d, f"{META_KEY}.json")


def _state(row: Artifact) -> ArtifactState | None:
    """The row's state; None once its upload's file or its run variant's dir is gone.
    Blocking."""
    if _exists(row):
        return "ready"
    if row.source == "upload" or (row.variant is not None and _variant_gone(row)):
        return None
    if row.variant is None:
        return "missing"
    cache = ResultCache(str(row.log_id), str(row.module_id), row.user_id, run_variant=row.variant)
    status = (cache.read_meta() or {}).get("status")
    if status in ("failed", "cancelled"):
        return "failed"
    return "running" if status in ("queued", "running") else "missing"


async def states(session: AsyncSession, rows: Sequence[Artifact]) -> dict[str, ArtifactState]:
    """The state of each of *rows* still there, by id. An upload's row and a run
    variant's whose dir is gone are deleted (and left out); any other run's row whose
    file is missing stays: a rerun empties the cache (or the variant's dir) first, and
    the row keeps the user's name and pin for when the result is back."""

    def _states() -> dict[str, ArtifactState | None]:
        return {r.id: _state(r) for r in rows}

    found = await asyncio.to_thread(_states)
    gone = [i for i, s in found.items() if s is None]
    if gone:
        await session.execute(delete(Artifact).where(Artifact.id.in_(gone)))
        await session.commit()
    out: dict[str, ArtifactState] = {i: s for i, s in found.items() if s is not None}
    return out


def listed(row: Artifact, state: ArtifactState) -> bool:
    """Whether the workspace shows the row: its file is there, or it is a pinned run
    variant's whose rerun is under way or failed (to retry or delete)."""
    return state == "ready" or (row.pinned and state in ("failed", "running"))


async def prune(session: AsyncSession, rows: Sequence[Artifact]) -> list[Artifact]:
    """*rows* whose file is there (``states``)."""
    found = await states(session, rows)
    return [r for r in rows if found.get(r.id) == "ready"]


# -- files --------------------------------------------------------------------------


def artifact_file(user_id: str, ref: ArtifactRef) -> Path | None:
    """The local file behind *ref* in *user_id*'s data (hydrated from S3 first), None
    when it is not there. Reading a run variant's stamps its ``last_used_at``. The
    caller has checked the ref is the user's. Blocking."""
    if ref.module_id == WORKSPACE_MODULE_ID:
        return workspace_path(user_id, ref.key)
    cache = ResultCache(ref.log_id, ref.module_id, user_id, run_variant=ref.variant)
    found = cache.path(ref.key)
    if found is not None:
        cache.touch()
    return found


def read_content(path: Path) -> bytes:
    """JSON text: a JSON artifact as stored, or a Parquet table's first
    ``PREVIEW_ROWS`` rows as ``{columns, rows, total_rows, truncated}``. Blocking."""
    if path.suffix == ".json":
        if path.stat().st_size > get_settings().artifact_upload_max_bytes:
            raise ArtifactTooLargeError(path.name)
        return path.read_bytes()
    if path.suffix != ".parquet":
        raise NotPreviewableError(path.name)
    import pyarrow.parquet as pq

    pf = pq.ParquetFile(path)
    batch = next(pf.iter_batches(batch_size=PREVIEW_ROWS), None)
    frame = (batch if batch is not None else pf.schema_arrow.empty_table()).to_pandas()
    preview = {
        "columns": [str(c) for c in frame.columns],
        # to_json maps NaN to null and timestamps to ISO strings: JSON-safe rows.
        "rows": json.loads(frame.to_json(orient="records", date_format="iso")),
        "total_rows": pf.metadata.num_rows,
        # The whole table: `GET /artifacts/{id}/download`.
        "truncated": pf.metadata.num_rows > len(frame),
    }
    return json.dumps(preview).encode()


def save_upload(user_id: str, artifact_id: str, text: str) -> int:
    """Write an uploaded artifact and persist it to the S3 mirror; its size. Blocking."""
    path = workspace_file(user_id, artifact_id)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text)
    storage_sync.persist_dir_sync(path.parent, skip=lambda rel: rel != path.name)
    return path.stat().st_size


def delete_upload(user_id: str, artifact_id: str) -> None:
    """Delete an uploaded artifact's file, locally and in the S3 mirror. Blocking."""
    path = workspace_file(user_id, artifact_id)
    with contextlib.suppress(FileNotFoundError):
        path.unlink()
    storage_sync.delete_dir_remote_sync(path.parent, keep=lambda rel: rel != path.name)
