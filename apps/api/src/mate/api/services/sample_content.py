"""Seed sample event logs and a starter dashboard into listed accounts.

Some accounts, such as the anonymous conference-reviewer ones, should find
ready-to-explore content on their first login: a few sample logs with the
default modules already computed, and one example dashboard. This module
seeds that content in-process, at API start, through the same paths a user's
own upload takes. The import job, ``log.imported``, the eager discovery
precompute and the per-(log, module) run rows all behave exactly as they would
for a browser upload. That rules out a separate CLI process, which would share
neither the job runtime nor the event bus.

The manifest is a JSON file named by ``SAMPLE_CONTENT_MANIFEST``.
``infra/keycloak/create-reviewer-accounts.sh`` writes one::

    {
      "tracking_exempt": true,
      "users": [{"id": "<keycloak user id>", "username": "reviewer1"}],
      "logs": [{"file": "helpdesk.csv", "name": "Helpdesk (sample)",
                "csv_mapping": {"case_id": "case_id", ...}}],
      "dashboard": {"name": "Example dashboard",
                    "template_id": "process-overview", "log": 0}
    }

``file`` is resolved relative to the manifest and must stay inside its folder.
``users[].id`` is the Keycloak ``sub`` (the platform's user id), so the content
is already in place before the first login.

What it does:

- Each account is seeded once. A ``sample_content.seeded`` user setting
  records it, and later starts skip the account, so a reviewer who deletes a
  sample log does not get it back.
- Inside one run, an existing log or dashboard with the manifest's name is
  reused instead of duplicated, so a run cut short by a restart resumes
  cleanly.
- With ``tracking_exempt`` the account gets the persisted tracking-exemption
  flag *before* any job runs for it, so not even the seeding jobs are recorded.
- It never raises into the app. Every failure is logged and the account is
  retried at the next start.
"""

from __future__ import annotations

import asyncio
import json
import shutil
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import structlog
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from mate.api.config import get_settings
from mate.api.dashboard_templates import get_template
from mate.api.db.models import Dashboard, EventLog, Job, User, UserSetting
from mate.api.ingest.detect import detect_format, original_extension, sniff_format
from mate.api.ingest.dispatch import IMPORT_JOB_TYPE
from mate.api.ingest.storage import log_paths
from mate.api.jobs.runtime import JobRuntime
from mate.api.modules import ModuleLoader
from mate.api.modules.installs import user_module_ids
from mate.api.modules.processing import ModuleProcessingCoordinator
from mate.api.routes.dashboards import layout_blob
from mate.api.routes.modules import reconcile_user_default_modules
from mate.api.schemas.event_logs import CsvColumnMapping
from mate.api.services.log_aggregates import delete_log_and_data
from mate.api.services.tracking_exemption import invalidate_tracking_cache, mark_tracking_exempt
from mate.api.uuid7 import uuid7_str

log = structlog.get_logger(__name__)

SEEDED_KEY = "sample_content.seeded"
# Upper bound for one account's imports. Failure is detected from the import job
# (failed / cancelled), so this only catches a job that hangs: the queue is shared
# and first-in-first-out, so imports may wait behind other users' long jobs.
IMPORT_TIMEOUT_SECONDS = 4 * 3600.0
_POLL_SECONDS = 1.0


class SampleUser(BaseModel):
    id: str = Field(min_length=1, max_length=36)
    username: str | None = None


class SampleLog(BaseModel):
    file: str
    name: str
    csv_mapping: CsvColumnMapping | None = None


class SampleDashboard(BaseModel):
    name: str
    template_id: str = "process-overview"
    # Index into ``logs`` of the log the board is bound to.
    log: int = 0
    description: str | None = None


class SampleManifest(BaseModel):
    users: list[SampleUser]
    logs: list[SampleLog] = Field(default_factory=list)
    dashboard: SampleDashboard | None = None
    tracking_exempt: bool = False
    # Modules whose precompute is started for every sample log once all accounts
    # have their logs. ``None`` = the dashboard's modules. Kept short on purpose:
    # the job queue is first-in-first-out and shared with real users, and heavy
    # modules (a TensorFlow drift detector, …) still compute on first open.
    warm_modules: list[str] | None = None


def load_manifest(path: Path) -> SampleManifest:
    manifest = SampleManifest.model_validate(json.loads(path.read_text(encoding="utf-8")))
    root = path.parent.resolve()
    for entry in manifest.logs:
        source = (root / entry.file).resolve()
        if not source.is_relative_to(root):
            raise ValueError(f"sample log {entry.file!r} points outside {root}")
        if not source.is_file():
            raise ValueError(f"sample log {entry.file!r} not found in {root}")
    if manifest.dashboard is not None and not 0 <= manifest.dashboard.log < len(manifest.logs):
        raise ValueError("dashboard.log does not index an entry of logs")
    return manifest


