"""Flow tools (``analysis`` toolset): list your flows, run one on a process, read a run.

Flows are built in the web app (``/flows``); these tools only run them. They reuse
the ``/api/v1/flows`` route internals, so ownership is 404-on-foreign and a flow that
cannot run fails with ``[invalid]`` listing why. A run is a ``flow.run`` job:
``wait_for_job(job_id)``, then ``get_flow_run`` for each node's state and artifact refs
(``get_artifact`` reads them).
"""

from __future__ import annotations

from typing import Any, cast

from fastapi import HTTPException

from mate.api.db.engine import get_sessionmaker
from mate.api.db.models import FlowRun
from mate.api.mcp.core import MCPContext, authz, guarded
from mate.api.mcp.errors import CODE_INVALID, CODE_NOT_FOUND, from_http_exception, tool_error
from mate.api.mcp.registry import mcp_tool
from mate.api.mcp.scopes import SCOPE_MODULES_READ, SCOPE_MODULES_WRITE


def _error(exc: HTTPException) -> Exception:
    # The routes' 422 carries `{message, errors: [{node_id, edge_id, message}]}`.
    detail = cast(object, exc.detail)
    if isinstance(detail, dict) and "errors" in detail:
        body = cast(dict[str, Any], detail)
        problems = "; ".join(str(e.get("message")) for e in body["errors"])
        return tool_error(CODE_INVALID, f"{body.get('message')} {problems}".strip())
    return from_http_exception(exc)


@mcp_tool(toolset="analysis", idempotent=True)
async def list_flows(ctx: MCPContext) -> list[dict[str, Any]]:
    """List your flows (module pipelines built in the web app), newest first.

    Each has ``id``, ``name``, ``description``, ``log_model`` (the kind of process it
    runs on) and ``last_run`` (``status`` + ``log_id``) when it has run.
    """
    p = await authz(ctx, SCOPE_MODULES_READ)

    async def _impl() -> list[dict[str, Any]]:
        from mate.api.routes.flows import list_flows as list_route

        async with get_sessionmaker()() as session:
            rows = await list_route(session, p.user)
        return [r.model_dump(mode="json") for r in rows]

    return await guarded(p, "list_flows", {}, _impl())


@mcp_tool(toolset="analysis", write=True)
async def run_flow(ctx: MCPContext, flow_id: str, log_id: str) -> dict[str, Any]:
    """Run one of your flows on a process (event log) you own.

    Returns the run: ``id``, ``status`` (``queued``) and ``job_id`` - wait with
    ``wait_for_job(job_id)``, then read it with ``get_flow_run``. Nodes whose inputs
    did not change reuse their earlier results. Fails with [invalid] when the flow
    cannot run (an unconnected required input, a module you no longer have, a
    process of the wrong kind) or the process is not ready.
    """
    p = await authz(ctx, SCOPE_MODULES_WRITE, write=True)

    async def _impl() -> dict[str, Any]:
        from mate.api.routes.flows import FlowRunBody, start_flow_run

        async with get_sessionmaker()() as session:
            try:
                run = await start_flow_run(flow_id, FlowRunBody(log_id=log_id), session, p.user)
            except HTTPException as exc:
                raise _error(exc) from exc
        return run.model_dump(mode="json")

    return await guarded(
        p, "run_flow", {"flow_id": flow_id, "log_id": log_id}, _impl(), mutation=True
    )


@mcp_tool(toolset="analysis", idempotent=True)
async def get_flow_run(ctx: MCPContext, run_id: str) -> dict[str, Any]:
    """Get one flow run: its ``status`` (queued|running|ready|failed|cancelled),
    ``error`` and ``nodes`` - per node id its ``status`` (also ``skipped`` when an
    upstream node failed), ``error``, whether its earlier result was ``reused`` and the
    ``refs`` of the artifacts it produced (``get_artifact(ref)`` reads one)."""
    p = await authz(ctx, SCOPE_MODULES_READ)

    async def _impl() -> dict[str, Any]:
        from mate.api.routes.flows import get_flow_run as get_route

        async with get_sessionmaker()() as session:
            run = await session.get(FlowRun, run_id)
            if run is None or run.user_id != p.user.id:
                raise tool_error(CODE_NOT_FOUND, "Flow run not found.")
            try:
                out = await get_route(run.flow_id, run.id, session, p.user)
            except HTTPException as exc:
                raise _error(exc) from exc
        return out.model_dump(mode="json")

    return await guarded(p, "get_flow_run", {"run_id": run_id}, _impl())
