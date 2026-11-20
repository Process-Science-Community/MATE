"""/api/v1/module-outputs - catalog of module *data* outputs for generic visualization.

A *module output* is a module's named, typed data output (manifest ``outputs:``)
rendered by the platform's generic visualization layer instead of a
module-authored widget. This router only exposes the **catalog**; the data
itself is fetched by the frontend straight from the module's own route
(``GET /api/v1/modules/{module_id}{route}``), so the per-request ephemeral
event filter and result-cache variant apply unchanged - no new data path.

Mirrors ``GET /api/v1/modules/cards`` (the widget catalog): it returns every
owned module's outputs with their ``log_models`` and the Dashboards palette
filters by the board's model client-side.
"""

from __future__ import annotations

from typing import Any, Literal

from fastapi import APIRouter, Header, HTTPException
from pydantic import BaseModel, Field

from mate.api.auth import CurrentUserDep
from mate.api.db.models import EventLog
from mate.api.db.session import SessionDep
from mate.api.hub.gate import ensure_log_usable
from mate.api.module_outputs.adapters import resolve_output
from mate.api.module_outputs.envelope import OutputEnvelope
from mate.api.module_outputs.transforms import TransformError, apply_transforms
from mate.api.modules import get_module_loader
from mate.api.modules.installs import user_module_ids, user_owns_module
from mate.api.modules.loader import decode_event_filter_header
from mate.api.schemas.event_logs import LogModel
from mate.api.sharing import user_can_access_log_module

router = APIRouter(prefix="/module-outputs", tags=["module-outputs"])


class OutputCatalogEntry(BaseModel):
    """One module data output the Dashboards palette can drop as a generic-viz
    card. Aggregated from every owned module's ``outputs:`` so the palette can
    render the catalog without loading any bundle - the data is fetched lazily
    from ``route`` when the card mounts."""

    module_id: str
    module_name: str
    output_id: str
    title: str
    description: str | None = None
    icon: str | None = None
    # Data shape this output produces - drives which generic viz can render it.
    shape: Literal["table", "graph", "kpi", "tree", "blob"]
    # Module sub-route (leading slash) the frontend calls under
    # /api/v1/modules/{module_id} to fetch the data.
    route: str
    # Log model(s) this output applies to. The palette only offers an output
    # whose models include the board's model (case-centric vs OCEL).
    log_models: list[LogModel] = Field(default_factory=lambda: ["case_centric"])
    params_schema: dict[str, Any] | None = None


@router.get("/catalog", response_model=list[OutputCatalogEntry])
async def list_outputs(session: SessionDep, user: CurrentUserDep) -> list[OutputCatalogEntry]:
    """Catalog of every output exposed by the modules this user owns.

    Ordering is stable (module, then declared output order) so the palette
    doesn't reshuffle between loads.
    """
    try:
        loader = get_module_loader()
    except HTTPException:
        return []
    manifests = loader.manifests()
    if not manifests:
        return []

    owned = await user_module_ids(session, user.id)
    entries: list[OutputCatalogEntry] = []
    for m in manifests:
        if m.id not in owned:
            continue
        for d in m.outputs:
            entries.append(
                OutputCatalogEntry(
                    module_id=m.id,
                    module_name=m.name,
                    output_id=d.id,
                    title=d.title or d.id.replace("-", " ").replace("_", " ").title(),
                    description=d.description,
                    icon=d.icon,
                    shape=d.shape,
                    route=d.route,
                    log_models=d.log_models,
                    params_schema=d.params_schema,
                )
            )
    return entries


async def _assert_owns(session: SessionDep, user_id: str, module_id: str) -> None:
    if not await user_owns_module(session, user_id, module_id):
        raise HTTPException(status_code=404, detail=f"Module {module_id!r} is not installed.")


async def _assert_can_read_log(
    session: SessionDep, user_id: str, log_id: str, module_id: str
) -> None:
    # The module context and result cache join log_id into on-disk paths, so a
    # foreign (or crafted) id must stop here: own log, or one shared with you via
    # a dashboard that actually places THIS module (not every module on the log).
    if not await user_can_access_log_module(session, log_id, user_id, module_id):
        raise HTTPException(status_code=404, detail="Event log not found.")
    # A copy of a withdrawn dataset: 410 before the output is even resolved (the
    # module-context gate would catch it later, but only for a real output).
    row = await session.get(EventLog, log_id)
    if row is not None and row.dataset_revision_id is not None:
        await ensure_log_usable(session, row)


@router.get("/{module_id}/{output_id}", response_model=OutputEnvelope)
async def get_output(
    module_id: str,
    output_id: str,
    log_id: str,
    session: SessionDep,
    user: CurrentUserDep,
    x_ff_event_filter: str | None = Header(default=None, alias="X-FF-Event-Filter"),
) -> OutputEnvelope:
    """Resolve a module output to a canonical envelope (server-side). Honors the
    ephemeral ``X-FF-Event-Filter`` header exactly like the module's own route."""
    await _assert_owns(session, user.id, module_id)
    await _assert_can_read_log(session, user.id, log_id, module_id)
    loader = get_module_loader()
    try:
        return await resolve_output(
            loader,
            module_id,
            output_id,
            log_id,
            user.id,
            filter_override=decode_event_filter_header(x_ff_event_filter),
        )
    except ValueError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc


class TransformRequest(BaseModel):
    log_id: str
    transforms: list[dict[str, Any]] = Field(default_factory=list)


@router.post("/{module_id}/{output_id}/transform", response_model=OutputEnvelope)
async def transform_output(
    module_id: str,
    output_id: str,
    body: TransformRequest,
    session: SessionDep,
    user: CurrentUserDep,
    x_ff_event_filter: str | None = Header(default=None, alias="X-FF-Event-Filter"),
) -> OutputEnvelope:
    """Resolve a module output then apply an ordered transform chain over it."""
    await _assert_owns(session, user.id, module_id)
    await _assert_can_read_log(session, user.id, body.log_id, module_id)
    loader = get_module_loader()
    try:
        env = await resolve_output(
            loader,
            module_id,
            output_id,
            body.log_id,
            user.id,
            filter_override=decode_event_filter_header(x_ff_event_filter),
        )
        return apply_transforms(env, body.transforms)
    except TransformError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
