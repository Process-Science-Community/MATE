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
from mate.api.db.models import Dashboard, EventLog, User, UserSetting
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
from mate.api.services.tracking_exemption import mark_tracking_exempt
from mate.api.uuid7 import uuid7_str

log = structlog.get_logger(__name__)

SEEDED_KEY = "sample_content.seeded"
# How long one account's imports may take before the run gives up (and retries
# at the next start). The bundled samples import in seconds.
IMPORT_TIMEOUT_SECONDS = 600.0
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
    try:
        manifest = await asyncio.to_thread(load_manifest, path)
    except Exception:
        log.exception("sample_content.manifest_invalid", path=str(path))
        return
    for user in manifest.users:
        try:
            await seed_user(sessionmaker, runtime, loader, coordinator, manifest, path.parent, user)
        except asyncio.CancelledError:
            raise
        except Exception:
            log.exception("sample_content.seed_failed", user_id=user.id)


async def seed_user(
    sessionmaker: async_sessionmaker[AsyncSession],
    runtime: JobRuntime,
    loader: ModuleLoader,
    coordinator: ModuleProcessingCoordinator,
    manifest: SampleManifest,
    root: Path,
    user: SampleUser,
) -> bool:
    """Seed one account. Returns False when it was already seeded."""
    async with sessionmaker() as session:
        if await session.get(UserSetting, (user.id, SEEDED_KEY)) is not None:
            return False
        if await session.get(User, user.id) is None:
            session.add(User(id=user.id, preferred_username=user.username))
            await session.flush()
        if manifest.tracking_exempt:
            await mark_tracking_exempt(session, user.id, source="manifest")
        # Install the default modules now, not on the first visit: the import's
        # precompute plan only covers modules the account already has.
        await reconcile_user_default_modules(session, loader, user.id)
        await session.commit()
    get_settings().ensure_user_dirs(user.id)

    log_ids: list[str] = []
    for entry in manifest.logs:
        log_ids.append(await _import_log(sessionmaker, runtime, root, user.id, entry))
    await _wait_ready(sessionmaker, log_ids)

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

    # Eager modules (discovery) already ran on import. Warm every other installed
    # module that has a precompute too (e.g. a lazy one such as complexity), so
    # module pages and dashboard cards are ready on the first visit. A module with
    # nothing to precompute returns no runs, which is fine.
    async with sessionmaker() as session:
        module_ids = await user_module_ids(session, user.id)
    for log_id in log_ids:
        for module_id in sorted(module_ids):
            try:
                await coordinator.ensure(user.id, log_id, module_id)
            except LookupError:
                log.info("sample_content.module_unavailable", module_id=module_id)
    log.info("sample_content.seeded", user_id=user.id, logs=len(log_ids))
    return True


async def _import_log(
    sessionmaker: async_sessionmaker[AsyncSession],
    runtime: JobRuntime,
    root: Path,
    user_id: str,
    entry: SampleLog,
) -> str:
    """Submit an import for ``entry`` (or reuse an existing same-named log)."""
    async with sessionmaker() as session:
        existing = await session.scalar(
            select(EventLog.id)
            .where(EventLog.user_id == user_id, EventLog.name == entry.name)
            .where(EventLog.status != "failed")
            .limit(1)
        )
        if existing is not None:
            return existing

        source = (root / entry.file).resolve()
        filename = source.name
        coarse = detect_format(filename)
        log_id = uuid7_str()
        paths = log_paths(log_id, user_id)
        paths.ensure()
        original = paths.original_for(original_extension(filename, coarse))
        await asyncio.to_thread(shutil.copyfile, source, original)
        source_format, ocel_flavor = await asyncio.to_thread(
            sniff_format, original, coarse, filename=filename
        )
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
    await runtime.submit(
        type_=IMPORT_JOB_TYPE,
        user_id=user_id,
        title=f"Import - {entry.name}",
        subtitle=f"event_log.import · {source_format}",
        payload=payload,
    )
    return log_id


async def _wait_ready(sessionmaker: async_sessionmaker[AsyncSession], log_ids: list[str]) -> None:
    loop = asyncio.get_running_loop()
    deadline = loop.time() + IMPORT_TIMEOUT_SECONDS
    pending = set(log_ids)
    while pending:
        async with sessionmaker() as session:
            rows = await session.execute(
                select(EventLog.id, EventLog.status).where(EventLog.id.in_(pending))
            )
            for log_id, status in rows.all():
                if status == "ready":
                    pending.discard(log_id)
                elif status == "failed":
                    raise RuntimeError(f"sample log {log_id} import ended as {status!r}")
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
