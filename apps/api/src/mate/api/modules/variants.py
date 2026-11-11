"""Run variants - extra runs of one module's precompute for one log.

The default run (``log_module_runs``, ``modules.processing``) computes a module with
the log's own settings. A *run spec* asks for a different one::

    {"params": {...},   # values for the module's `ui.scope: dataset` properties only
     "inputs": {...},   # {port: "ArtifactRef"} - explicit bindings; unbound ports keep
                        #   the default binding (the `from` producer's default result)
     "filter": [...]}   # a dashboard-style filter override; null keeps the committed one

Its canonical JSON hashes to the *variant key* (16 hex chars). A variant has no DB
row: it lives in the module's result cache as ``_r_<key>/`` (``ResultCache(...,
run_variant=key)``) next to the default result, with its state in ``__meta.json``.
Like the default run it is ``stale`` once the log's data revision or the variant's
fingerprint (compute code + its effective precompute config + its producers') moves on.

Nothing runs a variant's producers for it: a required input that has not been produced
fails the variant at request time, with no job (and its job, if gone by the time it
starts). A variant's artifacts are addressed
``{log_id}/{module_id}@{key}/{cache_key}`` and indexed in the artifact workspace
(``mate.api.modules.artifacts``) once it settles ready. A user's finished variants are
capped at ``RUN_VARIANTS_MAX_BYTES_PER_USER``, least recently used deleted first - one
holding a pinned workspace artifact never, unless its (re)run failed or was cancelled.
"""

from __future__ import annotations

import asyncio
import contextlib
import json
import os
import time
from collections.abc import AsyncGenerator, Callable
from dataclasses import dataclass, field
from datetime import UTC, datetime
from functools import partial
from pathlib import Path
from typing import TYPE_CHECKING, Any, cast

import structlog
from pydantic import BaseModel, ConfigDict, Field, field_validator
from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from mate.api.config import get_settings
from mate.api.db.models import EventLog, Job, User
from mate.api.events import EventBus
from mate.api.jobs.runtime import WORKER_LOST
from mate.api.modules.artifacts import forget_variant, pinned_variant_dirs, register_run
from mate.api.modules.cache import (
    META_KEY,
    RUN_PREFIX,
    ResultCache,
    hydrate_run_metas,
    is_run_variant,
    run_variant_key,
)
from mate.api.modules.event_filters import FILTER_OPS
from mate.api.modules.inputs import ModuleInputs, resolve_refs
from mate.api.modules.installs import user_module_ids
from mate.api.modules.log_config import dataset_properties, run_fingerprints
from mate.api.storage import sync as storage_sync
from mate.sdk.artifacts import ArtifactRef, get_artifact_type
from mate.sdk.manifest import Manifest

if TYPE_CHECKING:
    from mate.api.modules.loader import LoadedModule, ModuleLoader

log = structlog.get_logger(__name__)

TOPIC = "module_variant.updated"
_IN_FLIGHT = ("queued", "running")
# A pin no longer keeps a variant whose run ended like this: the GC may evict it.
_UNKEPT = ("failed", "cancelled")
_FROM_JOB = {"completed": "ready", "failed": "failed", "cancelled": "cancelled"}
_ERROR_MAX = 2000
_INTERRUPTED = "Interrupted by a restart."
# A run variant dir with no readable `__meta.json` is an orphan (a cancelled job's late
# write) only once it is this old - a younger one may be a hydrate or first write.
_ORPHAN_GRACE_S = 600.0


def _utcnow() -> datetime:
    return datetime.now(UTC).replace(tzinfo=None)


