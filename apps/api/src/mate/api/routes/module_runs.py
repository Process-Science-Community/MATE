"""/api/v1/event-logs/{log_id}/module-runs - per-log module compute state.

Modules compute lazily (see ``mate.api.modules.processing``): a log is ready the
moment it is parsed, and a module runs for it the first time something needs it.
These two routes are how the UI reads what has computed for a log, and how a module
page or dashboard card asks for one to be computed.

``…/module-runs/{module_id}/variants`` requests, reads and deletes *run variants* -
extra runs with their own per-log params, input artifacts and filter
(``mate.api.modules.variants``).
"""

from __future__ import annotations

from dataclasses import asdict
from typing import Literal

from fastapi import APIRouter, HTTPException, Response
from pydantic import BaseModel

from mate.api.auth import CurrentUserDep, get_owned_event_log
from mate.api.db.session import SessionDep
from mate.api.modules.processing import ModuleProcessingCoordinator, RunState, get_coordinator
from mate.api.modules.variants import RunSpec, VariantState
from mate.api.schemas.common import UtcDateTime

router = APIRouter(prefix="/event-logs/{log_id}/module-runs", tags=["event-logs"])

RunStatus = Literal[
    "idle", "queued", "running", "ready", "stale", "failed", "cancelled", "unavailable"
]


class ModuleRunState(BaseModel):
    module_id: str
    # `idle`: never computed for this log. `stale`: computed, but the log's data, the
    # module's code or a `ui.affects: precompute` setting has changed since. `queued`
    # with no `job_id`: waiting for a module it depends on.
    status: RunStatus
    job_id: str | None = None
    # How the module behaves at import ("eager" runs in the background right after
    # every import); absent for a module that does not precompute on import.
    precompute: Literal["eager", "lazy"] | None = None
    error: str | None = None
    finished_at: UtcDateTime | None = None


class EnsureRunBody(BaseModel):
    # Retry a failed or cancelled run. A fresh result is never re-run.
    force: bool = False


def _out(state: RunState) -> ModuleRunState:
    # Validated rather than cast: the coordinator's statuses are plain strings.
    return ModuleRunState.model_validate(asdict(state))


def _coordinator() -> ModuleProcessingCoordinator:
    coordinator = get_coordinator()
    if coordinator is None:
        raise HTTPException(status_code=503, detail="Module processing is not running.")
    return coordinator


@router.get("", response_model=list[ModuleRunState])
async def list_module_runs(
    log_id: str, session: SessionDep, user: CurrentUserDep
) -> list[ModuleRunState]:
    """The compute state of every module you have that precomputes, for this log."""
    await get_owned_event_log(session, log_id, user.id)
    try:
        states = await _coordinator().list_states(user.id, log_id)
    except LookupError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    return [_out(s) for s in states]


@router.post("/{module_id}", response_model=list[ModuleRunState])
async def ensure_module_run(
    log_id: str,
    module_id: str,
    session: SessionDep,
    user: CurrentUserDep,
    body: EnsureRunBody | None = None,
) -> list[ModuleRunState]:
    """Make sure a module - and every module it needs first - has computed for this log.

    Starts whatever is missing or stale and returns each module's state in
    dependency order; a module with nothing to precompute returns an empty list.
    Idempotent: asking again while a run is in flight (or after it finished) starts
    nothing new, so a panel can call it on every open.
    """
    row = await get_owned_event_log(session, log_id, user.id)
    if row.status != "ready":
        raise HTTPException(
            status_code=409,
            detail="The event log is not ready yet - wait for the import to finish.",
        )
    try:
        states = await _coordinator().ensure(
            user.id, log_id, module_id, force=bool(body and body.force)
        )
    except LookupError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    return [_out(s) for s in states]


# --------------------------------------------------------------------------- #
# Run variants
# --------------------------------------------------------------------------- #

VariantStatus = Literal["queued", "running", "ready", "stale", "failed", "cancelled", "unavailable"]


class VariantArtifactRef(BaseModel):
    dataset_id: str
    artifact_type: str
    # `{log_id}/{module_id}@{variant}/{cache_key}` - an input binding or MCP read.
    ref: str


class ModuleVariantState(BaseModel):
    # The spec's key: 16 hex chars of its canonical JSON's hash.
    variant: str
    # `stale`: the log's data, the module's code or the variant's effective
    # `ui.affects: precompute` config changed since it ran.
    status: VariantStatus
    spec: RunSpec
    job_id: str | None = None
    error: str | None = None
    finished_at: UtcDateTime | None = None
    refs: list[VariantArtifactRef]


class EnsureVariantBody(RunSpec):
    # Retry a failed or cancelled variant. A fresh one is never re-run.
    force: bool = False


def _variant_out(state: VariantState) -> ModuleVariantState:
    return ModuleVariantState.model_validate(asdict(state))


@router.post("/{module_id}/variants", response_model=ModuleVariantState)
async def ensure_module_variant(
    log_id: str,
    module_id: str,
    body: EnsureVariantBody,
    response: Response,
    session: SessionDep,
    user: CurrentUserDep,
) -> ModuleVariantState:
    """Make sure the run variant this spec names has computed for this log.

    ``params`` may only hold the module's ``ui.scope: dataset`` properties (422
    otherwise); ``inputs`` binds input ports to artifact refs, every unbound port
    keeps its default producer; ``filter`` replaces the log's committed filter.
    Idempotent like the default run: 202 while the variant's job is in flight, 200
    once settled. A required input that has not been produced fails the variant
    without a job - nothing runs its producer for it.
    """
    row = await get_owned_event_log(session, log_id, user.id)
    if row.status != "ready":
        raise HTTPException(
            status_code=409,
            detail="The event log is not ready yet - wait for the import to finish.",
        )
    try:
        spec = RunSpec.model_validate(body.canonical())
        state = await _coordinator().variants.ensure(
            user.id, log_id, module_id, spec, force=body.force
        )
    except LookupError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    if state.status in ("queued", "running"):
        response.status_code = 202
    return _variant_out(state)


@router.get("/{module_id}/variants", response_model=list[ModuleVariantState])
async def list_module_variants(
    log_id: str, module_id: str, session: SessionDep, user: CurrentUserDep
) -> list[ModuleVariantState]:
    """Every run variant of the module for this log."""
    await get_owned_event_log(session, log_id, user.id)
    try:
        states = await _coordinator().variants.list(user.id, log_id, module_id)
    except LookupError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    return [_variant_out(s) for s in states]


@router.get("/{module_id}/variants/{variant}", response_model=ModuleVariantState)
async def get_module_variant(
    log_id: str, module_id: str, variant: str, session: SessionDep, user: CurrentUserDep
) -> ModuleVariantState:
    await get_owned_event_log(session, log_id, user.id)
    try:
        state = await _coordinator().variants.get(user.id, log_id, module_id, variant)
    except LookupError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    if state is None:
        raise HTTPException(status_code=404, detail=f"Variant {variant!r} not found.")
    return _variant_out(state)


@router.delete("/{module_id}/variants/{variant}", status_code=204)
async def delete_module_variant(
    log_id: str, module_id: str, variant: str, session: SessionDep, user: CurrentUserDep
) -> None:
    """Cancel the variant's job if it is in flight and delete its results."""
    await get_owned_event_log(session, log_id, user.id)
    try:
        deleted = await _coordinator().variants.delete(user.id, log_id, module_id, variant)
    except LookupError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    if not deleted:
        raise HTTPException(status_code=404, detail=f"Variant {variant!r} not found.")
