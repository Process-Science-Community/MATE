"""Flow runs: one parent ``flow.run`` job per run that works through the flow's nodes.

The job is *detached* (``JobRuntime.register(detached=True)``): it never holds a worker
slot, since all it does is wait on the module nodes' jobs. Nodes run one at a time in
:func:`run_order`. A module node with no params and no input other than its default
binding *is* the module's default run (``coordinator.ensure``) - no variant, no second
copy in the workspace - unless that run is older than an input it reads (its producer
re-ran in this flow run, or wrote since): then it runs as the variant of no spec,
which reads the input anew. Any other is a run variant (``VariantRunner.ensure``) with
the node's params and its connected inputs - variant keys are content-addressed, so
an unchanged node reuses its fresh variant without a job (memoisation). Its variant
job is a child of the flow job in the jobs drawer. Every bound input's producer is
brought up to date first - a default-run ref's default run (on any log) runs or
refreshes, a variant ref's variant re-runs when stale or failed; an unconnected
optional port's producer never runs for it (best effort, as for the default run) -
and a variant whose input finished after it did re-runs (``rerun``): an input ref
names the producer, not its content. Any error fails that node alone: everything
downstream is skipped and the run fails; other branches still run. Cancelling a run
cancels the variant job it waits on only when it submitted that job and no other run
waits on, or is joining, that variant. An output node names (and pins) its module
output's workspace artifact.

``flow_runs.nodes_json`` holds every node's state; each change is published as
``flow_run.updated`` (``{user_id, flow_id, run_id, node_id, status}``, ``node_id``
null for the run itself). Waiting rides the variant/run bus events, re-reading the
state at least every ``_POLL_S`` in case one was dropped.
"""

from __future__ import annotations

import asyncio
import contextlib
from collections import defaultdict
from collections.abc import AsyncGenerator, Awaitable, Callable
from datetime import UTC, datetime, timedelta
from typing import Any

import structlog
from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from mate.api.db.models import Artifact, EventLog, Flow, FlowRun, Job
from mate.api.events import EventBus
from mate.api.flows.graph import (
    LOG_HANDLE,
    Catalog,
    FlowEdge,
    FlowGraph,
    LogSourceData,
    ModuleNode,
    OutputNode,
    SourceNode,
    flow_problems,
    is_log_type,
    load_catalog,
    outputs,
    run_order,
    type_id,
    workspace_type,
)
from mate.api.jobs.runtime import JobHandle, JobRuntime
from mate.api.modules.artifacts import register_run
from mate.api.modules.cache import run_variant_key
from mate.api.modules.inputs import ModuleInputs
from mate.api.modules.processing import ModuleProcessingCoordinator, RunState
from mate.api.modules.variants import RunSpec, VariantState
from mate.api.uuid7 import uuid7_str
from mate.sdk.artifacts import WORKSPACE_MODULE_ID, ArtifactRef
from mate.sdk.manifest import Manifest

log = structlog.get_logger(__name__)

JOB_TYPE = "flow.run"
TOPIC = "flow_run.updated"
IN_FLIGHT = ("queued", "running")
_WAKE_TOPICS = ("module_variant.updated", "module_run.updated")
_POLL_S = 1.0
_ERROR_MAX = 2000
INTERRUPTED = "Interrupted by a restart."
# A queued run with no job row yet this young is `start()` still submitting it.
_START_GRACE = timedelta(seconds=60)

Wait = Callable[[], Awaitable[None]]


def _utcnow() -> datetime:
    return datetime.now(UTC).replace(tzinfo=None)


def _settled(nodes: dict[str, Any], status: str, error: str | None) -> dict[str, Any]:
    """*nodes* with none left in flight: a running node ends *status* (with *error*), a
    queued one skipped."""
    return {
        nid: s
        if s.get("status") not in IN_FLIGHT
        else {**s, "status": "skipped"}
        if s.get("status") == "queued"
        else {**s, "status": status, "error": error}
        for nid, s in nodes.items()
    }


def _node_state(status: str = "queued") -> dict[str, Any]:
    return {
        "status": status,
        "variant": None,
        "job_id": None,
        "error": None,
        "refs": [],
        "reused": False,
        "artifact_id": None,
    }


