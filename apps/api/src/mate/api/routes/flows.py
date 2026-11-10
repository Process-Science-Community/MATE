"""/api/v1/flows - the flow builder: module node graphs run on a log (``mate.api.flows``).

A flow is private to its owner and a template: ``POST /flows/{id}/runs`` binds the
log. A save is refused (422) only for what the graph alone gets wrong; what depends
on the user's modules and workspace is listed by ``POST /flows/{id}/validate`` and
refuses a run. Every 422 carries ``{message, errors: [{node_id, edge_id, message}]}``
so the editor can mark the culprits.
"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any, Literal

from fastapi import APIRouter, HTTPException, Query, Response, status
from pydantic import BaseModel, Field
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from mate.api.auth import CurrentUserDep, get_owned_event_log
from mate.api.db.models import EventLog, Flow, FlowRun
from mate.api.db.session import SessionDep
from mate.api.flows.executor import FlowExecutor, get_flow_executor
from mate.api.flows.graph import (
    FlowGraph,
    FlowProblem,
    LogModel,
    ModuleNode,
    flow_problems,
    graph_errors,
    load_catalog,
    palette,
)
from mate.api.schemas.common import UtcDateTime
from mate.api.uuid7 import uuid7_str

router = APIRouter(prefix="/flows", tags=["flows"])

FlowRunStatus = Literal["queued", "running", "ready", "failed", "cancelled"]
NodeRunStatus = Literal["queued", "running", "ready", "failed", "skipped", "cancelled"]


def _utcnow() -> datetime:
    return datetime.now(UTC).replace(tzinfo=None)


# -- schemas ------------------------------------------------------------------------


class FlowRunSummary(BaseModel):
    id: str
    log_id: str
    status: FlowRunStatus
    created_at: UtcDateTime
    finished_at: UtcDateTime | None = None


class FlowSummary(BaseModel):
    id: str
    name: str
    description: str | None = None
    log_model: LogModel
    created_at: UtcDateTime
    updated_at: UtcDateTime
    # The newest run, whatever its log.
    last_run: FlowRunSummary | None = None


class FlowDetail(FlowSummary):
    graph: FlowGraph
    # `{module_id: "version+compute_hash"}` of the module nodes at the last save.
    pins: dict[str, str]


class FlowCreate(BaseModel):
    name: str = Field(min_length=1, max_length=255)
    description: str | None = Field(default=None, max_length=2000)
    # Fixed at creation: the kind of log the flow runs on.
    log_model: LogModel = "case_centric"
    graph: FlowGraph = Field(default_factory=FlowGraph)


class FlowUpdate(BaseModel):
    """Replaces the flow's name, description and graph."""

    name: str = Field(min_length=1, max_length=255)
    description: str | None = Field(default=None, max_length=2000)
    graph: FlowGraph


class FlowValidateBody(BaseModel):
    # Also check the flow can run on this log (yours, ready, of the flow's model).
    log_id: str | None = None


class FlowValidation(BaseModel):
    ok: bool
    errors: list[FlowProblem]


class FlowRunBody(BaseModel):
    log_id: str


class FlowNodeRef(BaseModel):
    # The module output's dataset id; null for an artifact source.
    dataset_id: str | None = None
    artifact_type: str
    # The ArtifactRef - opens in the artifact workspace.
    ref: str


class FlowNodeRunState(BaseModel):
    # `skipped`: an upstream node failed (or the run was cancelled before it).
    status: NodeRunStatus
    # A module node's run variant key; null when it is the module's default run.
    variant: str | None = None
    # A module node's variant job (a child of the run's), or its default run's job.
    job_id: str | None = None
    error: str | None = None
    refs: list[FlowNodeRef] = Field(default_factory=lambda: [])
    # A module node: its fresh variant was reused - nothing ran.
    reused: bool = False
    # An output node: the workspace artifact it named / pinned.
    artifact_id: str | None = None


class FlowRunOut(FlowRunSummary):
    flow_id: str
    # The parent `flow.run` job; the module nodes' variant jobs are its children.
    job_id: str | None = None
    error: str | None = None
    nodes: dict[str, FlowNodeRunState]


# -- helpers --------------------------------------------------------------------------


def _executor() -> FlowExecutor:
    executor = get_flow_executor()
    if executor is None:
        raise HTTPException(status_code=503, detail="Flow runs are not available.")
    return executor


def _invalid(message: str, problems: list[FlowProblem]) -> HTTPException:
    return HTTPException(
        status_code=422,
        detail={"message": message, "errors": [p.model_dump() for p in problems]},
    )


async def _owned_flow(session: AsyncSession, flow_id: str, user_id: str) -> Flow:
    row = await session.get(Flow, flow_id)
    if row is None or row.user_id != user_id:
        raise HTTPException(status_code=404, detail="Flow not found.")
    return row


def _run_summary(run: FlowRun | None) -> FlowRunSummary | None:
    if run is None:
        return None
    return FlowRunSummary.model_validate(run, from_attributes=True)


