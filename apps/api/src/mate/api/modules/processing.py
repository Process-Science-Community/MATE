"""Module-processing coordinator - lazy, per-(log, module) precompute.

A log is ``ready`` the moment it is parsed; nothing waits on its modules. Whether a
module has computed for a log is a row in ``log_module_runs``, and a run is started
by exactly one of three triggers, all funnelled through :meth:`_ensure_one`:

* **Eager at import** - the loader hands every job-backed ``@on_event`` to
  :meth:`dispatch` instead of submitting it. A module runs for the event only if it
  is ``eager`` (its manifest, or an admin override) or already part of the log's
  *working set* (it has computed there before), and is enabled and available.
* **On use** - the module page, a dashboard card or an MCP read calls
  :meth:`ensure`, which also runs whatever the module needs first (chained
  producers, hard ``requirements.modules``).
* **Data change** - a committed filter or reimport re-publishes ``log.imported`` and
  a cell edit calls :meth:`schedule_refresh`; both refresh the working set only.

``log_module_runs.status`` is the state *for the data revision recorded on the row*.
``idle`` (no row) and ``stale`` (revision or module version moved on) are derived at
read time, so a data change never has to touch a row to invalidate it.

Every write to one (log, module) row runs under a per-pair lock, including the
terminal-job handler, so a job finishing while its row is being superseded can never
resurrect the old state. The API is a single uvicorn process (the in-memory job queue
already assumes it), so an ``asyncio.Lock`` is enough.
"""

from __future__ import annotations

import asyncio
import contextlib
from collections.abc import AsyncGenerator
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any

import structlog
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from mate.api.db.models import EventLog, Job, LogModuleRun, ModuleConfig
from mate.api.events import EventBus
from mate.api.modules.cache import ResultCache
from mate.api.modules.defaults import get_precompute_overrides, resolve_precompute_mode
from mate.api.modules.installs import user_module_ids
from mate.api.modules.loader import ModuleLoader
from mate.api.tasks import spawn

log = structlog.get_logger(__name__)

# The topics a freshly imported (or re-filtered) log is announced on.
IMPORT_TOPICS = ("log.imported", "ocel.imported")

_IN_FLIGHT = ("queued", "running")
# A module counts as "used on this log" while its last state is a result or a run
# in progress. Failed and cancelled runs are left out on purpose: re-running a
# module that keeps failing (cv4cdd with no model) on every data change helps nobody.
_WORKING_SET = ("ready", "queued", "running")
_JOB_TO_RUN_STATUS = {"completed": "ready", "failed": "failed", "cancelled": "cancelled"}

# Cell edits arrive in bursts; refresh the working set once they stop.
REFRESH_DELAY_S = 30.0

# The message `JobRuntime._reconcile_orphan_running` stamps on a job a crash killed.
_WORKER_LOST = "Worker terminated before job completed."
_ERROR_MAX = 2000


def _utcnow() -> datetime:
    return datetime.now(UTC).replace(tzinfo=None)


@dataclass(frozen=True)
class RunState:
    """One module's state for one log, as the API reports it."""

    module_id: str
    # idle | queued | running | ready | stale | failed | cancelled | unavailable
    status: str
    job_id: str | None = None
    # The effective import-time mode, only for a module that precomputes on import.
    precompute: str | None = None
    error: str | None = None
    finished_at: datetime | None = None


@dataclass(frozen=True)
class _Row:
    """A detached copy of a ``log_module_runs`` row (sessions are short-lived)."""

    status: str
    job_id: str | None
    data_revision: int
    module_version: str | None
    error: str | None
    finished_at: datetime | None

    @classmethod
    def of(cls, row: LogModuleRun) -> _Row:
        return cls(
            status=row.status,
            job_id=row.job_id,
            data_revision=int(row.data_revision),
            module_version=row.module_version,
            error=row.error,
            finished_at=row.finished_at,
        )

    @property
    def waiting(self) -> bool:
        """Queued but with no job yet - held back until its prerequisites are ready."""
        return self.status == "queued" and self.job_id is None