class _Run:
    """One run's node states: every change is persisted and published."""

    def __init__(
        self,
        executor: FlowExecutor,
        run_id: str,
        flow_id: str,
        user_id: str,
        log_id: str,
        graph: FlowGraph,
    ) -> None:
        self.executor = executor
        self.run_id = run_id
        self.flow_id = flow_id
        self.user_id = user_id
        self.log_id = log_id
        self.graph = graph
        self.nodes: dict[str, dict[str, Any]] = {n.id: _node_state() for n in graph.nodes}
        # The run variant (log, module, key) a module node waits on, or is about to join.
        self.waiting: tuple[str, str, str] | None = None

    async def save(self, node_id: str | None, status: str, **values: Any) -> None:
        if node_id is not None:
            self.nodes[node_id] = {**self.nodes[node_id], "status": status, **values}
            row = {"nodes_json": {k: dict(v) for k, v in self.nodes.items()}}
        else:
            row = {"status": status, "nodes_json": dict(self.nodes), **values}
        async with self.executor.sessionmaker() as session:
            await session.execute(update(FlowRun).where(FlowRun.id == self.run_id).values(**row))
            await session.commit()
        await self.executor.publish(self.user_id, self.flow_id, self.run_id, node_id, status)

    async def fail(self, node_id: str, error: str) -> None:
        await self.save(node_id, "failed", error=error[:_ERROR_MAX])