def _run_out(run: FlowRun) -> FlowRunOut:
    return FlowRunOut(
        id=run.id,
        flow_id=run.flow_id,
        log_id=run.log_id,
        job_id=run.job_id,
        status=run.status,  # pyright: ignore[reportArgumentType]
        error=run.error,
        created_at=run.created_at,
        finished_at=run.finished_at,
        nodes={k: FlowNodeRunState.model_validate(v) for k, v in run.nodes_json.items()},
    )


async def _last_runs(session: AsyncSession, flow_ids: list[str]) -> dict[str, FlowRun]:
    newest = (
        select(FlowRun.flow_id, func.max(FlowRun.created_at).label("at"))
        .where(FlowRun.flow_id.in_(flow_ids))
        .group_by(FlowRun.flow_id)
        .subquery()
    )
    rows = await session.execute(
        select(FlowRun).join(
            newest, (FlowRun.flow_id == newest.c.flow_id) & (FlowRun.created_at == newest.c.at)
        )
    )
    return {r.flow_id: r for r in rows.scalars()}


def _detail(row: Flow, last: FlowRun | None) -> FlowDetail:
    return FlowDetail(
        id=row.id,
        name=row.name,
        description=row.description,
        log_model=row.log_model,  # pyright: ignore[reportArgumentType]
        created_at=row.created_at,
        updated_at=row.updated_at,
        last_run=_run_summary(last),
        graph=FlowGraph.model_validate(row.graph_json),
        pins={str(k): str(v) for k, v in row.pins_json.items()},
    )


async def _save_graph(session: AsyncSession, row: Flow, graph: FlowGraph, user_id: str) -> None:
    errors = graph_errors(graph)
    if errors:
        raise _invalid("The flow graph is invalid.", errors)
    catalog = await load_catalog(session, user_id)
    in_graph = {n.data.module_id for n in graph.nodes if isinstance(n, ModuleNode)}
    # A module not loaded right now keeps the pin it was saved with.
    kept = {k: v for k, v in row.pins_json.items() if k in in_graph}
    row.pins_json = {**kept, **catalog.pins(graph)}
    row.graph_json = graph.stored()


async def _log_problems(
    session: AsyncSession, flow: Flow, log_id: str, user_id: str
) -> tuple[EventLog, list[FlowProblem]]:
    """The owned log (404 otherwise) and what keeps the flow from running on it."""
    log_row = await get_owned_event_log(session, log_id, user_id)
    problems: list[FlowProblem] = []
    if log_row.status != "ready":
        problems.append(FlowProblem(message="The event log is not ready yet."))
    if log_row.log_model != flow.log_model:
        problems.append(
            FlowProblem(message=f"This flow runs on {flow.log_model.replace('_', '-')} logs.")
        )
    return log_row, problems


async def _problems(
    session: AsyncSession, flow: Flow, user_id: str, log_id: str | None
) -> tuple[EventLog | None, list[FlowProblem]]:
    graph = FlowGraph.model_validate(flow.graph_json)
    catalog = await load_catalog(session, user_id)
    problems = await flow_problems(
        session, graph, user_id=user_id, log_model=flow.log_model, catalog=catalog
    )
    if log_id is None:
        return None, problems
    log_row, on_log = await _log_problems(session, flow, log_id, user_id)
    return log_row, [*on_log, *problems]


# -- flows ------------------------------------------------------------------------------


@router.get("", response_model=list[FlowSummary])
async def list_flows(session: SessionDep, user: CurrentUserDep) -> list[FlowSummary]:
    """Your flows, newest first, each with its latest run."""
    rows = list(
        (
            await session.execute(
                select(Flow).where(Flow.user_id == user.id).order_by(Flow.created_at.desc())
            )
        ).scalars()
    )
    last = await _last_runs(session, [r.id for r in rows])
    return [
        FlowSummary(
            id=r.id,
            name=r.name,
            description=r.description,
            log_model=r.log_model,  # pyright: ignore[reportArgumentType]
            created_at=r.created_at,
            updated_at=r.updated_at,
            last_run=_run_summary(last.get(r.id)),
        )
        for r in rows
    ]


@router.get("/palette")
async def get_flow_palette(
    session: SessionDep,
    user: CurrentUserDep,
    log_model: LogModel = "case_centric",
) -> dict[str, Any]:
    """The nodes a flow on *log_model* logs can use: each installed module that
    computes something, with its input ports, typed outputs and per-log params -
    plus the converters (``{from_type, to_type, module_id, input, output}``) the
    editor offers between two mismatched ports. Artifact types are base ids
    (``petri_net``); the log source is ``log_artifact_type``."""
    return palette(await load_catalog(session, user.id), log_model)


@router.post("", response_model=FlowDetail, status_code=status.HTTP_201_CREATED)
async def create_flow(body: FlowCreate, session: SessionDep, user: CurrentUserDep) -> FlowDetail:
    now = _utcnow()
    row = Flow(
        id=uuid7_str(),
        user_id=user.id,
        name=body.name.strip() or "Untitled flow",
        description=body.description,
        log_model=body.log_model,
        graph_json={},
        pins_json={},
        created_at=now,
        updated_at=now,
    )
    await _save_graph(session, row, body.graph, user.id)
    session.add(row)
    await session.commit()
    return _detail(row, None)