def _derive(row: _Row | None, revision: int, version: str | None) -> str:
    """The reported status: the stored one, or ``idle`` / ``stale`` when it no
    longer describes the log's current data or the loaded module version."""
    if row is None:
        return "idle"
    if row.status in _IN_FLIGHT:
        return row.status
    outdated = row.data_revision != revision or (
        row.module_version is not None and version is not None and row.module_version != version
    )
    return "stale" if outdated else row.status


def _in_working_set(row: _Row | None) -> bool:
    return row is not None and row.status in _WORKING_SET


def _auto_run(*, mode: str | None, in_working_set: bool, allow_eager: bool) -> bool:
    """Whether an event (not a user) should start this module: it is in the log's
    working set, or it is eager and the event is a first import."""
    return in_working_set or (allow_eager and mode == "eager")


def log_imported_payload(row: EventLog, **extra: Any) -> dict[str, Any]:
    """The ``log.imported`` / ``ocel.imported`` body for an existing log row.

    What the import handler publishes, rebuilt from the row - for a committed
    filter and for on-demand runs, where no import is in flight.
    """
    payload: dict[str, Any] = {
        "log_id": row.id,
        "user_id": row.user_id,
        "events_count": int(row.events_count or 0),
        "detected_schema": row.detected_schema,
    }
    if row.log_model == "object_centric":
        payload.update(
            objects_count=int(row.objects_count or 0),
            object_types_count=int(row.object_types_count or 0),
            relations_count=int(row.relations_count or 0),
        )
    else:
        payload.update(cases_count=int(row.cases_count or 0), fixed_columns=[])
    payload.update(extra)
    return payload


@dataclass
class _Read:
    """Everything one `_ensure_one` decision needs, read in a single short session."""

    log_name: str
    log_model: str
    log_status: str
    revision: int
    row: _Row | None
    mode: str | None
    enabled: bool
    availability: str
    unavailable_reason: str | None
    prereqs: list[str]
    prereq_rows: dict[str, _Row | None]
    log_row: EventLog