class FlowExecutor:
    """Starts, runs and settles flow runs (one per process, ``get_flow_executor``)."""

    def __init__(
        self,
        coordinator: ModuleProcessingCoordinator,
        runtime: JobRuntime,
        bus: EventBus,
        sessionmaker: async_sessionmaker[AsyncSession],
    ) -> None:
        self._coordinator = coordinator
        self._runtime = runtime
        self._bus = bus
        self.sessionmaker = sessionmaker
        # The runs this process is working through, by id.
        self._runs: dict[str, _Run] = {}
        runtime.register(JOB_TYPE, self._run, detached=True)

    async def publish(
        self, user_id: str, flow_id: str, run_id: str, node_id: str | None, status: str
    ) -> None:
        # `user_id` is mandatory: the SSE fan-out filters on it (tenant isolation).
        await self._bus.publish(
            TOPIC,
            {
                "user_id": user_id,
                "flow_id": flow_id,
                "run_id": run_id,
                "node_id": node_id,
                "status": status,
            },
        )

    # -- the API ---------------------------------------------------------------

    async def start(self, user_id: str, flow: Flow, log_row: EventLog) -> FlowRun:
        """Record a queued run of *flow* on *log_row* and submit its job. The caller
        has checked ownership and :func:`flow_problems`."""
        job_id = uuid7_str()
        run = FlowRun(
            id=uuid7_str(),
            flow_id=flow.id,
            user_id=user_id,
            log_id=log_row.id,
            job_id=job_id,
            status="queued",
            nodes_json={},
            created_at=_utcnow(),
        )
        async with self.sessionmaker() as session:
            session.add(run)
            await session.commit()
            await session.refresh(run)
        try:
            await self._runtime.submit(
                type_=JOB_TYPE,
                user_id=user_id,
                title=f"Flow · {flow.name}",
                subtitle=log_row.name,
                payload={"flow_id": flow.id, "run_id": run.id, "log_id": log_row.id},
                job_id=job_id,
            )
        except Exception as exc:
            async with self.sessionmaker() as session:
                await session.execute(
                    update(FlowRun)
                    .where(FlowRun.id == run.id)
                    .values(status="failed", error=str(exc)[:_ERROR_MAX], finished_at=_utcnow())
                )
                await session.commit()
            raise
        await self.publish(user_id, flow.id, run.id, None, "queued")
        return run

    async def cancel(self, run: FlowRun) -> None:
        """Cancel an in-flight run: its job, which cancels the variant job it waits on
        when the run submitted it and no other run waits on it."""
        if run.status in IN_FLIGHT and run.job_id:
            await self._runtime.cancel(run.job_id)

    async def reconcile(self, session: AsyncSession, run: FlowRun) -> FlowRun:
        """*run*, settled from its job when that ended without the run recording it (a
        job cancelled before it started, a crash in the handler)."""
        if run.status not in IN_FLIGHT or not run.job_id or self._runtime.is_live(run.job_id):
            return run
        job = await session.get(Job, run.job_id)
        if job is not None and job.status in IN_FLIGHT:
            return run
        if job is None and run.status == "queued" and _utcnow() - run.created_at < _START_GRACE:
            return run  # `start()` is still submitting its job
        cancelled = job is not None and job.status == "cancelled"
        run.status = "cancelled" if cancelled else "failed"
        run.error = None if cancelled else ((job.error if job else None) or INTERRUPTED)
        run.finished_at = _utcnow()
        run.nodes_json = _settled(run.nodes_json, run.status, run.error)
        await session.commit()
        await self.publish(run.user_id, run.flow_id, run.id, None, run.status)
        return run

    async def reconcile_boot(self) -> None:
        """Fail the runs (and their jobs) a previous process left in flight: nothing
        resumes a flow run - run it again."""
        now = _utcnow()
        async with self.sessionmaker() as session:
            await session.execute(
                update(Job)
                .where(Job.type == JOB_TYPE, Job.status.in_(IN_FLIGHT))
                .values(status="failed", error=INTERRUPTED, finished_at=now)
            )
            runs = await session.execute(select(FlowRun).where(FlowRun.status.in_(IN_FLIGHT)))
            for run in runs.scalars():
                run.status, run.error, run.finished_at = "failed", INTERRUPTED, now
                run.nodes_json = _settled(run.nodes_json, "failed", INTERRUPTED)
            await session.commit()

    # -- the job ---------------------------------------------------------------

    @contextlib.asynccontextmanager
    async def _wakeups(self, user_id: str) -> AsyncGenerator[Wait]:
        """A ``wait()`` that returns on the user's next variant/run event, or after
        ``_POLL_S`` at the latest."""
        woken = asyncio.Event()
        async with self._bus.subscribe(_WAKE_TOPICS) as stream:

            async def _pump() -> None:
                async for env in stream:
                    if env.payload.get("user_id") == user_id:
                        woken.set()

            pump = asyncio.create_task(_pump())

            async def wait() -> None:
                with contextlib.suppress(TimeoutError):
                    await asyncio.wait_for(woken.wait(), _POLL_S)
                woken.clear()

            try:
                yield wait
            finally:
                pump.cancel()
                with contextlib.suppress(asyncio.CancelledError):
                    await pump

    async def _run(self, handle: JobHandle) -> None:
        run_id = str(handle.payload.get("run_id"))
        async with self.sessionmaker() as session:
            row = await session.get(FlowRun, run_id)
            flow = await session.get(Flow, row.flow_id) if row is not None else None
            if row is None or flow is None:
                raise LookupError("The flow run no longer exists.")
            graph = FlowGraph.model_validate(flow.graph_json)
            catalog = await load_catalog(session, row.user_id)
            problems = await flow_problems(
                session, graph, user_id=row.user_id, log_model=flow.log_model, catalog=catalog
            )
            log_row = await session.get(EventLog, row.log_id)
            if log_row is None or log_row.deleted_at is not None:
                raise LookupError("The event log no longer exists.")
            run = _Run(self, row.id, flow.id, row.user_id, row.log_id, graph)
        await run.save(None, "running", job_id=handle.id, error=None, finished_at=None)
        if problems:
            error = "; ".join(p.message for p in problems)[:_ERROR_MAX]
            await run.save(None, "failed", error=error, finished_at=_utcnow())
            raise RuntimeError(error)

        self._runs[run.run_id] = run
        try:
            async with self._wakeups(run.user_id) as wait:
                await self._nodes(run, handle, catalog, wait)
        except BaseException as exc:
            # Cancelled (by the user, or a shutdown) or broken: stop the node's job too.
            with contextlib.suppress(Exception):
                await self._stop_waiting(run, handle.id)
            if handle.cancelled:
                run.nodes = _settled(run.nodes, "cancelled", None)
                await run.save(None, "cancelled", finished_at=_utcnow())
            else:
                error = INTERRUPTED if isinstance(exc, asyncio.CancelledError) else str(exc)
                error = (error or type(exc).__name__)[:_ERROR_MAX]
                run.nodes = _settled(run.nodes, "failed", error)
                await run.save(None, "failed", error=error, finished_at=_utcnow())
            raise
        finally:
            self._runs.pop(run.run_id, None)
        failed = [s for s in run.nodes.values() if s["status"] == "failed"]
        if failed:
            error = f"{len(failed)} node(s) failed: {failed[0]['error']}"[:_ERROR_MAX]
            await run.save(None, "failed", error=error, finished_at=_utcnow())
            raise RuntimeError(error)
        await run.save(None, "ready", finished_at=_utcnow())

    async def _stop_waiting(self, run: _Run, flow_job_id: str) -> None:
        """Cancel the job of the variant *run* waits on - if *run* submitted it (its
        child) and no other run waits on, or is joining, that variant."""
        waiting = run.waiting
        if waiting is None:
            return
        state = await self._coordinator.variants.get(run.user_id, *waiting)
        if state is None or state.job_id is None or state.status not in IN_FLIGHT:
            return
        async with self.sessionmaker() as session:
            job = await session.get(Job, state.job_id)
        # Checked after the reads, with no await before the cancel.
        others = any(r.waiting == waiting for r in self._runs.values() if r is not run)
        if job is not None and job.parent_job_id == flow_job_id and not others:
            await self._runtime.cancel(state.job_id)

    async def _nodes(self, run: _Run, handle: JobHandle, catalog: Catalog, wait: Wait) -> None:
        by_id = {n.id: n for n in run.graph.nodes}
        incoming: dict[str, list[FlowEdge]] = defaultdict(list)
        for e in run.graph.edges:
            incoming[e.target].append(e)
        reran: set[str] = set()
        specs: dict[str, dict[str, Any]] = {}
        for nid in run_order(run.graph):
            node, ups = by_id[nid], incoming[nid]
            try:
                if any(run.nodes[e.source]["status"] != "ready" for e in ups):
                    await run.save(nid, "skipped")
                elif isinstance(node, SourceNode):
                    await self._source(run, node)
                elif isinstance(node, OutputNode):
                    await self._output(run, node, ups[0], catalog, specs)
                else:
                    await self._module(run, handle, node, ups, catalog, wait, reran, specs)
            except (LookupError, ValueError) as exc:  # the node can't run as it stands
                run.waiting = None
                await run.fail(nid, str(exc) or type(exc).__name__)
            except Exception as exc:
                # Fails this node alone: its dependents skip, other branches still run.
                log.exception("flows.node_failed", run_id=run.run_id, node_id=nid)
                run.waiting = None
                await run.fail(nid, str(exc) or type(exc).__name__)

    async def _source(self, run: _Run, node: SourceNode) -> None:
        if isinstance(node.data, LogSourceData):
            await run.save(node.id, "ready")
            return
        async with self.sessionmaker() as session:
            found = await workspace_type(session, run.user_id, node.data.ref)
        if found is None:
            await run.fail(node.id, f"No artifact {node.data.ref!r} in your workspace.")
            return
        ref = {"dataset_id": None, "artifact_type": found, "ref": node.data.ref}
        await run.save(node.id, "ready", refs=[ref])

    async def _module(
        self,
        run: _Run,
        handle: JobHandle,
        node: ModuleNode,
        ups: list[FlowEdge],
        catalog: Catalog,
        wait: Wait,
        reran: set[str],
        specs: dict[str, dict[str, Any]],
    ) -> None:
        mid = node.data.module_id
        manifest = catalog.manifests[mid]
        started = _utcnow()
        await run.save(node.id, "running")
        inputs: dict[str, str] = {}
        for e in ups:
            if e.source_handle == LOG_HANDLE:
                continue  # the log: every module reads it
            # A module output by its dataset id, an artifact source by its ref.
            refs = run.nodes[e.source]["refs"]
            ref = next(
                (r["ref"] for r in refs if e.source_handle in (r["dataset_id"], r["ref"])), None
            )
            if ref is None:
                await run.fail(node.id, f"The upstream node produced no {e.source_handle!r}.")
                return
            inputs[str(e.target_handle)] = ref
        # A ref its port reads unconnected anyway is no explicit input.
        default = ModuleInputs.bind(manifest.inputs, catalog.manifests, run.log_id, run.user_id)
        spec = RunSpec(
            params=node.data.params,
            inputs={k: v for k, v in inputs.items() if str(default.ref(k)) != v},
        )
        written = await self._written(run, manifest, inputs, default, wait)
        if not spec.params and not spec.inputs:
            own = await self._default_run(run, run.log_id, mid, wait)
            finished = own.finished_at if own is not None else None
            if finished is None or not (
                any(at > finished for at in written)
                or (finished < started and any(e.source in reran for e in ups))
            ):
                await self._default_node(run, node, catalog, own, started, reran)
                return
            # The default run read an older input: the variant of no spec reads it anew.

        variants = self._coordinator.variants
        key = run_variant_key(spec.canonical())
        run.waiting = (run.log_id, mid, key)  # before it is joined: a cancel elsewhere sees it
        before = await variants.get(run.user_id, run.log_id, mid, key)
        last = before.finished_at if before is not None else None
        state = await variants.ensure(
            run.user_id,
            run.log_id,
            mid,
            spec,
            force=True,
            rerun=any(e.source in reran for e in ups)
            or (last is not None and any(at > last for at in written)),
            parent_job_id=handle.id,
        )
        reused = state.status == "ready"
        if state.status in IN_FLIGHT:
            await run.save(node.id, "running", variant=state.variant, job_id=state.job_id)
        while state.status in IN_FLIGHT:
            await wait()
            state = await variants.get(run.user_id, run.log_id, mid, state.variant) or VariantState(
                state.variant, "failed", state.spec, error="The run is gone."
            )
        run.waiting = None
        if state.status != "ready":
            error = state.error or f"The run ended {state.status}."
            if state.status == "stale":
                error = "The log changed while it ran - run the flow again."
            await run.save(
                node.id, "failed", variant=state.variant, job_id=state.job_id, error=error
            )
            return
        if not reused:
            reran.add(node.id)
        specs[node.id] = state.spec
        await run.save(
            node.id,
            "ready",
            variant=state.variant,
            job_id=state.job_id,
            # Base type ids (`petri_net`), like the palette's.
            refs=[{**r, "artifact_type": type_id(r["artifact_type"]) or ""} for r in state.refs],
            reused=reused,
            error=None,
        )

    async def _written(
        self,
        run: _Run,
        manifest: Manifest,
        inputs: dict[str, str],
        default: ModuleInputs,
        wait: Wait,
    ) -> list[datetime]:
        """When each input the node reads was last written - every connected ref and
        every other port's default binding - its producer brought up to date first. An
        unconnected optional port's producer never runs for it, as for the default run
        (``ModuleLoader.input_producers``): only what it already wrote counts."""
        ports = {p.id: p for p in manifest.inputs if not is_log_type(p.artifact_type)}
        bound = {k: str(r) for k in ports if (r := default.ref(k)) is not None} | inputs
        producers: dict[tuple[str, str, str | None], bool] = {}  # -> run it
        for port, text in bound.items():
            r = ArtifactRef.parse(text)
            if r.module_id != WORKSPACE_MODULE_ID:  # an upload never changes
                at = (r.log_id, r.module_id, r.variant)
                producers[at] = producers.get(at, False) or port in inputs or ports[port].required
        written: list[datetime] = []
        states: dict[str, RunState] = {}
        for (log_id, producer, key), refresh in sorted(producers.items(), key=str):
            what = f"{producer} (input of {manifest.name}): "
            found: RunState | VariantState | None
            if key is not None:
                found = await self._variant_input(run, log_id, producer, key, wait, what)
            elif refresh:
                found = await self._default_run(run, log_id, producer, wait, what)
            else:
                if not states:
                    listed = await self._coordinator.list_states(run.user_id, log_id)
                    states = {s.module_id: s for s in listed}
                found = states.get(producer)
            if found is not None and found.finished_at is not None:
                written.append(found.finished_at)
        return written

    async def _variant_input(
        self, run: _Run, log_id: str, module_id: str, key: str, wait: Wait, what: str
    ) -> VariantState | None:
        """The run variant an input ref names, re-run with its own spec when stale or
        failed and waited for: its ready state, None when it is gone. LookupError
        (prefixed *what*) when it can't become ready."""
        variants = self._coordinator.variants
        state = await variants.get(run.user_id, log_id, module_id, key)
        if state is None or state.status == "ready":
            return state
        spec = RunSpec.model_validate(state.spec)
        state = await variants.ensure(run.user_id, log_id, module_id, spec, force=True)
        while state.status in IN_FLIGHT:
            await wait()
            state = await variants.get(run.user_id, log_id, module_id, key) or VariantState(
                key, "failed", state.spec, error="The run is gone."
            )
        if state.status != "ready":
            raise LookupError(f"{what}{state.error or f'its run is {state.status}'}")
        return state

    async def _default_node(
        self,
        run: _Run,
        node: ModuleNode,
        catalog: Catalog,
        state: RunState | None,
        started: datetime,
        reran: set[str],
    ) -> None:
        """A module node that is the module's default run, brought up to date (*state*)."""
        module_id = node.data.module_id
        finished = state.finished_at if state is not None else None
        reused = finished is None or finished < started
        if not reused:
            reran.add(node.id)
        refs = [
            {
                "dataset_id": d.id,
                "artifact_type": type_id(d.artifact_type) or "",
                "ref": str(ArtifactRef(run.log_id, module_id, str(d.cache_key))),
            }
            for d in outputs(catalog.manifests[module_id]).values()
        ]
        job_id = state.job_id if state is not None else None
        await run.save(node.id, "ready", job_id=job_id, refs=refs, reused=reused, error=None)

    async def _default_run(
        self, run: _Run, log_id: str, module_id: str, wait: Wait, what: str = ""
    ) -> RunState | None:
        """Run (or refresh) *module_id*'s default run for *log_id* and wait for it: its
        ready state, None when it has nothing to run. LookupError (prefixed *what*) when
        it can't become ready."""
        try:
            states = await self._coordinator.ensure(run.user_id, log_id, module_id, force=True)
            while (mine := next((s for s in states if s.module_id == module_id), None)) is not None:
                if mine.status not in IN_FLIGHT:
                    break
                await wait()
                states = await self._coordinator.ensure(run.user_id, log_id, module_id)
        except LookupError as exc:
            raise LookupError(f"{what}{exc}") from exc
        if mine is None:
            blocked = next((s for s in states if s.status == "unavailable"), None)
            if blocked is None:
                return None
            raise LookupError(f"{what}{blocked.error or 'unavailable'}")
        if mine.status != "ready":
            raise LookupError(f"{what}{mine.error or f'its run is {mine.status}'}")
        return mine

    async def _output(
        self,
        run: _Run,
        node: OutputNode,
        edge: FlowEdge,
        catalog: Catalog,
        specs: dict[str, dict[str, Any]],
    ) -> None:
        upstream = run.nodes[edge.source]
        produced = next(
            (r for r in upstream["refs"] if r["dataset_id"] == edge.source_handle), None
        )
        module = next(
            (n for n in run.graph.nodes if n.id == edge.source and isinstance(n, ModuleNode)), None
        )
        if produced is None or module is None:
            await run.fail(node.id, f"The module has no output {edge.source_handle!r}.")
            return
        ref = str(produced["ref"])
        async with self.sessionmaker() as session:
            row = await self._artifact(session, run.user_id, ref)
            if row is None:
                # The variant settled before the workspace indexed it (or the index
                # was pruned): index it now.
                await register_run(
                    session,
                    user_id=run.user_id,
                    log_id=run.log_id,
                    manifest=catalog.manifests[module.data.module_id],
                    owned_producers=catalog.manifests,
                    variant=upstream["variant"],
                    spec=specs.get(edge.source),
                )
                row = await self._artifact(session, run.user_id, ref)
            if row is None:
                await run.fail(node.id, f"The module did not produce {edge.source_handle!r}.")
                return
            if node.data.name.strip():
                row.name = node.data.name.strip()
            if node.data.pin:
                row.pinned = True
            artifact_id = row.id
            await session.commit()
        await run.save(node.id, "ready", refs=[produced], artifact_id=artifact_id)

    @staticmethod
    async def _artifact(session: AsyncSession, user_id: str, ref: str) -> Artifact | None:
        stmt = select(Artifact).where(Artifact.user_id == user_id, Artifact.ref == ref)
        return (await session.execute(stmt)).scalar_one_or_none()


_executor: FlowExecutor | None = None


def get_flow_executor() -> FlowExecutor | None:
    return _executor


def set_flow_executor(executor: FlowExecutor | None) -> None:
    global _executor
    _executor = executor