@router.get("/{flow_id}", response_model=FlowDetail)
async def get_flow(flow_id: str, session: SessionDep, user: CurrentUserDep) -> FlowDetail:
    row = await _owned_flow(session, flow_id, user.id)
    return _detail(row, (await _last_runs(session, [row.id])).get(row.id))


@router.put("/{flow_id}", response_model=FlowDetail)
async def update_flow(
    flow_id: str, body: FlowUpdate, session: SessionDep, user: CurrentUserDep
) -> FlowDetail:
    """Replace the name, description and graph (``log_model`` is fixed); re-records
    the module pins."""
    row = await _owned_flow(session, flow_id, user.id)
    await _save_graph(session, row, body.graph, user.id)
    row.name = body.name.strip() or row.name
    row.description = body.description
    row.updated_at = _utcnow()
    await session.commit()
    return _detail(row, (await _last_runs(session, [row.id])).get(row.id))


@router.delete("/{flow_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_flow(flow_id: str, session: SessionDep, user: CurrentUserDep) -> Response:
    """Delete the flow and its runs, cancelling any in flight. The artifacts its runs
    produced stay in the workspace."""
    row = await _owned_flow(session, flow_id, user.id)
    executor = get_flow_executor()
    if executor is not None:
        runs = await session.execute(
            select(FlowRun).where(
                FlowRun.flow_id == row.id, FlowRun.status.in_(("queued", "running"))
            )
        )
        for run in runs.scalars():
            await executor.cancel(run)
    await session.delete(row)
    await session.commit()
    return Response(status_code=status.HTTP_204_NO_CONTENT)


@router.post("/{flow_id}/validate", response_model=FlowValidation)
async def validate_flow(
    flow_id: str,
    session: SessionDep,
    user: CurrentUserDep,
    body: FlowValidateBody | None = None,
) -> FlowValidation:
    """Everything that keeps the saved flow from running - on the log too, when given."""
    row = await _owned_flow(session, flow_id, user.id)
    _log_row, problems = await _problems(session, row, user.id, body.log_id if body else None)
    return FlowValidation(ok=not problems, errors=problems)


# -- runs -------------------------------------------------------------------------------


@router.post("/{flow_id}/runs", response_model=FlowRunOut, status_code=status.HTTP_202_ACCEPTED)
async def start_flow_run(
    flow_id: str, body: FlowRunBody, session: SessionDep, user: CurrentUserDep
) -> FlowRunOut:
    """Run the flow on a log you own: one ``flow.run`` job (in the jobs drawer,
    cancellable) works through the nodes. Unchanged nodes reuse their fresh results.
    422 with the problems when it cannot run."""
    row = await _owned_flow(session, flow_id, user.id)
    log_row, problems = await _problems(session, row, user.id, body.log_id)
    assert log_row is not None
    if problems:
        raise _invalid("The flow cannot run.", problems)
    run = await _executor().start(user.id, row, log_row)
    return _run_out(run)


@router.get("/{flow_id}/runs", response_model=list[FlowRunOut])
async def list_flow_runs(
    flow_id: str,
    session: SessionDep,
    user: CurrentUserDep,
    limit: int = Query(default=20, ge=1, le=100),
) -> list[FlowRunOut]:
    """The flow's runs, newest first."""
    await _owned_flow(session, flow_id, user.id)
    rows = (
        await session.execute(
            select(FlowRun)
            .where(FlowRun.flow_id == flow_id)
            .order_by(FlowRun.created_at.desc(), FlowRun.id.desc())
            .limit(limit)
        )
    ).scalars()
    executor = get_flow_executor()
    out: list[FlowRunOut] = []
    for run in rows:
        if executor is not None:
            run = await executor.reconcile(session, run)
        out.append(_run_out(run))
    return out


async def _owned_run(session: AsyncSession, flow_id: str, run_id: str, user_id: str) -> FlowRun:
    await _owned_flow(session, flow_id, user_id)
    run = await session.get(FlowRun, run_id)
    if run is None or run.flow_id != flow_id:
        raise HTTPException(status_code=404, detail="Flow run not found.")
    executor = get_flow_executor()
    return run if executor is None else await executor.reconcile(session, run)


@router.get("/{flow_id}/runs/{run_id}", response_model=FlowRunOut)
async def get_flow_run(
    flow_id: str, run_id: str, session: SessionDep, user: CurrentUserDep
) -> FlowRunOut:
    return _run_out(await _owned_run(session, flow_id, run_id, user.id))


@router.post("/{flow_id}/runs/{run_id}/cancel", response_model=FlowRunOut)
async def cancel_flow_run(
    flow_id: str, run_id: str, session: SessionDep, user: CurrentUserDep
) -> FlowRunOut:
    """Cancel the run and the variant job it is waiting on - when the run submitted it
    and no other run waits on it. A finished run is returned as is."""
    run = await _owned_run(session, flow_id, run_id, user.id)
    await _executor().cancel(run)
    await session.refresh(run)
    return _run_out(await _executor().reconcile(session, run))
