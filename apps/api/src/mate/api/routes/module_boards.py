"""/api/v1/event-logs/{log_id}/modules/{module_id}/board - a panel-less module's page.

A module that declares ``frontend.board`` renders its page as a dashboard: one
``Dashboard`` row per (user, module, log), tagged with ``module_id`` and created
from the manifest's items the first time it is read. It is edited and saved like
any dashboard (``PATCH /dashboards/{id}``), but it never leaves its (module, log)
and stays out of the dashboard list, sharing and export.
"""

from __future__ import annotations

import structlog
from fastapi import APIRouter, HTTPException
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from mate.api.auth import CurrentUserDep, get_owned_event_log
from mate.api.db.models import Dashboard, EventLog
from mate.api.db.session import SessionDep
from mate.api.modules import get_module_loader
from mate.api.modules.installs import user_owns_module
from mate.api.routes.dashboards import dashboard_detail, dashboard_settings, layout_blob
from mate.api.schemas.dashboards import CanvasSettings, DashboardDetail, DashboardItem, DatasetRef
from mate.api.uuid7 import uuid7_str
from mate.sdk.manifest import Manifest

log = structlog.get_logger(__name__)

router = APIRouter(prefix="/event-logs/{log_id}/modules/{module_id}/board", tags=["dashboards"])


def seed_items(manifest: Manifest) -> list[DashboardItem]:
    """The manifest's ``frontend.board`` items as ``viz`` dashboard cards."""
    board = manifest.frontend.board
    return [
        DashboardItem(
            i=it.id,
            kind="viz",
            dataset_ref=DatasetRef(module_id=manifest.id, dataset_id=it.dataset),
            viz_id=it.viz,
            mapping=it.mapping,
            title=it.title,
            x=it.x,
            y=it.y,
            w=it.w,
            h=it.h,
            config=it.options,
        )
        for it in (board.items if board else [])
    ]


async def _resolve(
    session: AsyncSession, log_id: str, module_id: str, user_id: str
) -> tuple[EventLog, Manifest]:
    """404 unless the user owns the log and a loaded install of a module with a board."""
    log_row = await get_owned_event_log(session, log_id, user_id)
    loaded = get_module_loader().loaded.get(module_id)
    if loaded is None or not await user_owns_module(session, user_id, module_id):
        raise HTTPException(status_code=404, detail=f"Module {module_id!r} is not installed.")
    if loaded.manifest.frontend.board is None:
        raise HTTPException(status_code=404, detail=f"Module {module_id!r} has no board.")
    return log_row, loaded.manifest


async def _board(
    session: AsyncSession, log_row: EventLog, manifest: Manifest, user_id: str
) -> Dashboard:
    """The user's board for (module, log), seeded from the manifest on first use."""
    stmt = select(Dashboard).where(
        Dashboard.user_id == user_id,
        Dashboard.module_id == manifest.id,
        Dashboard.event_log_id == log_row.id,
    )
    row = (await session.execute(stmt)).scalar_one_or_none()
    if row is not None:
        return row
    row = Dashboard(
        id=uuid7_str(),
        user_id=user_id,
        name=manifest.name,
        event_log_id=log_row.id,
        log_model=log_row.log_model,
        module_id=manifest.id,
        layout_json=layout_blob(seed_items(manifest), CanvasSettings()),
    )
    session.add(row)
    try:
        await session.commit()
    except IntegrityError:
        # A concurrent first read seeded it (unique user/module/log index) - use that one.
        await session.rollback()
        return (await session.execute(stmt)).scalar_one()
    log.info("module_board.seeded", dashboard_id=row.id, module_id=manifest.id)
    return row


@router.get("", response_model=DashboardDetail)
async def get_module_board(
    log_id: str, module_id: str, session: SessionDep, user: CurrentUserDep
) -> DashboardDetail:
    """The user's board for this module + log, created from the manifest on first read."""
    log_row, manifest = await _resolve(session, log_id, module_id, user.id)
    return dashboard_detail(await _board(session, log_row, manifest, user.id))


@router.post("/reset", response_model=DashboardDetail)
async def reset_module_board(
    log_id: str, module_id: str, session: SessionDep, user: CurrentUserDep
) -> DashboardDetail:
    """Put the manifest's items back. Board settings (filters, presets) are kept."""
    log_row, manifest = await _resolve(session, log_id, module_id, user.id)
    row = await _board(session, log_row, manifest, user.id)
    row.layout_json = layout_blob(seed_items(manifest), dashboard_settings(row))
    await session.commit()
    return dashboard_detail(row)
