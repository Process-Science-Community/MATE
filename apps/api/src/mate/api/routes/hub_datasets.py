"""/api/v1/hub - the dataset hub (published event logs, copy-on-use).

``/hub/status`` is always mounted (the web reads it to show or hide the nav);
everything else 404s while ``HUB_DATASETS_ENABLED`` is off. None of these routes
publish a bus event.
"""

from __future__ import annotations

from typing import Annotated, Literal

from fastapi import APIRouter, Depends, HTTPException, Query, Response, status
from sqlalchemy import select

from mate.api.auth import CurrentUserDep
from mate.api.auth.dependencies import may_curate_datasets
from mate.api.config import get_settings
from mate.api.db.models import HubDataset
from mate.api.db.session import SessionDep
from mate.api.hub import service
from mate.api.hub.access import (
    NOT_FOUND,
    CuratorDep,
    get_readable_dataset,
    require_hub_enabled,
)
from mate.api.hub.licenses import LICENSES, license_dict
from mate.api.modules import get_module_loader
from mate.api.schemas.hub_datasets import (
    DatasetCreate,
    DatasetDetail,
    DatasetDisableRequest,
    DatasetPage,
    DatasetPurgeRequest,
    DatasetUpdate,
    DatasetUseRequest,
    DatasetUseResponse,
    HubStatus,
    LicenseOut,
    PublishCandidate,
)

status_router = APIRouter(prefix="/hub", tags=["hub"])
router = APIRouter(prefix="/hub", tags=["hub"], dependencies=[Depends(require_hub_enabled)])


@status_router.get("/status", response_model=HubStatus)
async def hub_status(user: CurrentUserDep) -> HubStatus:
    enabled = get_settings().hub_datasets_enabled
    return HubStatus(enabled=enabled, can_curate=enabled and may_curate_datasets(user.roles))


@router.get("/licenses", response_model=list[LicenseOut])
async def list_licenses(user: CurrentUserDep) -> list[LicenseOut]:
    del user
    return [LicenseOut.model_validate(license_dict(lic.id, None, None)) for lic in LICENSES]


@router.get("/datasets", response_model=DatasetPage)
async def list_datasets(
    session: SessionDep,
    user: CurrentUserDep,
    q: Annotated[str | None, Query(max_length=200)] = None,
    license: Annotated[str | None, Query(max_length=32)] = None,
    namespace: Annotated[str | None, Query(max_length=64)] = None,
    sort: Annotated[Literal["updated", "uses"], Query()] = "updated",
    limit: Annotated[int, Query(ge=1, le=50)] = 24,
    offset: Annotated[int, Query(ge=0)] = 0,
) -> DatasetPage:
    items, total = await service.list_datasets(
        session,
        user,
        q=q,
        license=license,
        namespace=namespace,
        sort=sort,
        limit=limit,
        offset=offset,
    )
    return DatasetPage(items=items, total=total, limit=limit, offset=offset)


def _check_slug(namespace: str, name: str) -> None:
    if not (service.slug_ok(namespace) and service.slug_ok(name)):
        raise HTTPException(status_code=404, detail=NOT_FOUND)


async def _readable(
    session: SessionDep, user: CurrentUserDep, namespace: str, name: str
) -> HubDataset:
    _check_slug(namespace, name)
    return await get_readable_dataset(session, namespace, name, user)


async def _curated(session: SessionDep, namespace: str, name: str) -> HubDataset:
    _check_slug(namespace, name)
    ds = (
        await session.execute(
            select(HubDataset).where(HubDataset.namespace == namespace, HubDataset.name == name)
        )
    ).scalar_one_or_none()
    if ds is None:
        raise HTTPException(status_code=404, detail=NOT_FOUND)
    return ds


@router.get("/publish-candidates/{log_id}", response_model=list[PublishCandidate])
async def publish_candidates(
    log_id: str, session: SessionDep, curator: CuratorDep
) -> list[PublishCandidate]:
    """The module results a publish of ``log_id`` ships by default (the dialog's list)."""
    rows = await service.publish_candidates(session, get_module_loader(), curator, log_id)
    return [PublishCandidate.model_validate(r) for r in rows]


@router.post("/datasets", response_model=DatasetDetail, status_code=status.HTTP_201_CREATED)
async def publish_dataset(
    body: DatasetCreate, session: SessionDep, curator: CuratorDep
) -> DatasetDetail:
    ds = await service.publish(session, get_module_loader(), curator, body)
    return await service.get_detail(session, ds, curator)


@router.get("/datasets/{namespace}/{name}", response_model=DatasetDetail)
async def get_dataset(
    namespace: str, name: str, session: SessionDep, user: CurrentUserDep
) -> DatasetDetail:
    ds = await _readable(session, user, namespace, name)
    return await service.get_detail(session, ds, user)


@router.post("/datasets/{namespace}/{name}/use", response_model=DatasetUseResponse)
async def use_dataset(
    namespace: str,
    name: str,
    session: SessionDep,
    user: CurrentUserDep,
    response: Response,
    body: DatasetUseRequest | None = None,
) -> DatasetUseResponse:
    ds = await _readable(session, user, namespace, name)
    row, created = await service.use(session, ds, user, new_copy=bool(body and body.new_copy))
    response.status_code = status.HTTP_201_CREATED if created else status.HTTP_200_OK
    return DatasetUseResponse(log=await service.log_detail(session, row, user), created=created)


@router.patch("/datasets/{namespace}/{name}", response_model=DatasetDetail)
async def update_dataset(
    namespace: str, name: str, body: DatasetUpdate, session: SessionDep, curator: CuratorDep
) -> DatasetDetail:
    ds = await _curated(session, namespace, name)
    ds = await service.update_card(session, ds, body)
    return await service.get_detail(session, ds, curator)


@router.post("/datasets/{namespace}/{name}/disable", response_model=DatasetDetail)
async def disable_dataset(
    namespace: str,
    name: str,
    body: DatasetDisableRequest,
    session: SessionDep,
    curator: CuratorDep,
) -> DatasetDetail:
    ds = await _curated(session, namespace, name)
    ds = await service.disable(session, ds, curator, body.reason)
    return await service.get_detail(session, ds, curator)


@router.post("/datasets/{namespace}/{name}/enable", response_model=DatasetDetail)
async def enable_dataset(
    namespace: str, name: str, session: SessionDep, curator: CuratorDep
) -> DatasetDetail:
    ds = await _curated(session, namespace, name)
    ds = await service.enable(session, ds)
    return await service.get_detail(session, ds, curator)


@router.post("/datasets/{namespace}/{name}/purge", response_model=DatasetDetail)
async def purge_dataset(
    namespace: str,
    name: str,
    body: DatasetPurgeRequest,
    session: SessionDep,
    curator: CuratorDep,
) -> DatasetDetail:
    ds = await _curated(session, namespace, name)
    ds = await service.purge(session, ds, curator, body.confirm)
    return await service.get_detail(session, ds, curator)