async def seed_from_settings(
    sessionmaker: async_sessionmaker[AsyncSession],
    runtime: JobRuntime,
    loader: ModuleLoader,
    coordinator: ModuleProcessingCoordinator,
) -> None:
    """Seed every account in ``SAMPLE_CONTENT_MANIFEST``; a no-op when unset."""
    raw = get_settings().sample_content_manifest.strip()
    if not raw:
        return
    path = Path(raw)
    if not path.exists():
        # Removed on purpose after the review (create-reviewer-accounts.sh delete).
        log.info("sample_content.manifest_absent", path=str(path))
        return
    try:
        manifest = await asyncio.to_thread(load_manifest, path)
    except Exception:
        log.exception("sample_content.manifest_invalid", path=str(path))
        return
    # Phase 1 for every account (logs + dashboard), phase 2 (module warm-up)
    # only afterwards: warm-up jobs queued between two accounts' imports would
    # hold the later imports back behind them.
    seeded: list[tuple[SampleUser, list[str]]] = []
    for user in manifest.users:
        try:
            log_ids = await seed_user(sessionmaker, runtime, loader, manifest, path.parent, user)
        except asyncio.CancelledError:
            raise
        except Exception:
            log.exception("sample_content.seed_failed", user_id=user.id)
            continue
        if log_ids is not None:
            seeded.append((user, log_ids))
    for user, log_ids in seeded:
        await warm_modules(sessionmaker, coordinator, manifest, user.id, log_ids)


async def seed_user(
    sessionmaker: async_sessionmaker[AsyncSession],
    runtime: JobRuntime,
    loader: ModuleLoader,
    manifest: SampleManifest,
    root: Path,
    user: SampleUser,
) -> list[str] | None:
    """Import the sample logs and create the dashboard for one account.

    Returns the account's sample log ids, or ``None`` when it was already seeded.
    The eager modules (discovery) run as part of the import; the rest of the
    warm-up is :func:`warm_modules`.
    """
    async with sessionmaker() as session:
        if await session.get(UserSetting, (user.id, SEEDED_KEY)) is not None:
            return None
        if await session.get(User, user.id) is None:
            session.add(User(id=user.id, preferred_username=user.username))
            await session.flush()
        if manifest.tracking_exempt:
            await mark_tracking_exempt(session, user.id, source="manifest")
        # Install the default modules now, not on the first visit: the import's
        # precompute plan only covers modules the account already has.
        await reconcile_user_default_modules(session, loader, user.id)
        await session.commit()
    # Only after the commit: an earlier invalidation would let a recorder re-cache
    # the pre-flag state.
    invalidate_tracking_cache(user.id)
    get_settings().ensure_user_dirs(user.id)

    imports = [
        await _import_log(sessionmaker, runtime, root, user.id, entry) for entry in manifest.logs
    ]
    await _wait_ready(sessionmaker, imports)
    log_ids = [log_id for log_id, _job in imports]

    async with sessionmaker() as session:
        if manifest.dashboard is not None:
            await _create_dashboard(
                session, user.id, manifest.dashboard, log_ids[manifest.dashboard.log]
            )
        session.add(
            UserSetting(
                user_id=user.id,
                key=SEEDED_KEY,
                value_json={
                    "at": datetime.now(UTC).isoformat(),
                    "logs": log_ids,
                },
            )
        )
        await session.commit()

    log.info("sample_content.seeded", user_id=user.id, logs=len(log_ids))
    return log_ids


async def warm_modules(
    sessionmaker: async_sessionmaker[AsyncSession],
    coordinator: ModuleProcessingCoordinator,
    manifest: SampleManifest,
    user_id: str,
    log_ids: list[str],
) -> None:
    """Start the precompute of the manifest's warm-up modules on every sample log.

    Only queues the runs (the coordinator tracks them); a module the account does
    not have, or one with nothing to precompute, is skipped.
    """
    wanted = set(manifest.warm_modules or [])
    if manifest.warm_modules is None and manifest.dashboard is not None:
        template = get_template(manifest.dashboard.template_id)
        if template is not None:
            wanted = {it.module_id for it in template.items if it.kind == "widget" and it.module_id}
    async with sessionmaker() as session:
        owned = await user_module_ids(session, user_id)
    for log_id in log_ids:
        for module_id in sorted(wanted & owned):
            try:
                await coordinator.ensure(user_id, log_id, module_id)
            except LookupError:
                log.info("sample_content.module_unavailable", module_id=module_id)
            except asyncio.CancelledError:
                raise
            except Exception:
                log.exception("sample_content.warm_failed", user_id=user_id, module_id=module_id)


