"""Who may curate and who may read a dataset.

Readers who may not see a dataset get 404, never 403, so a slug's existence is
not disclosed. The same predicate exists twice - :func:`can_read_dataset` for one
row and :func:`readable_clause` as a SQL ``WHERE`` for the list query - and the
visibility-matrix test pins them to each other.
"""

from __future__ import annotations

from typing import Annotated

from fastapi import Depends, HTTPException
from sqlalchemy import ColumnElement, and_, false, or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from mate.api.auth.dependencies import (
    CurrentUser,
    CurrentUserDep,
    is_sandboxed,
    may_curate_datasets,
)
from mate.api.auth.sandbox_flag import user_is_sandboxed
from mate.api.config import get_settings
from mate.api.db.models import HubDataset, Team, TeamMember
from mate.api.hub.licenses import OPEN_LICENSE_IDS

NOT_FOUND = "Dataset not found."


async def require_curator(user: CurrentUserDep) -> CurrentUser:
    if not may_curate_datasets(user.roles):
        raise HTTPException(status_code=403, detail="Dataset curation requires the admin role.")
    return user


CuratorDep = Annotated[CurrentUser, Depends(require_curator)]


async def require_hub_enabled() -> None:
    """Router-level dependency: the hub routes 404 while the flag is off."""
    if not get_settings().hub_datasets_enabled:
        raise HTTPException(status_code=404, detail="Not Found")


async def live_team_ids(session: AsyncSession, user_id: str) -> set[str]:
    """Teams the user belongs to, soft-deleted teams excluded.

    ``sharing.user_team_ids`` keeps soft-deleted teams; a restricted dataset must
    not stay readable through a team an admin deleted.
    """
    rows = await session.execute(
        select(TeamMember.team_id)
        .join(Team, Team.id == TeamMember.team_id)
        .where(TeamMember.user_id == user_id, Team.deleted_at.is_(None))
    )
    return {r[0] for r in rows}


async def team_is_live(session: AsyncSession, team_id: str | None) -> bool:
    if not team_id:
        return False
    team = await session.get(Team, team_id)
    return team is not None and team.deleted_at is None


async def can_read_dataset(session: AsyncSession, ds: HubDataset, user: CurrentUser) -> bool:
    if may_curate_datasets(user.roles):
        return True
    if ds.status != "active":
        return False
    if is_sandboxed(user.roles):
        return ds.visibility == "public" and ds.license_id in OPEN_LICENSE_IDS
    if ds.visibility == "public":
        return True
    if ds.visibility == "restricted":
        if not ds.audience_team_id:
            return False
        return ds.audience_team_id in await live_team_ids(session, user.id)
    return False


def readable_clause(user: CurrentUser, team_ids: set[str]) -> ColumnElement[bool] | None:
    """:func:`can_read_dataset` as a ``WHERE`` clause; ``None`` = no filter (curators)."""
    if may_curate_datasets(user.roles):
        return None
    active = HubDataset.status == "active"
    if is_sandboxed(user.roles):
        return and_(
            active,
            HubDataset.visibility == "public",
            HubDataset.license_id.in_(sorted(OPEN_LICENSE_IDS)),
        )
    restricted = (
        and_(
            HubDataset.visibility == "restricted",
            HubDataset.audience_team_id.in_(sorted(team_ids)),
        )
        if team_ids
        else false()
    )
    return and_(active, or_(HubDataset.visibility == "public", restricted))


async def recipient_may_read_derived_log(
    session: AsyncSession, dataset_id: str | None, user_id: str
) -> bool:
    """May a share *recipient* read a log derived from ``dataset_id``?

    Used by ``mate.api.sharing`` (only the user id is known there, no roles):
    public → yes; restricted → a live member of the audience team, so removing
    someone from the team also ends their access through a shared dashboard;
    private (curators only) → no. A sandboxed recipient (persisted flag, see
    ``auth.sandbox_flag``) gets exactly what :func:`can_read_dataset` gives it:
    public datasets under an open license only.
    """
    if dataset_id is None:
        return False
    ds = await session.get(HubDataset, dataset_id)
    if ds is None:
        return False
    if await user_is_sandboxed(session, user_id):
        return ds.visibility == "public" and ds.license_id in OPEN_LICENSE_IDS
    if ds.visibility == "public":
        return True
    if ds.visibility == "restricted" and ds.audience_team_id:
        return ds.audience_team_id in await live_team_ids(session, user_id)
    return False


async def get_readable_dataset(
    session: AsyncSession, namespace: str, name: str, user: CurrentUser
) -> HubDataset:
    ds = (
        await session.execute(
            select(HubDataset).where(HubDataset.namespace == namespace, HubDataset.name == name)
        )
    ).scalar_one_or_none()
    if ds is None or not await can_read_dataset(session, ds, user):
        raise HTTPException(status_code=404, detail=NOT_FOUND)
    return ds
