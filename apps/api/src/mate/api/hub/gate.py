"""Takedown (410) and external-AI egress gates for logs derived from a dataset.

Every check is a no-op (and issues no query) for an ordinary log: only a row with
``dataset_revision_id`` set is looked up. The gates stay active when
``HUB_DATASETS_ENABLED`` is off - a derived log stays governed by its dataset.

No hub service imports here: ``mate.api.auth.ownership`` and the module loader
import this module.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from typing import Any

from fastapi import HTTPException
from sqlalchemy import ColumnElement, exists, or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from mate.api.db.models import EventLog, HubDataset, HubDatasetRevision

WITHDRAWN_DETAIL = "This dataset has been withdrawn by an administrator."
NO_EGRESS_DETAIL = (
    "The license of this dataset does not allow sending its data to external AI providers."
)


@dataclass(frozen=True)
class Lineage:
    dataset: HubDataset | None
    revision: HubDatasetRevision | None
    snapshot: dict[str, Any]

    @property
    def withdrawn(self) -> bool:
        """Disabled dataset or purged revision. A lineage whose dataset row is
        gone counts as withdrawn too (the data can no longer be vouched for)."""
        if self.dataset is None or self.revision is None:
            return True
        return self.dataset.status != "active" or self.revision.purged_at is not None

    @property
    def status(self) -> str:
        if self.revision is not None and self.revision.purged_at is not None:
            return "purged"
        if self.dataset is None or self.dataset.status != "active":
            return "disabled"
        return "active"

    @property
    def allow_external_ai(self) -> bool:
        if self.dataset is not None:
            return bool(self.dataset.allow_external_ai)
        return bool(self.snapshot.get("allow_external_ai", True))


def _snapshot(row: EventLog) -> dict[str, Any]:
    snap = row.license_snapshot
    return dict(snap) if isinstance(snap, dict) else {}


async def lineage_for(session: AsyncSession, row: EventLog) -> Lineage | None:
    """The dataset a log was copied from, or ``None`` for an ordinary log."""
    if row.dataset_revision_id is None:
        return None
    found = (
        await session.execute(
            select(HubDatasetRevision, HubDataset)
            .join(HubDataset, HubDataset.id == HubDatasetRevision.dataset_id)
            .where(HubDatasetRevision.id == row.dataset_revision_id)
        )
    ).first()
    if found is None:
        return Lineage(dataset=None, revision=None, snapshot=_snapshot(row))
    rev, ds = found
    return Lineage(dataset=ds, revision=rev, snapshot=_snapshot(row))


async def ensure_log_usable(session: AsyncSession, row: EventLog) -> None:
    """410 when the log's dataset was disabled or its revision purged."""
    lineage = await lineage_for(session, row)
    if lineage is not None and lineage.withdrawn:
        raise HTTPException(status_code=410, detail=WITHDRAWN_DETAIL)


async def external_ai_allowed(session: AsyncSession, row: EventLog) -> bool:
    """Whether this log's data may be sent to an external AI provider / MCP client.

    True for ordinary logs. For a derived log the dataset's *live* flag decides,
    so an admin change applies to existing copies; the use-time snapshot is the
    fallback when the dataset row is gone.
    """
    lineage = await lineage_for(session, row)
    if lineage is None:
        return True
    return lineage.allow_external_ai


async def ensure_external_ai_allowed(session: AsyncSession, row: EventLog) -> None:
    if not await external_ai_allowed(session, row):
        raise HTTPException(status_code=403, detail=NO_EGRESS_DETAIL)


def egress_listable_clause() -> ColumnElement[bool]:
    """SQL ``WHERE`` term: the log may be *listed* to an external AI client.

    An ordinary log, or a copy of an active, unpurged dataset that allows external
    AI. A no-egress or withdrawn copy never reaches the client, not even its name
    or counts (same rule as ``ai_nav.list_user_processes``). Correlated
    ``EXISTS`` - portable to Postgres and SQLite.
    """
    allowed = exists(
        select(HubDatasetRevision.id)
        .join(HubDataset, HubDataset.id == HubDatasetRevision.dataset_id)
        .where(
            HubDatasetRevision.id == EventLog.dataset_revision_id,
            HubDatasetRevision.purged_at.is_(None),
            HubDataset.status == "active",
            HubDataset.allow_external_ai.is_(True),
        )
        .correlate(EventLog)
    )
    return or_(EventLog.dataset_revision_id.is_(None), allowed)


@dataclass(frozen=True)
class SourceDatasetInfo:
    """Plain data for ``schemas.event_logs.SourceDataset`` (kept schema-free here)."""

    dataset_id: str
    slug: str
    title: str
    revision: int
    license_id: str
    license_name: str | None
    license_url: str | None
    citation: str | None
    publisher_name: str | None
    publisher_url: str | None
    status: str
    disabled_reason: str | None
    allow_external_ai: bool

    def as_dict(self) -> dict[str, Any]:
        return dict(self.__dict__)


def _info(lineage: Lineage) -> SourceDatasetInfo:
    ds, rev, snap = lineage.dataset, lineage.revision, lineage.snapshot
    if ds is not None and rev is not None:
        return SourceDatasetInfo(
            dataset_id=ds.id,
            slug=f"{ds.namespace}/{ds.name}",
            title=ds.title,
            revision=rev.number,
            license_id=ds.license_id,
            license_name=ds.license_name,
            license_url=ds.license_url,
            citation=ds.citation,
            publisher_name=ds.publisher_name,
            publisher_url=ds.publisher_url,
            status=lineage.status,
            disabled_reason=ds.disabled_reason if ds.status != "active" else None,
            allow_external_ai=bool(ds.allow_external_ai),
        )
    return SourceDatasetInfo(
        dataset_id=str(snap.get("dataset_id") or ""),
        slug=str(snap.get("slug") or ""),
        title=str(snap.get("title") or ""),
        revision=int(snap.get("revision") or 0),
        license_id=str(snap.get("license_id") or "unknown"),
        license_name=snap.get("license_name"),
        license_url=snap.get("license_url"),
        citation=snap.get("citation"),
        publisher_name=snap.get("publisher_name"),
        publisher_url=snap.get("publisher_url"),
        status="disabled",
        disabled_reason=None,
        allow_external_ai=bool(snap.get("allow_external_ai", True)),
    )


async def source_datasets_for(
    session: AsyncSession, rows: Sequence[EventLog]
) -> dict[str, SourceDatasetInfo]:
    """``{log_id: SourceDatasetInfo}`` for the derived logs among ``rows`` (one query)."""
    rev_ids = {r.dataset_revision_id for r in rows if r.dataset_revision_id is not None}
    if not rev_ids:
        return {}
    found = (
        await session.execute(
            select(HubDatasetRevision, HubDataset)
            .join(HubDataset, HubDataset.id == HubDatasetRevision.dataset_id)
            .where(HubDatasetRevision.id.in_(rev_ids))
        )
    ).all()
    by_rev: dict[str, tuple[HubDatasetRevision, HubDataset]] = {
        rev.id: (rev, ds) for rev, ds in found
    }
    out: dict[str, SourceDatasetInfo] = {}
    for r in rows:
        if r.dataset_revision_id is None:
            continue
        pair = by_rev.get(r.dataset_revision_id)
        lineage = Lineage(
            dataset=pair[1] if pair else None,
            revision=pair[0] if pair else None,
            snapshot=_snapshot(r),
        )
        out[r.id] = _info(lineage)
    return out


async def source_dataset_for(session: AsyncSession, row: EventLog) -> SourceDatasetInfo | None:
    lineage = await lineage_for(session, row)
    return _info(lineage) if lineage is not None else None