async def _import_log(
    sessionmaker: async_sessionmaker[AsyncSession],
    runtime: JobRuntime,
    root: Path,
    user_id: str,
    entry: SampleLog,
) -> tuple[str, str | None]:
    """Submit an import for ``entry``; returns ``(log_id, job_id)``.

    A ready same-named log from an earlier, interrupted run is reused (job
    ``None``). A same-named log that never became ready is a dead import: imports
    are not resumed after a restart, and a failed one keeps ``importing``, so it is
    deleted and imported afresh rather than waited on forever.
    """
    async with sessionmaker() as session:
        same_name = (
            await session.scalars(
                select(EventLog).where(
                    EventLog.user_id == user_id,
                    EventLog.name == entry.name,
                    EventLog.deleted_at.is_(None),
                )
            )
        ).all()
        for row in same_name:
            if row.status == "ready":
                return row.id, None
        for row in same_name:
            log.info("sample_content.dead_import_removed", log_id=row.id, status=row.status)
            await delete_log_and_data(session, runtime, row, user_id)

        source = (root / entry.file).resolve()
        filename = source.name
        coarse = detect_format(filename)
        log_id = uuid7_str()
        paths = log_paths(log_id, user_id)
        paths.ensure()
        original = paths.original_for(original_extension(filename, coarse))
        try:
            await asyncio.to_thread(shutil.copyfile, source, original)
            source_format, ocel_flavor = await asyncio.to_thread(
                sniff_format, original, coarse, filename=filename
            )
        except Exception:
            await asyncio.to_thread(paths.remove)
            raise
        session.add(
            EventLog(
                id=log_id,
                user_id=user_id,
                name=entry.name,
                source_format=source_format,
                source_filename=filename,
                status="importing",
                created_at=datetime.now(UTC).replace(tzinfo=None),
            )
        )
        await session.commit()

    payload: dict[str, Any] = {
        "log_id": log_id,
        "source_format": source_format,
        "ocel_flavor": ocel_flavor,
        "original_path": str(original),
        "csv_mapping": entry.csv_mapping.model_dump() if entry.csv_mapping else None,
        "xml_mapping": None,
        "json_mapping": None,
        "column_roles": None,
    }
    job_id = await runtime.submit(
        type_=IMPORT_JOB_TYPE,
        user_id=user_id,
        title=f"Import - {entry.name}",
        subtitle=f"event_log.import · {source_format}",
        payload=payload,
    )
    return log_id, job_id


_JOB_DEAD = frozenset({"failed", "cancelled"})


async def _wait_ready(
    sessionmaker: async_sessionmaker[AsyncSession], imports: list[tuple[str, str | None]]
) -> None:
    """Wait until every log is ``ready``; raise when its import job died.

    The import handler never sets the log to ``failed`` - a failed import leaves
    the log ``importing`` and only the job row says so - so the job is what is
    watched for failure.
    """
    loop = asyncio.get_running_loop()
    deadline = loop.time() + IMPORT_TIMEOUT_SECONDS
    pending = dict(imports)
    while pending:
        async with sessionmaker() as session:
            rows = await session.execute(
                select(EventLog.id, EventLog.status).where(EventLog.id.in_(list(pending)))
            )
            for log_id, status in rows.all():
                if status == "ready":
                    pending.pop(log_id, None)
            job_ids = [j for j in pending.values() if j is not None]
            if job_ids:
                dead = await session.execute(
                    select(Job.id, Job.status).where(Job.id.in_(job_ids), Job.status.in_(_JOB_DEAD))
                )
                for job_id, status in dead.all():
                    raise RuntimeError(f"sample log import job {job_id} ended as {status!r}")
        if not pending:
            return
        if loop.time() > deadline:
            raise TimeoutError(f"sample log import timed out: {sorted(pending)}")
        await asyncio.sleep(_POLL_SECONDS)


async def _create_dashboard(
    session: AsyncSession, user_id: str, spec: SampleDashboard, log_id: str
) -> None:
    """Create the starter board, unless one with that name exists."""
    template = get_template(spec.template_id)
    if template is None:
        raise ValueError(f"unknown dashboard template {spec.template_id!r}")
    exists = await session.scalar(
        select(Dashboard.id)
        .where(Dashboard.user_id == user_id, Dashboard.name == spec.name)
        .limit(1)
    )
    if exists is None:
        session.add(
            Dashboard(
                id=uuid7_str(),
                user_id=user_id,
                name=spec.name,
                description=spec.description or template.description,
                event_log_id=log_id,
                log_model=template.log_model,
                layout_json=layout_blob(template.items, template.settings),
                created_at=datetime.now(UTC).replace(tzinfo=None),
            )
        )
        await session.flush()