class RunSpec(BaseModel):
    """What makes a variant differ from the module's default run."""

    model_config = ConfigDict(extra="forbid")

    params: dict[str, Any] = Field(default_factory=dict)
    inputs: dict[str, str] = Field(default_factory=dict)
    # Replaces the log's committed Events-tab filter; `[]` = the raw log.
    filter: list[dict[str, Any]] | None = None

    @field_validator("params")
    @classmethod
    def _drop_nulls(cls, value: dict[str, Any]) -> dict[str, Any]:
        # `null` inherits, like the per-log PUT: `{"x": null}` is the same variant as `{}`.
        return {k: v for k, v in value.items() if v is not None}

    @field_validator("filter")
    @classmethod
    def _known_ops(cls, value: list[dict[str, Any]] | None) -> list[dict[str, Any]] | None:
        for entry in value or []:
            if not isinstance(entry.get("field"), str) or entry.get("op") not in FILTER_OPS:
                raise ValueError(
                    f"filter entries need a string `field` and an `op` in {sorted(FILTER_OPS)}"
                )
        return value

    def canonical(self) -> dict[str, Any]:
        return self.model_dump(mode="json", include={"params", "inputs", "filter"})


@dataclass(frozen=True)
class VariantState:
    """One variant's state, as the API reports it."""

    variant: str
    # queued | running | ready | stale | failed | cancelled | unavailable
    status: str
    spec: dict[str, Any]
    job_id: str | None = None
    error: str | None = None
    finished_at: datetime | None = None
    # `{dataset_id, artifact_type, ref}` for each typed, cached output of the module.
    refs: list[dict[str, str]] = field(default_factory=lambda: [])


@dataclass(frozen=True)
class _Ctx:
    log_row: EventLog
    loaded: LoadedModule
    owned_producers: dict[str, Manifest]


def _derive(meta: dict[str, Any], revision: int, fingerprint: str) -> str:
    """The stored status, or ``stale`` once the data or the fingerprint moved on."""
    status = str(meta.get("status"))
    if status in _IN_FLIGHT:
        return status
    recorded = meta.get("fingerprint")
    if meta.get("data_revision") != revision or (recorded and recorded != fingerprint):
        return "stale"
    return status


def _refs(manifest: Manifest, log_id: str, key: str) -> list[dict[str, str]]:
    return [
        {
            "dataset_id": d.id,
            "artifact_type": str(get_artifact_type(d.artifact_type) or d.artifact_type),
            "ref": str(ArtifactRef(log_id, manifest.id, d.cache_key, key)),
        }
        for d in manifest.datasets
        if d.artifact_type is not None and d.cache_key is not None
    ]


def _finished(meta: dict[str, Any]) -> datetime | None:
    raw = meta.get("finished_at")
    return datetime.fromisoformat(raw) if isinstance(raw, str) else None


def _local_meta(d: Path) -> dict[str, Any] | None:
    """A run variant dir's ``__meta.json`` on this host (never fetched from S3); None
    when missing or unreadable."""
    try:
        meta = json.loads((d / f"{META_KEY}.json").read_text())
    except (OSError, ValueError):
        return None
    return cast(dict[str, Any], meta) if isinstance(meta, dict) else None


def _size(d: Path) -> int:
    """Bytes under *d*, leaving out ``_write_atomic`` temp files and tolerating any file
    removed between the listing and its stat."""
    total = 0
    for dirpath, _dirs, files in os.walk(d):
        for f in files:
            if f.endswith(".tmp"):
                continue
            with contextlib.suppress(FileNotFoundError):
                total += os.path.getsize(os.path.join(dirpath, f))
    return total


def _scan(root: Path) -> list[tuple[Path, int, dict[str, Any] | None]]:
    """``(dir, bytes, meta)`` of every run variant under a user's results root - but
    a meta-less dir only once it is past the orphan grace period."""
    out: list[tuple[Path, int, dict[str, Any] | None]] = []
    for d in root.glob(f"*/*/{RUN_PREFIX}*"):
        try:
            if not d.is_dir():
                continue
            size = _size(d)
            meta = _local_meta(d)
            # Without one it is an orphan (a cancelled job's late write): evicted first.
            if meta is None and time.time() - d.stat().st_mtime < _ORPHAN_GRACE_S:
                continue
        except FileNotFoundError:
            continue  # removed while scanning
        out.append((d, size, meta))
    return out