class ModuleProcessingCoordinator:
    """Owns the lifecycle of every (log, module) precompute run."""

    def __init__(
        self,
        loader: ModuleLoader,
        bus: EventBus,
        sessionmaker: async_sessionmaker[AsyncSession],
    ) -> None:
        self._loader = loader
        self._bus = bus
        self._sessionmaker = sessionmaker
        # One lock per (log, module). Never pruned: bounded by the (log, module)
        # pairs ever touched, a few hundred bytes each.
        self._locks: dict[tuple[str, str], asyncio.Lock] = {}
        self._refresh_tasks: dict[str, asyncio.Task[Any]] = {}

    # -- plumbing -----------------------------------------------------------

    @contextlib.asynccontextmanager
    async def _locked(self, log_id: str, module_id: str) -> AsyncGenerator[None]:
        lock = self._locks.setdefault((log_id, module_id), asyncio.Lock())
        async with lock:
            yield

    def _version(self, module_id: str) -> str | None:
        loaded = self._loader.loaded.get(module_id)
        return loaded.manifest.version if loaded is not None else None

    def _is_import_root(self, module_id: str) -> bool:
        return any(
            module_id in self._loader.precompute_subscriber_module_ids(topic)
            for topic in IMPORT_TOPICS
        )

    async def _modes(self, session: AsyncSession) -> dict[str, str]:
        """Effective ``precompute`` mode of every module that precomputes on import."""
        overrides = await get_precompute_overrides(session)
        return {
            mid: resolve_precompute_mode(loaded.manifest.precompute, overrides, mid)
            for mid, loaded in self._loader.loaded.items()
            if self._is_import_root(mid)
        }

    async def precompute_info(self, session: AsyncSession) -> dict[str, dict[str, Any]]:
        """``{module_id: {manifest, effective, overridden}}`` for import-time modules.

        Feeds the admin modules page. A module with no import-time precompute is
        absent: there is nothing for an admin to switch.
        """
        overrides = await get_precompute_overrides(session)
        out: dict[str, dict[str, Any]] = {}
        for mid, loaded in self._loader.loaded.items():
            if not self._is_import_root(mid):
                continue
            manifest_mode = loaded.manifest.precompute
            effective = resolve_precompute_mode(manifest_mode, overrides, mid)
            out[mid] = {
                "manifest": manifest_mode,
                "effective": effective,
                "overridden": effective != manifest_mode,
            }
        return out

    async def _enabled(self, session: AsyncSession, user_id: str, module_id: str) -> bool:
        cfg = await session.get(ModuleConfig, (user_id, module_id))
        if cfg is not None:
            return bool(cfg.enabled)
        loaded = self._loader.loaded.get(module_id)
        return loaded.manifest.default_enabled if loaded is not None else False

    async def _publish_run(
        self, user_id: str, log_id: str, module_id: str, status: str, job_id: str | None
    ) -> None:
        # `user_id` is mandatory: the SSE fan-out filters on it server-side, and an
        # event without it would reach every connected user.
        await self._bus.publish(
            "module_run.updated",
            {
                "user_id": user_id,
                "log_id": log_id,
                "module_id": module_id,
                "status": status,
                "job_id": job_id,
            },
        )

    async def _write_row(
        self,
        user_id: str,
        log_id: str,
        module_id: str,
        *,
        status: str,
        job_id: str | None,
        revision: int,
        version: str | None,
        error: str | None = None,
        finished_at: datetime | None = None,
    ) -> None:
        async with self._sessionmaker() as session:
            row = await session.get(LogModuleRun, (log_id, module_id))
            if row is None:
                row = LogModuleRun(log_id=log_id, module_id=module_id, user_id=user_id)
                session.add(row)
            row.status = status
            row.job_id = job_id
            row.data_revision = revision
            row.module_version = version
            row.error = error
            row.requested_at = _utcnow()
            row.finished_at = finished_at
            await session.commit()

    async def _read(self, user_id: str, log_id: str, module_id: str) -> _Read:
        async with self._sessionmaker() as session:
            log_row = await session.get(EventLog, log_id)
            if log_row is None or log_row.deleted_at is not None or log_row.user_id != user_id:
                raise LookupError(f"Event log {log_id!r} not found.")
            owned = await user_module_ids(session, user_id)
            if module_id not in owned or module_id not in self._loader.loaded:
                raise LookupError(f"Module {module_id!r} is not installed.")
            modes = await self._modes(session)
            prereqs = [d for d in self._loader.precompute_deps(module_id, owned) if d != module_id]
            raw = {
                mid: await session.get(LogModuleRun, (log_id, mid)) for mid in (module_id, *prereqs)
            }
            rows = {mid: (_Row.of(r) if r is not None else None) for mid, r in raw.items()}
            avail = self._loader.availability_for(
                detected_schema=log_row.detected_schema,
                events_count=log_row.events_count,
                cases_count=log_row.cases_count,
                installed_module_ids=owned,
                log_model=log_row.log_model,
            ).get(module_id)
            return _Read(
                log_name=log_row.name,
                log_model=log_row.log_model,
                log_status=log_row.status,
                revision=int(log_row.data_revision or 0),
                row=rows.pop(module_id),
                mode=modes.get(module_id),
                enabled=await self._enabled(session, user_id, module_id),
                availability=avail.status if avail is not None else "unavailable",
                unavailable_reason="; ".join(avail.reasons) if avail is not None else None,
                prereqs=prereqs,
                prereq_rows=rows,
                log_row=log_row,
            )

    def _state(self, module_id: str, read: _Read) -> RunState:
        status = _derive(read.row, read.revision, self._version(module_id))
        return RunState(
            module_id=module_id,
            status=status,
            job_id=read.row.job_id if read.row is not None else None,
            precompute=read.mode,
            error=read.row.error if read.row is not None else None,
            finished_at=read.row.finished_at if read.row is not None else None,
        )

    # -- the one state machine ----------------------------------------------

    async def _ensure_one(
        self,
        user_id: str,
        log_id: str,
        module_id: str,
        *,
        force: bool = False,
        restart: bool = False,
        parent_job_id: str | None = None,
    ) -> RunState:
        """Bring one module's run for a log up to date, submitting its job if needed.

        Does nothing when the run is already fresh, in flight for the current data,
        or failed/cancelled for it (``force`` retries those; ``restart`` also
        re-runs an in-flight one whose job was lost in a crash). Otherwise it
        cancels any outdated job, empties the module's result cache and submits -
        or, when a prerequisite has not finished, parks a ``queued`` row with no job
        that :meth:`_release_waiters` submits once the prerequisites are ready.
        """
        async with self._locked(log_id, module_id):
            read = await self._read(user_id, log_id, module_id)
            version = self._version(module_id)
            if read.log_status != "ready":
                return RunState(module_id=module_id, status="idle", precompute=read.mode)
            row = read.row
            status = _derive(row, read.revision, version)

            if not restart:
                if status == "ready" or (status in ("failed", "cancelled") and not force):
                    return self._state(module_id, read)
                if (
                    row is not None
                    and status in _IN_FLIGHT
                    and not row.waiting
                    and row.data_revision == read.revision
                ):
                    return self._state(module_id, read)

            if read.availability == "unavailable":
                return RunState(
                    module_id=module_id,
                    status="unavailable",
                    precompute=read.mode,
                    error=read.unavailable_reason,
                )

            old_job_id = row.job_id if row is not None and row.status in _IN_FLIGHT else None

            # A prerequisite that failed can never un-block this module; one that is
            # still running (or stale and about to be refreshed) just has to finish.
            pending: list[str] = []
            for dep in read.prereqs:
                dep_status = _derive(read.prereq_rows.get(dep), read.revision, self._version(dep))
                if dep_status == "ready":
                    continue
                if dep_status in ("failed", "cancelled"):
                    return await self._park_failed(
                        user_id, log_id, module_id, read, f"{dep} did not complete ({dep_status})."
                    )
                pending.append(dep)
            if pending:
                if row is not None and row.waiting and row.data_revision == read.revision:
                    return self._state(module_id, read)
                if old_job_id:
                    await self._loader.runtime.cancel(old_job_id)
                await self._write_row(
                    user_id,
                    log_id,
                    module_id,
                    status="queued",
                    job_id=None,
                    revision=read.revision,
                    version=version,
                )
                await self._publish_run(user_id, log_id, module_id, "queued", None)
                return RunState(module_id=module_id, status="queued", precompute=read.mode)

            declared, payload = self._submission(read, module_id, user_id, parent_job_id)
            if declared is None:
                return RunState(
                    module_id=module_id,
                    status="unavailable",
                    precompute=read.mode,
                    error="This module cannot precompute for this kind of log.",
                )

            # Cancel before clearing: a cancelled job never reaches its cache write,
            # so it cannot land stale output in the cache the new run starts from.
            if old_job_id:
                await self._loader.runtime.cancel(old_job_id)
            await asyncio.to_thread(
                ResultCache(log_id, module_id, user_id).clear, keep_reserved=True
            )
            job_id = await self._loader.submit_precompute(
                module_id,
                declared,
                user_id,
                log_id,
                payload,
                parent_job_id=parent_job_id,
                subtitle=read.log_name,
            )
            await self._write_row(
                user_id,
                log_id,
                module_id,
                status="queued",
                job_id=job_id,
                revision=read.revision,
                version=version,
            )
            await self._publish_run(user_id, log_id, module_id, "queued", job_id)
            return RunState(
                module_id=module_id, status="queued", job_id=job_id, precompute=read.mode
            )

    def _submission(
        self, read: _Read, module_id: str, user_id: str, parent_job_id: str | None
    ) -> tuple[str | None, dict[str, Any]]:
        """``(declared topic, event payload)`` to run `module_id` with, or ``(None, {})``.

        A module that subscribes to the log's import topic gets a rebuilt import
        payload; one that is only chained gets the minimal ``<producer>.completed``
        body (no handler reads more than ``log_id``).
        """
        import_topic = "ocel.imported" if read.log_model == "object_centric" else "log.imported"
        declared = self._loader.precompute_subscription(module_id, import_topic)
        extra = {"import_job_id": parent_job_id} if parent_job_id else {}
        if declared is not None:
            return declared, log_imported_payload(read.log_row, **extra)
        chained = [
            t
            for t in self._loader.precompute_declared_topics(module_id)
            if not any(t == imp or t in ("*", "**") for imp in IMPORT_TOPICS)
        ]
        if not chained:
            return None, {}
        return chained[0], {"user_id": user_id, "log_id": read.log_row.id, **extra}

    async def _park_failed(
        self, user_id: str, log_id: str, module_id: str, read: _Read, error: str
    ) -> RunState:
        await self._write_row(
            user_id,
            log_id,
            module_id,
            status="failed",
            job_id=None,
            revision=read.revision,
            version=self._version(module_id),
            error=error,
            finished_at=_utcnow(),
        )
        await self._publish_run(user_id, log_id, module_id, "failed", None)
        return RunState(module_id=module_id, status="failed", precompute=read.mode, error=error)

    # -- triggers -----------------------------------------------------------

    async def ensure(
        self, user_id: str, log_id: str, module_id: str, *, force: bool = False
    ) -> list[RunState]:
        """Make sure `module_id` and everything it needs has computed for the log.

        Returns the state of each module in dependency order (empty when the module
        has no precompute anywhere in its closure - nothing to wait for). ``force``
        retries a failed or cancelled run; it never re-runs a fresh result.
        """
        async with self._sessionmaker() as session:
            owned = await user_module_ids(session, user_id)
        if module_id not in owned or module_id not in self._loader.loaded:
            raise LookupError(f"Module {module_id!r} is not installed.")
        states: list[RunState] = []
        for dep in self._loader.precompute_deps(module_id, owned):
            state = await self._ensure_one(user_id, log_id, dep, force=force)
            states.append(state)
            if state.status == "unavailable":
                break  # nothing after an unavailable module can run
        return states

    async def dispatch(
        self,
        module_id: str,
        declared_topic: str,
        event_topic: str,
        user_id: str,
        payload: dict[str, Any],
    ) -> None:
        """Handle one event for a job-backed ``@on_event`` (the loader's hook).

        An import event runs the module only if it is eager or already used on the
        log; a ``<module>.completed`` event only wakes a module that is waiting on it
        or in the working set; any other topic is a plain job-backed subscription
        and is submitted as before.
        """
        log_id = payload.get("log_id")
        parent = payload.get("import_job_id")
        parent_job_id = parent if isinstance(parent, str) else None
        if event_topic not in IMPORT_TOPICS and not event_topic.endswith(".completed"):
            await self._loader.submit_precompute(
                module_id,
                declared_topic,
                user_id,
                log_id if isinstance(log_id, str) else "",
                payload,
                parent_job_id=parent_job_id,
            )
            return
        if not isinstance(log_id, str) or not log_id:
            return
        # A refilter re-publishes `log.imported` with `reapplied`; it refreshes what
        # was used but must not wake modules that were never opened.
        first_import = event_topic in IMPORT_TOPICS and not payload.get("reapplied")
        await self._maybe_run(
            user_id, log_id, module_id, allow_eager=first_import, parent_job_id=parent_job_id
        )

    async def _maybe_run(
        self,
        user_id: str,
        log_id: str,
        module_id: str,
        *,
        allow_eager: bool,
        parent_job_id: str | None = None,
    ) -> None:
        """The automatic (non-user) gate in front of `_ensure_one`."""
        try:
            read = await self._read(user_id, log_id, module_id)
        except LookupError:
            return
        if not _auto_run(
            mode=read.mode, in_working_set=_in_working_set(read.row), allow_eager=allow_eager
        ):
            return
        if not read.enabled or read.availability == "unavailable":
            return
        await self._ensure_one(user_id, log_id, module_id, parent_job_id=parent_job_id)

    async def refresh_working_set(self, user_id: str, log_id: str) -> None:
        """Re-run, for the log's current data, every module already used on it."""
        async with self._sessionmaker() as session:
            ids = (
                (
                    await session.execute(
                        select(LogModuleRun.module_id).where(
                            LogModuleRun.log_id == log_id,
                            LogModuleRun.status.in_(_WORKING_SET),
                        )
                    )
                )
                .scalars()
                .all()
            )
            owned = await user_module_ids(session, user_id)
        # Producers first, so a consumer finds them in flight instead of stale.
        ordered = sorted(ids, key=lambda m: len(self._loader.precompute_deps(m, owned)))
        for module_id in ordered:
            try:
                await self._maybe_run(user_id, log_id, module_id, allow_eager=False)
            except Exception:
                log.exception("modules.processing.refresh_failed", log_id=log_id, module=module_id)

    def schedule_refresh(self, user_id: str, log_id: str, *, delay: float | None = None) -> None:
        """Refresh the working set once edits to the log go quiet (debounced per log)."""
        pending = self._refresh_tasks.pop(log_id, None)
        if pending is not None:
            pending.cancel()
        wait = REFRESH_DELAY_S if delay is None else delay

        async def _later() -> None:
            await asyncio.sleep(wait)
            self._refresh_tasks.pop(log_id, None)
            await self.refresh_working_set(user_id, log_id)

        self._refresh_tasks[log_id] = spawn(_later(), name=f"module-refresh-{log_id}")

    def stop(self) -> None:
        """Cancel debounced refreshes still waiting out their delay (shutdown).

        A refresh that never fires costs nothing: the working set's rows read as
        ``stale`` and recompute the next time their module is opened.
        """
        for task in self._refresh_tasks.values():
            task.cancel()
        self._refresh_tasks.clear()

    # -- job completion -----------------------------------------------------

    async def on_terminal_job(self, payload: dict[str, Any]) -> None:
        """Settle the run a terminal ``job.*`` event belongs to (if it tracks one).

        The (log, module) comes from the *job* row, not the run row: a job can finish
        before the run row that points at it is committed, and the lock below makes
        this wait for that commit instead of missing it.
        """
        job_id = payload.get("id")
        if not isinstance(job_id, str):
            return
        async with self._sessionmaker() as session:
            job = await session.get(Job, job_id)
            if job is None or job.module_id is None:
                return
            log_id = job.payload_json.get("log_id")
            if not isinstance(log_id, str) or not log_id:
                return
            module_id, user_id, parent_job_id = job.module_id, job.user_id, job.parent_job_id
        await self._settle(job_id, user_id, log_id, module_id, parent_job_id)

    async def _settle(
        self, job_id: str, user_id: str, log_id: str, module_id: str, parent_job_id: str | None
    ) -> None:
        async with self._locked(log_id, module_id), self._sessionmaker() as session:
            row = await session.get(LogModuleRun, (log_id, module_id))
            if row is None or row.job_id != job_id:
                return  # untracked, or superseded by a newer run
            job = await session.get(Job, job_id)
            log_row = await session.get(EventLog, log_id)
            new_status = _JOB_TO_RUN_STATUS.get(job.status) if job is not None else None
            if job is None or new_status is None or log_row is None:
                return
            error = (job.error or "")[:_ERROR_MAX] or None
            row.status = new_status
            row.error = error if new_status == "failed" else None
            row.finished_at = job.finished_at or _utcnow()
            await session.commit()
            status = _derive(
                _Row.of(row), int(log_row.data_revision or 0), self._version(module_id)
            )
        await self._publish_run(user_id, log_id, module_id, status, job_id)
        if new_status == "ready":
            if status == "ready":
                # Chained subscribers wake on this; its `import_job_id` keeps their job
                # in the same import group when the run was part of one.
                body: dict[str, Any] = {"user_id": user_id, "log_id": log_id}
                if parent_job_id:
                    body["import_job_id"] = parent_job_id
                await self._bus.publish(f"{module_id}.completed", body)
                await self._release_waiters(user_id, log_id)
            return
        await self._fail_waiters(user_id, log_id, module_id, error or new_status)

    async def _waiting_modules(self, log_id: str) -> list[str]:
        async with self._sessionmaker() as session:
            rows = await session.execute(
                select(LogModuleRun.module_id).where(
                    LogModuleRun.log_id == log_id,
                    LogModuleRun.status == "queued",
                    LogModuleRun.job_id.is_(None),
                )
            )
            return list(rows.scalars().all())

    async def _release_waiters(self, user_id: str, log_id: str) -> None:
        """Re-evaluate the log's parked modules now that one more has finished."""
        for module_id in await self._waiting_modules(log_id):
            try:
                await self._ensure_one(user_id, log_id, module_id)
            except Exception:
                log.exception("modules.processing.release_failed", log_id=log_id, module=module_id)

    async def _fail_waiters(
        self, user_id: str, log_id: str, failed_module: str, error: str
    ) -> None:
        """A module that was waiting on `failed_module` can never run - say so."""
        async with self._sessionmaker() as session:
            owned = await user_module_ids(session, user_id)
        for module_id in await self._waiting_modules(log_id):
            if failed_module not in self._loader.precompute_deps(module_id, owned):
                continue
            try:
                async with self._locked(log_id, module_id):
                    read = await self._read(user_id, log_id, module_id)
                    if read.row is None or not read.row.waiting:
                        continue
                    await self._park_failed(
                        user_id,
                        log_id,
                        module_id,
                        read,
                        f"{failed_module} did not complete: {error}",
                    )
            except Exception:
                log.exception("modules.processing.fail_waiter_failed", log_id=log_id)

    # -- reads for the API --------------------------------------------------

    async def list_states(self, user_id: str, log_id: str) -> list[RunState]:
        """State of every module the user has that precomputes, for one log."""
        async with self._sessionmaker() as session:
            log_row = await session.get(EventLog, log_id)
            if log_row is None or log_row.deleted_at is not None or log_row.user_id != user_id:
                raise LookupError(f"Event log {log_id!r} not found.")
            owned = await user_module_ids(session, user_id)
            modes = await self._modes(session)
            rows = {
                r.module_id: _Row.of(r)
                for r in (
                    await session.execute(select(LogModuleRun).where(LogModuleRun.log_id == log_id))
                )
                .scalars()
                .all()
            }
            avail = self._loader.availability_for(
                detected_schema=log_row.detected_schema,
                events_count=log_row.events_count,
                cases_count=log_row.cases_count,
                installed_module_ids=owned,
                log_model=log_row.log_model,
            )
            revision = int(log_row.data_revision or 0)
        out: list[RunState] = []
        for mid in sorted(owned):
            if mid not in self._loader.loaded or not self._loader.has_precompute(mid):
                continue
            a = avail.get(mid)
            if a is not None and a.status == "unavailable":
                out.append(
                    RunState(
                        module_id=mid,
                        status="unavailable",
                        precompute=modes.get(mid),
                        error="; ".join(a.reasons) or None,
                    )
                )
                continue
            row = rows.get(mid)
            out.append(
                RunState(
                    module_id=mid,
                    status=_derive(row, revision, self._version(mid)),
                    job_id=row.job_id if row is not None else None,
                    precompute=modes.get(mid),
                    error=row.error if row is not None else None,
                    finished_at=row.finished_at if row is not None else None,
                )
            )
        return out

    # -- import-job plan (what the jobs drawer shows) ------------------------

    async def precompute_plan(
        self,
        topic: str,
        user_id: str,
        session: AsyncSession,
        *,
        log_id: str,
        log_model: str,
        detected_schema: dict[str, Any] | None,
        events_count: int | None,
        cases_count: int | None,
    ) -> tuple[set[str], list[dict[str, Any]]]:
        """``(nodes, plan)`` for an import on ``topic``: the modules that will run.

        Lazy processing narrows this to the modules :meth:`dispatch` will actually
        start for the import - eager ones plus those already used on the log (a
        reimport) - and anything chained off them that the log has used. ``plan`` is
        ``[{"id", "after": [...]}]`` in execution order for the jobs drawer's
        checklist; roots carry an empty ``after``.
        """
        owned = await user_module_ids(session, user_id)
        modes = await self._modes(session)
        avail = self._loader.availability_for(
            detected_schema=detected_schema,
            events_count=events_count,
            cases_count=cases_count,
            installed_module_ids=owned,
            log_model=log_model,
        )
        closure, edges = self._loader.precompute_closure(topic, owned)
        roots = self._loader.precompute_subscriber_module_ids(topic) & owned
        used = {
            mid
            for (mid,) in (
                await session.execute(
                    select(LogModuleRun.module_id).where(
                        LogModuleRun.log_id == log_id,
                        LogModuleRun.status.in_(_WORKING_SET),
                    )
                )
            ).all()
        }
        nodes: set[str] = set()
        for mid in roots:
            a = avail.get(mid)
            if a is not None and a.status == "unavailable":
                continue
            if not await self._enabled(session, user_id, mid):
                continue
            if _auto_run(mode=modes.get(mid), in_working_set=mid in used, allow_eager=True):
                nodes.add(mid)
        # Chained consumers run only when something upstream does and the log used them.
        changed = True
        while changed:
            changed = False
            for mid in closure - roots - nodes:
                if mid in used and edges.get(mid, set()) & nodes:
                    nodes.add(mid)
                    changed = True
        after = {
            mid: ([] if mid in roots else sorted(edges.get(mid, set()) & nodes)) for mid in nodes
        }
        plan = [{"id": mid, "after": after[mid]} for mid in self._topo_order(after)]
        return nodes, plan

    @staticmethod
    def _topo_order(after: dict[str, list[str]]) -> list[str]:
        """Module ids in dependency order; alphabetical within a layer.

        Kahn's algorithm over the same ``after`` lists the plan publishes. The
        per-layer ``sorted()`` makes the output byte-stable across imports (dict
        iteration order would otherwise leak insertion order into a persisted
        artefact). A cycle - impossible for a `provides`/`consumes` graph the
        loader validated, but cheap to survive - leaves its members unemitted, so
        they are appended rather than silently dropped.
        """
        pending = {mid: set(deps) for mid, deps in after.items()}
        emitted: list[str] = []
        done: set[str] = set()
        while True:
            layer = sorted(mid for mid, deps in pending.items() if deps <= done)
            if not layer:
                break
            for mid in layer:
                emitted.append(mid)
                done.add(mid)
                del pending[mid]
        emitted.extend(sorted(pending))
        return emitted

    # -- boot ---------------------------------------------------------------

    async def reconcile_boot(self) -> None:
        """Settle runs a previous process left in flight.

        Runs after the job runtime has requeued its resumable work. A run whose
        job finished while the API was down takes that job's outcome; one whose job
        was lost (never re-enqueued, killed with its worker, or pruned) is restarted
        once - it was asked for before the restart. Import children the runtime
        resumed are left alone: their terminal event will settle them.
        """
        async with self._sessionmaker() as session:
            stuck = (
                (
                    await session.execute(
                        select(LogModuleRun).where(LogModuleRun.status.in_(_IN_FLIGHT))
                    )
                )
                .scalars()
                .all()
            )
            targets = [
                (r.user_id, r.log_id, r.module_id, r.job_id, _Row.of(r).waiting) for r in stuck
            ]
        for user_id, log_id, module_id, job_id, waiting in targets:
            try:
                if module_id not in self._loader.loaded:
                    continue
                if waiting:
                    await self._ensure_one(user_id, log_id, module_id)
                    continue
                if job_id is not None and await self._settle_from_job(
                    user_id, log_id, module_id, job_id
                ):
                    continue
                await self._ensure_one(user_id, log_id, module_id, restart=True)
            except Exception:
                log.exception(
                    "modules.processing.reconcile_failed", log_id=log_id, module=module_id
                )

    async def _settle_from_job(
        self, user_id: str, log_id: str, module_id: str, job_id: str
    ) -> bool:
        """Apply a finished job's outcome to its run. ``True`` when nothing more is needed.

        A job that is still live (queued under an import the runtime resumed) counts
        as handled; one the crash killed, or that no longer exists, does not.
        """
        async with self._sessionmaker() as session:
            job = await session.get(Job, job_id)
            if job is None:
                return False
            if job.status in _IN_FLIGHT:
                parent = await session.get(Job, job.parent_job_id) if job.parent_job_id else None
                return parent is not None and parent.type == "event_log.import"
            if job.status == "failed" and (job.error or "") == _WORKER_LOST:
                return False
            parent_job_id = job.parent_job_id
        await self._settle(job_id, user_id, log_id, module_id, parent_job_id)
        return True


_coordinator: ModuleProcessingCoordinator | None = None


def get_coordinator() -> ModuleProcessingCoordinator | None:
    """The process-global coordinator, or ``None`` before startup wires it.

    Returns ``None`` rather than raising so call sites that only *notify* it (a cell
    edit asking for a debounced refresh, the import handler asking for its plan)
    degrade to doing nothing in a bare-runtime test instead of failing.
    """
    return _coordinator


def set_coordinator(coordinator: ModuleProcessingCoordinator | None) -> None:
    global _coordinator
    _coordinator = coordinator