def _input_dirs(root: Path, spec: dict[str, Any]) -> set[Path]:
    """The run-variant dirs under *root* that a spec's explicit input refs read."""
    out: set[Path] = set()
    for text in cast(dict[str, Any], spec.get("inputs") or {}).values():
        try:
            ref = ArtifactRef.parse(str(text))
        except ValueError:
            continue
        if ref.variant is not None and is_run_variant(ref.variant):
            out.add(root / ref.log_id / ref.module_id / f"{RUN_PREFIX}{ref.variant}")
    return out


class VariantRunner:
    """Requests, settles, lists and deletes run variants (``coordinator.variants``)."""

    def __init__(
        self,
        loader: ModuleLoader,
        bus: EventBus,
        sessionmaker: async_sessionmaker[AsyncSession],
        submission: Callable[[EventLog, str, str, str | None], tuple[str | None, dict[str, Any]]],
    ) -> None:
        self._loader = loader
        self._bus = bus
        self._sessionmaker = sessionmaker
        # The coordinator's `(declared topic, event payload)` for a module + log.
        self._submission = submission
        # One lock per (log, module, variant); never pruned, like the coordinator's.
        self._locks: dict[tuple[str, str, str], asyncio.Lock] = {}

    @contextlib.asynccontextmanager
    async def _locked(self, log_id: str, module_id: str, key: str) -> AsyncGenerator[None]:
        lock = self._locks.setdefault((log_id, module_id, key), asyncio.Lock())
        async with lock:
            yield

    async def _context(
        self, session: AsyncSession, user_id: str, log_id: str, module_id: str
    ) -> _Ctx:
        """The owned log + loaded, owned module, or LookupError (the routes' 404)."""
        log_row = await session.get(EventLog, log_id)
        if log_row is None or log_row.deleted_at is not None or log_row.user_id != user_id:
            raise LookupError(f"Event log {log_id!r} not found.")
        owned = await user_module_ids(session, user_id)
        loaded = self._loader.loaded.get(module_id)
        if module_id not in owned or loaded is None:
            raise LookupError(f"Module {module_id!r} is not installed.")
        producers = {m: lm.manifest for m, lm in self._loader.loaded.items() if m in owned}
        return _Ctx(log_row, loaded, producers)

    async def _fingerprint(
        self, session: AsyncSession, ctx: _Ctx, user_id: str, params: dict[str, Any]
    ) -> str:
        """The run fingerprint of the module under this variant's effective config, its
        producers' (default-run) fingerprints included like the default run's."""
        mid = ctx.loaded.id
        fingerprints = await run_fingerprints(
            session,
            self._loader,
            user_id,
            ctx.log_row.id,
            [mid],
            owned=set(ctx.owned_producers),
            params={mid: params},
        )
        return cast(str, fingerprints[mid])  # loaded, so never None

    def _cache(self, user_id: str, log_id: str, module_id: str, key: str) -> ResultCache:
        return ResultCache(log_id, module_id, user_id, run_variant=key)

    async def _publish(
        self, user_id: str, log_id: str, module_id: str, key: str, status: str, job_id: str | None
    ) -> None:
        # `user_id` is mandatory: the SSE fan-out filters on it (tenant isolation).
        await self._bus.publish(
            TOPIC,
            {
                "user_id": user_id,
                "log_id": log_id,
                "module_id": module_id,
                "variant": key,
                "status": status,
                "job_id": job_id,
            },
        )

    async def _reconciled(
        self, user_id: str, log_id: str, module_id: str, key: str, *, publish: bool = True
    ) -> dict[str, Any] | None:
        """The variant's meta with an in-flight run checked against its job: a live job
        reports its status, a finished (or vanished) one is written back and published -
        once, by whichever caller makes that transition. Queued with no job yet is a
        submission in progress: in flight. Hold the lock."""
        cache = self._cache(user_id, log_id, module_id, key)
        meta = await asyncio.to_thread(cache.read_meta)
        job_id = meta.get("job_id") if meta is not None else None
        if meta is None or meta.get("status") not in _IN_FLIGHT or not isinstance(job_id, str):
            return meta
        async with self._sessionmaker() as session:
            job = await session.get(Job, job_id)
            if job is not None and job.status in _IN_FLIGHT:
                return {**meta, "status": job.status}
            if job is None:
                status, error, finished = "failed", "The run's job no longer exists.", None
            else:
                status = _FROM_JOB.get(job.status)
                error, finished = (job.error or "")[:_ERROR_MAX] or None, job.finished_at
            if status is None:
                return meta
        meta = {
            **meta,
            "status": status,
            "error": error if status == "failed" else None,
            "finished_at": (finished or _utcnow()).isoformat(),
        }
        await asyncio.to_thread(cache.write_meta, meta)
        if status == "ready":
            await self._register(user_id, log_id, module_id, key, meta)
        if publish:
            await self._publish(user_id, log_id, module_id, key, status, job_id)
        return meta

    async def _register(
        self, user_id: str, log_id: str, module_id: str, key: str, meta: dict[str, Any]
    ) -> None:
        """Index a variant that just settled ready in the artifact workspace. Best
        effort: a failure here never fails the variant."""
        loaded = self._loader.loaded.get(module_id)
        if loaded is None:
            return
        try:
            async with self._sessionmaker() as session:
                owned = await user_module_ids(session, user_id)
                await register_run(
                    session,
                    user_id=user_id,
                    log_id=log_id,
                    manifest=loaded.manifest,
                    owned_producers={
                        m: lm.manifest for m, lm in self._loader.loaded.items() if m in owned
                    },
                    variant=key,
                    spec=cast(dict[str, Any], meta.get("spec") or {}),
                    producer_hash=meta.get("fingerprint"),
                )
        except Exception:
            log.exception(
                "modules.variants.register_failed", log_id=log_id, module_id=module_id, variant=key
            )

    async def _state(
        self, session: AsyncSession, ctx: _Ctx, user_id: str, key: str, meta: dict[str, Any]
    ) -> VariantState:
        spec = cast(dict[str, Any], meta.get("spec") or {})
        fingerprint = await self._fingerprint(session, ctx, user_id, spec.get("params") or {})
        return VariantState(
            variant=key,
            status=_derive(meta, int(ctx.log_row.data_revision or 0), fingerprint),
            spec=spec,
            job_id=meta.get("job_id"),
            error=meta.get("error"),
            finished_at=_finished(meta),
            refs=_refs(ctx.loaded.manifest, ctx.log_row.id, key),
        )

    # -- the API ---------------------------------------------------------------

    async def ensure(
        self,
        user_id: str,
        log_id: str,
        module_id: str,
        spec: RunSpec,
        *,
        force: bool = False,
        rerun: bool = False,
        parent_job_id: str | None = None,
    ) -> VariantState:
        """Bring the variant *spec* names up to date, submitting its job if needed.

        Idempotent: a fresh or in-flight variant is returned as is; a failed or
        cancelled one - or an in-flight one whose job this process no longer holds (a
        restart lost it) - is rerun only with ``force``, a ready one only with
        ``rerun`` (a flow whose upstream variant just re-ran: an explicit input ref
        names the producer variant, not its content, so it never goes stale by
        itself). Otherwise only this variant's directory is emptied and the module's
        precompute submitted with the spec - under *parent_job_id* (a flow run's job).
        LookupError for a log or module the user doesn't own; ValueError for a spec that
        doesn't fit the module (a non-``scope: dataset`` param, an unknown port, a bad ref).
        """
        canonical = spec.canonical()
        key = run_variant_key(canonical)
        async with self._locked(log_id, module_id, key), self._sessionmaker() as session:
            ctx = await self._context(session, user_id, log_id, module_id)
            manifest = ctx.loaded.manifest
            unknown = sorted(set(spec.params) - set(dataset_properties(manifest)))
            if unknown:
                raise ValueError(f"Not a per-log parameter of {module_id!r}: {', '.join(unknown)}.")
            refs, problems = await resolve_refs(
                session,
                user_id=user_id,
                log_id=log_id,
                ports=manifest.inputs,
                owned_producers=ctx.owned_producers,
                refs=spec.inputs,
            )
            if problems:
                raise ValueError("; ".join(problems))
            revision = int(ctx.log_row.data_revision or 0)
            fingerprint = await self._fingerprint(session, ctx, user_id, spec.params)
            cache = self._cache(user_id, log_id, module_id, key)
            meta = await self._reconciled(user_id, log_id, module_id, key)
            if meta is not None:
                state = await self._state(session, ctx, user_id, key, meta)
                job_id = meta.get("job_id")
                fresh_in_flight = (
                    state.status in _IN_FLIGHT
                    and meta.get("data_revision") == revision
                    and meta.get("fingerprint") == fingerprint
                    and not (
                        force
                        and not (isinstance(job_id, str) and self._loader.runtime.is_live(job_id))
                    )
                )
                settled = (state.status == "ready" and not rerun) or (
                    state.status in ("failed", "cancelled") and not force
                )
                if fresh_in_flight or settled:
                    return state

            avail = self._loader.availability_for(
                detected_schema=ctx.log_row.detected_schema,
                events_count=ctx.log_row.events_count,
                cases_count=ctx.log_row.cases_count,
                installed_module_ids=set(ctx.owned_producers),
                log_model=ctx.log_row.log_model,
            ).get(module_id)
            declared, payload = self._submission(ctx.log_row, module_id, user_id, None)
            if avail is None or avail.status == "unavailable" or declared is None:
                reason = "; ".join(avail.reasons) if avail is not None else None
                return VariantState(
                    variant=key,
                    status="unavailable",
                    spec=canonical,
                    error=reason or "This module cannot precompute for this kind of log.",
                )

            # Cancel before clearing, so a cancelled job can't land output in the new run.
            if meta is not None and meta.get("status") in _IN_FLIGHT and meta.get("job_id"):
                await self._loader.runtime.cancel(str(meta["job_id"]))
            await asyncio.to_thread(cache.clear)
            now = _utcnow().isoformat()
            meta = {
                "spec": canonical,
                "status": "queued",
                "data_revision": revision,
                "fingerprint": fingerprint,
                "requested_at": now,
                "last_used_at": now,
                "job_id": None,
                "error": None,
                "finished_at": None,
            }
            # In flight before anything below can evict an input: another request's GC
            # scan then sees this spec (queued, no job yet) and keeps the dirs it reads.
            await asyncio.to_thread(cache.write_meta, meta)
            try:
                inputs = ModuleInputs.bind(
                    manifest.inputs, ctx.owned_producers, log_id, user_id, refs
                )
                missing = await asyncio.to_thread(inputs.missing)
                if missing:
                    meta.update(
                        status="failed",
                        error="; ".join(f"input {p!r} not produced" for p in missing),
                        finished_at=now,
                    )
                else:
                    root = get_settings().module_results_dir_for(user_id)
                    await self._gc(user_id, {cache.dir, *_input_dirs(root, canonical)})
                    meta["job_id"] = await self._loader.submit_precompute(
                        module_id,
                        declared,
                        user_id,
                        log_id,
                        payload,
                        subtitle=f"{ctx.log_row.name} · variant {key}",
                        run_spec=canonical,
                        parent_job_id=parent_job_id,
                    )
            except Exception as exc:
                # Never leave it queued with no job: nothing would ever settle it.
                error = str(exc)[:_ERROR_MAX] or type(exc).__name__
                meta.update(status="failed", error=error, finished_at=now)
                await asyncio.to_thread(cache.write_meta, meta)
                raise
            await asyncio.to_thread(cache.write_meta, meta)
            state = await self._state(session, ctx, user_id, key, meta)
        await self._publish(user_id, log_id, module_id, key, state.status, state.job_id)
        return state

    async def settle(
        self, job_id: str, user_id: str, log_id: str, module_id: str, spec: dict[str, Any]
    ) -> None:
        """Apply a variant job's terminal state to its ``__meta.json`` (the coordinator's
        terminal-job hook), unless a read already has. Only the job the variant points
        at counts, so *job_id* being an older one changes nothing."""
        key = run_variant_key(spec)
        async with self._locked(log_id, module_id, key):
            await self._reconciled(user_id, log_id, module_id, key)

    async def get(self, user_id: str, log_id: str, module_id: str, key: str) -> VariantState | None:
        if not is_run_variant(key):
            return None
        async with self._locked(log_id, module_id, key), self._sessionmaker() as session:
            ctx = await self._context(session, user_id, log_id, module_id)
            meta = await self._reconciled(user_id, log_id, module_id, key)
            return None if meta is None else await self._state(session, ctx, user_id, key, meta)

    async def list(self, user_id: str, log_id: str, module_id: str) -> list[VariantState]:
        async with self._sessionmaker() as session:
            await self._context(session, user_id, log_id, module_id)
        module_dir = ResultCache(log_id, module_id, user_id).dir
        await asyncio.to_thread(hydrate_run_metas, module_dir)
        keys = sorted(
            p.name[len(RUN_PREFIX) :]
            for p in module_dir.glob(f"{RUN_PREFIX}*")
            if p.is_dir() and is_run_variant(p.name[len(RUN_PREFIX) :])
        )
        states = [await self.get(user_id, log_id, module_id, key) for key in keys]
        return [s for s in states if s is not None]

    async def delete(self, user_id: str, log_id: str, module_id: str, key: str) -> bool:
        """Cancel the variant's job if in flight and delete its directory."""
        if not is_run_variant(key):
            return False
        async with self._locked(log_id, module_id, key), self._sessionmaker() as session:
            await self._context(session, user_id, log_id, module_id)
            cache = self._cache(user_id, log_id, module_id, key)
            meta = await asyncio.to_thread(cache.read_meta)
            if meta is None:
                return False
            if meta.get("status") in _IN_FLIGHT and meta.get("job_id"):
                await self._loader.runtime.cancel(str(meta["job_id"]))
            await asyncio.to_thread(cache.remove)
            await forget_variant(user_id, log_id, module_id, key)
        await self._publish(user_id, log_id, module_id, key, "deleted", None)
        return True

    async def reconcile_boot(self) -> None:
        """Resubmit, once, the variants a previous process left in flight (the
        coordinator's boot hook) - nothing re-enqueues a variant's job, so it would read
        as in flight forever: one whose job it left queued or running (failed as
        interrupted first), one whose job the runtime failed as killed mid-run (like the
        default run's restart), and one whose submission it cut short (queued, no job).
        Clients see only the resubmission's ``queued`` - the lost run's ``failed`` only
        when nothing resubmits it."""
        async with self._sessionmaker() as session:
            # shortcut: also re-reads the meta of every older worker-lost variant job
            # still in the table, each boot; fine while jobs are pruned.
            rows = (
                await session.execute(
                    select(Job).where(
                        Job.status.in_(_IN_FLIGHT)
                        | ((Job.status == "failed") & (Job.error == WORKER_LOST))
                    )
                )
            ).scalars()
            lost = [
                (j.id, j.user_id, j.module_id, j.payload_json, j.status in _IN_FLIGHT)
                for j in rows
                if j.module_id is not None
                and isinstance(j.payload_json.get("_run_spec"), dict)
                and not self._loader.runtime.is_live(j.id)
            ]
            interrupted = [job_id for job_id, *_, in_flight in lost if in_flight]
            if interrupted:
                await session.execute(
                    update(Job)
                    .where(Job.id.in_(interrupted))
                    .values(status="failed", error=_INTERRUPTED, finished_at=_utcnow())
                )
                await session.commit()
            user_ids = list((await session.execute(select(User.id))).scalars())
        for job_id, user_id, module_id, payload, _ in lost:
            log_id = str(payload.get("log_id") or "")
            try:
                spec = RunSpec.model_validate(payload["_run_spec"])
                key = run_variant_key(spec.canonical())
                async with self._locked(log_id, module_id, key):
                    meta = await asyncio.to_thread(
                        self._cache(user_id, log_id, module_id, key).read_meta
                    )
                    # Only the job the variant still points at: an older one was superseded.
                    if meta is None or meta.get("job_id") != job_id:
                        continue
                    if meta.get("status") not in _IN_FLIGHT:
                        continue
                    await self._reconciled(user_id, log_id, module_id, key, publish=False)
                resubmitted = False
                try:
                    state = await self.ensure(user_id, log_id, module_id, spec, force=True)
                    resubmitted = state.status != "unavailable"
                finally:
                    if not resubmitted:  # the lost run's `failed` is final: say so
                        await self._publish(user_id, log_id, module_id, key, "failed", job_id)
            except Exception:
                log.exception(
                    "modules.variants.reconcile_failed",
                    log_id=log_id,
                    module_id=module_id,
                    job_id=job_id,
                )
        for user_id in user_ids:
            root = get_settings().module_results_dir_for(user_id)
            for d, _size, meta in await asyncio.to_thread(_scan, root):
                if meta is None or meta.get("status") not in _IN_FLIGHT or meta.get("job_id"):
                    continue
                log_id, module_id = d.parent.parent.name, d.parent.name
                try:
                    spec = RunSpec.model_validate(meta.get("spec") or {})
                    await self.ensure(user_id, log_id, module_id, spec, force=True)
                except Exception:
                    log.exception(
                        "modules.variants.reconcile_failed", log_id=log_id, module_id=module_id
                    )

    # -- GC --------------------------------------------------------------------

    async def _gc(self, user_id: str, keep: set[Path]) -> None:
        """Delete the user's least recently used, settled variants until the total size
        of the settled ones is under ``RUN_VARIANTS_MAX_BYTES_PER_USER`` - never a dir in
        *keep* (the one being submitted and the producer variants its spec reads), one
        an in-flight variant's spec reads, nor one holding a pinned workspace artifact -
        unless its run failed or was cancelled (it has nothing to keep)."""
        cap = get_settings().run_variants_max_bytes_per_user
        if cap <= 0:
            return
        root = get_settings().module_results_dir_for(user_id)
        # shortcut: sizes only what is on local disk - in S3 mode a variant the reaper
        # evicted locally isn't counted until it is read again.
        found = await asyncio.to_thread(_scan, root)
        total = 0
        keep = set(keep)
        pinned = await pinned_variant_dirs(user_id)
        for d, size, scanned in found:
            status = (scanned or {}).get("status")
            if status in _IN_FLIGHT:
                keep |= _input_dirs(root, cast(dict[str, Any], (scanned or {}).get("spec") or {}))
            else:
                total += size
            if d in pinned and status not in _UNKEPT:
                keep.add(d)

        def _lru(item: tuple[Path, int, dict[str, Any] | None]) -> str:
            meta = item[2] or {}
            return str(meta.get("last_used_at") or meta.get("finished_at") or "")

        for d, size, scanned in sorted(found, key=_lru):
            if total < cap:
                break
            log_id, module_id, key = d.parent.parent.name, d.parent.name, d.name[3:]
            if d in keep or not is_run_variant(key):
                continue
            lock = self._locks.get((log_id, module_id, key))
            if lock is not None and lock.locked():
                continue  # being (re)submitted or settled right now
            async with self._locked(log_id, module_id, key):
                cache = self._cache(user_id, log_id, module_id, key)
                # The scan is lock-free and may be stale: an orphan may have a meta by
                # now, a settled variant may be resubmitted.
                meta = await asyncio.to_thread(_local_meta, d)
                if meta is not None and (scanned is None or meta.get("status") in _IN_FLIGHT):
                    continue
                if d in pinned and (meta or {}).get("status") not in _UNKEPT:
                    continue  # a pinned variant rerun since the scan
                if scanned is None:
                    # An orphan here - but in S3 mode the mirror may hold the whole
                    # variant (this dir a partial copy): drop that only if it is one too.
                    orphan = not await asyncio.to_thread(
                        storage_sync.remote_has_sync, cache.dir, f"{META_KEY}.json"
                    )
                    await asyncio.to_thread(partial(cache.remove, remote=orphan))
                else:
                    await asyncio.to_thread(cache.remove)
                await forget_variant(user_id, log_id, module_id, key)
            total -= size
            log.info(
                "modules.variants.evicted",
                user_id=user_id,
                log_id=log_id,
                module_id=module_id,
                variant=key,
                bytes=size,
            )
