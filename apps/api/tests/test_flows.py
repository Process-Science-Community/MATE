"""Flows (`mate.api.flows`, `/api/v1/flows`): module node graphs run on a log.

Runs use the `artifact_producer` -> `artifact_consumer` fixtures (a required
`petri_net` input); the palette's converter suggestions use the real `interop`
module. A plain module node is the module's default run; `_taggable` gives a fixture
a per-log param so a node that sets it runs as a variant.
"""

from __future__ import annotations

import asyncio
import contextlib
import importlib.util
from collections.abc import AsyncGenerator
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import pytest
import sqlalchemy as sa
from alembic.migration import MigrationContext
from alembic.operations import Operations
from httpx import AsyncClient

from mate.api.db.engine import get_sessionmaker
from mate.api.db.models import Artifact, EventLog, Flow, FlowRun, Job
from mate.api.flows.graph import FlowGraph, graph_errors, run_order
from mate.api.mcp.errors import MCPToolError
from mate.api.mcp.toolsets import flows as mcp_flows
from mate.api.modules.cache import ResultCache

from .conftest import TEST_USER_ID
from .test_log_module_config import (
    OTHER_ID,
    _seed_user,  # pyright: ignore[reportPrivateUsage]
)
from .test_mcp_analysis import (
    _ctx,  # pyright: ignore[reportPrivateUsage]
    _principal,  # pyright: ignore[reportPrivateUsage]
    _seed_log,  # pyright: ignore[reportPrivateUsage]
)
from .test_module_processing import import_sample, mods_client, module_jobs, run_row, wait_until

PAIR = ("artifact_producer", "artifact_consumer")
FLOWS = "/api/v1/flows"


def _now() -> datetime:
    return datetime.now(UTC).replace(tzinfo=None)


def _node(nid: str, type_: str, data: dict[str, Any]) -> dict[str, Any]:
    return {"id": nid, "type": type_, "position": {"x": 0, "y": 0}, "data": data}


def _edge(eid: str, source: str, handle: str, target: str, port: str) -> dict[str, Any]:
    return {
        "id": eid,
        "source": source,
        "sourceHandle": handle,
        "target": target,
        "targetHandle": port,
    }


def _pair_graph(
    name: str = "My net", pin: bool = True, params: dict[str, Any] | None = None
) -> dict[str, Any]:
    """log; producer (with *params*) -> consumer; producer -> output (named + pinned)."""
    return {
        "nodes": [
            _node("log", "source", {"kind": "log"}),
            _node("prod", "module", {"module_id": "artifact_producer", "params": params or {}}),
            _node("cons", "module", {"module_id": "artifact_consumer"}),
            _node("out", "output", {"name": name, "pin": pin}),
        ],
        "edges": [
            _edge("e1", "prod", "model", "cons", "reference_model"),
            _edge("e2", "prod", "model", "out", "in"),
        ],
    }


async def _create(c: AsyncClient, graph: dict[str, Any] | None = None, **body: Any) -> Any:
    resp = await c.post(FLOWS, json={"name": "Pair", "graph": graph or _pair_graph(), **body})
    assert resp.status_code == 201, resp.text
    return resp.json()


async def _settled(c: AsyncClient, flow_id: str, run_id: str) -> Any:
    out: Any = {}

    async def _done() -> bool:
        nonlocal out
        out = (await c.get(f"{FLOWS}/{flow_id}/runs/{run_id}")).json()
        return out["status"] not in ("queued", "running")

    await wait_until(_done, what="the flow run to settle")
    return out


async def _run(c: AsyncClient, flow_id: str, log_id: str) -> Any:
    resp = await c.post(f"{FLOWS}/{flow_id}/runs", json={"log_id": log_id})
    assert resp.status_code == 202, resp.text
    return await _settled(c, flow_id, resp.json()["id"])


def _statuses(run: Any) -> dict[str, str]:
    return {nid: n["status"] for nid, n in run["nodes"].items()}


async def _job(job_id: str) -> Job:
    async with get_sessionmaker()() as session:
        job = await session.get(Job, job_id)
    assert job is not None
    return job


@contextlib.asynccontextmanager
async def _flow_events() -> AsyncGenerator[list[dict[str, Any]]]:
    from mate.api.events import get_event_bus

    out: list[dict[str, Any]] = []
    subscribed = asyncio.Event()

    async def _collect() -> None:
        async with get_event_bus().subscribe(["flow_run.updated"]) as stream:
            subscribed.set()
            async for env in stream:
                out.append(env.payload)

    task = asyncio.create_task(_collect())
    await subscribed.wait()
    try:
        yield out
    finally:
        task.cancel()


async def _foreign_log(log_id: str) -> None:
    await _seed_user(OTHER_ID)
    async with get_sessionmaker()() as session:
        if await session.get(EventLog, log_id) is None:
            session.add(
                EventLog(
                    id=log_id, user_id=OTHER_ID, name="theirs", status="ready", created_at=_now()
                )
            )
            await session.commit()


def _taggable(monkeypatch: pytest.MonkeyPatch, *module_ids: str) -> None:
    """Give the loaded modules a per-log param `tag` their code ignores: a node that
    sets it runs as a run variant instead of the module's default run."""
    from mate.api.modules import get_module_loader

    tag = {"type": "integer", "ui": {"scope": "dataset"}}
    for mid in module_ids:
        manifest = get_module_loader().loaded[mid].manifest
        monkeypatch.setattr(manifest, "config_schema", {"properties": {"tag": tag}})


def _optional(monkeypatch: pytest.MonkeyPatch) -> None:
    """artifact_consumer's port optional, as conformance's and interop's are: its
    producer neither runs first nor stales the consumer's default run."""
    from mate.api.modules import get_module_loader

    port = get_module_loader().loaded["artifact_consumer"].manifest.inputs[0]
    monkeypatch.setattr(port, "required", False)


async def _unpin() -> None:
    """A pinned variant is never evicted, which would skew later GC tests that share
    this user's results root."""
    async with get_sessionmaker()() as session:
        await session.execute(
            sa.update(Artifact).where(Artifact.user_id == TEST_USER_ID).values(pinned=False)
        )
        await session.commit()


async def _bump(log_id: str) -> int:
    """A data change: the log's next data revision."""
    async with get_sessionmaker()() as session:
        row = await session.get(EventLog, log_id)
        assert row is not None
        row.data_revision = int(row.data_revision or 0) + 1
        await session.commit()
        return row.data_revision


async def _default_ready(c: AsyncClient, log_id: str) -> str:
    """Run the producer's default run on the log; its model's ref once indexed."""
    resp = await c.post(f"/api/v1/event-logs/{log_id}/module-runs/artifact_producer")
    assert resp.status_code == 200, resp.text
    ref = f"{log_id}/artifact_producer/model"

    async def _indexed() -> bool:
        row = await run_row(log_id, "artifact_producer")
        async with get_sessionmaker()() as session:
            found = await session.execute(sa.select(Artifact.id).where(Artifact.ref == ref))
            return row is not None and row.status == "ready" and found.first() is not None

    await wait_until(_indexed, what="the producer's default run")
    return ref


# --------------------------------------------------------------------------- #
# Pure
# --------------------------------------------------------------------------- #


def test_graph_errors_name_the_culprits_and_run_order_is_topological() -> None:
    def errors(graph: dict[str, Any]) -> list[tuple[str | None, str | None]]:
        return [(p.node_id, p.edge_id) for p in graph_errors(FlowGraph.model_validate(graph))]

    ok = _pair_graph()
    assert errors(ok) == []
    assert run_order(FlowGraph.model_validate(ok)) == ["log", "prod", "cons", "out"]
    # Upstream first, whatever the node order.
    diamond = {
        "nodes": [_node("d", "output", {}), *ok["nodes"][1:3], _node("x", "output", {})],
        "edges": [*ok["edges"][:1], _edge("e3", "prod", "model", "x", "in")],
    }
    order = run_order(FlowGraph.model_validate(diamond))
    assert order.index("prod") < order.index("cons") and order.index("prod") < order.index("x")

    dup = {"nodes": [*ok["nodes"], ok["nodes"][1]], "edges": ok["edges"]}
    assert errors(dup) == [("prod", None)]
    two_logs = {"nodes": [*ok["nodes"], _node("log2", "source", {"kind": "log"})], "edges": []}
    assert errors(two_logs) == [("log2", None)]
    dangling = {"nodes": ok["nodes"], "edges": [_edge("e9", "prod", "model", "nope", "in")]}
    assert errors(dangling) == [(None, "e9")]
    twice = {"nodes": ok["nodes"], "edges": [*ok["edges"], _edge("e3", "log", "log", "out", "in")]}
    assert errors(twice) == [(None, "e3")]
    into_source = {"nodes": ok["nodes"], "edges": [_edge("e4", "prod", "model", "log", "x")]}
    assert errors(into_source) == [(None, "e4")]
    cycle = {
        "nodes": ok["nodes"],
        "edges": [*ok["edges"], _edge("back", "cons", "seen", "prod", "x")],
    }
    assert errors(cycle) == [(None, "back")]


def test_migration_up_down_up(monkeypatch: pytest.MonkeyPatch) -> None:
    path = Path(__file__).resolve().parents[1] / "alembic" / "versions" / "0021_flows.py"
    spec = importlib.util.spec_from_file_location("mig_0021", path)
    assert spec is not None and spec.loader is not None
    mig = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mig)
    assert mig.down_revision == "0020_artifacts"
    eng = sa.create_engine("sqlite://")
    with eng.connect() as conn:
        conn.exec_driver_sql("CREATE TABLE users (id VARCHAR(36) PRIMARY KEY)")
        conn.exec_driver_sql("CREATE TABLE process_logs (id VARCHAR(36) PRIMARY KEY)")

        def run(fn: str) -> None:
            monkeypatch.setattr(mig, "op", Operations(MigrationContext.configure(conn)))
            getattr(mig, fn)()

        def tables() -> set[str]:
            return set(sa.inspect(conn).get_table_names())

        run("upgrade")
        run("upgrade")  # idempotent
        assert {"flows", "flow_runs"} <= tables()
        assert {i["name"] for i in sa.inspect(conn).get_indexes("flow_runs")} == {
            "ix_flow_runs_flow_created_at"
        }
        run("downgrade")
        assert not {"flows", "flow_runs"} & tables()
        run("upgrade")
        assert {"flows", "flow_runs"} <= tables()


# --------------------------------------------------------------------------- #
# CRUD + validation
# --------------------------------------------------------------------------- #


@pytest.mark.asyncio
async def test_crud_pins_save_errors_and_ownership(tmp_path: Path) -> None:
    async with mods_client(tmp_path, *PAIR) as c:
        flow = await _create(c, description="first")
        fid = flow["id"]
        assert flow["log_model"] == "case_centric" and flow["last_run"] is None
        assert flow["graph"] == FlowGraph.model_validate(_pair_graph()).stored()
        assert set(flow["pins"]) == set(PAIR)
        assert flow["pins"]["artifact_producer"].startswith("0.1.0+")
        assert [f["id"] for f in (await c.get(FLOWS)).json()][:1] == [fid]

        # PUT replaces name, description and graph; pins follow the module nodes.
        graph = _pair_graph()
        graph["nodes"] = graph["nodes"][:2]
        graph["edges"] = []
        resp = await c.put(f"{FLOWS}/{fid}", json={"name": "Renamed", "graph": graph})
        assert resp.status_code == 200, resp.text
        got = (await c.get(f"{FLOWS}/{fid}")).json()
        assert (got["name"], got["description"]) == ("Renamed", None)
        assert [n["id"] for n in got["graph"]["nodes"]] == ["log", "prod"]
        assert set(got["pins"]) == {"artifact_producer"}

        # What the graph alone gets wrong is never saved - the culprits are named.
        bad = _pair_graph()
        bad["edges"].append(_edge("loop", "cons", "x", "prod", "y"))
        resp = await c.put(f"{FLOWS}/{fid}", json={"name": "x", "graph": bad})
        assert resp.status_code == 422
        assert resp.json()["detail"]["errors"] == [
            {"node_id": None, "edge_id": "loop", "message": "This connection closes a cycle."}
        ]
        assert (await c.get(f"{FLOWS}/{fid}")).json()["name"] == "Renamed"
        dup = {"nodes": [_node("a", "output", {}), _node("a", "output", {})], "edges": []}
        resp = await c.post(FLOWS, json={"name": "dup", "graph": dup})
        assert resp.status_code == 422
        assert resp.json()["detail"]["errors"][0]["node_id"] == "a"
        # An unfinished graph saves fine (validate lists what is missing).
        resp = await c.post(FLOWS, json={"name": "wip", "graph": {"nodes": [], "edges": []}})
        assert resp.status_code == 201

        # Someone else's flow is a 404 everywhere; so is a run on their log.
        await _seed_user(OTHER_ID)
        async with get_sessionmaker()() as session:
            theirs = Flow(
                id=f"flow-other-{fid[:8]}",
                user_id=OTHER_ID,
                name="theirs",
                log_model="case_centric",
                graph_json=_pair_graph(),
                pins_json={},
            )
            session.add(theirs)
            await session.commit()
        url = f"{FLOWS}/{theirs.id}"
        assert (await c.get(url)).status_code == 404
        assert (await c.put(url, json={"name": "x", "graph": _pair_graph()})).status_code == 404
        assert (await c.post(f"{url}/validate", json={})).status_code == 404
        assert (await c.post(f"{url}/runs", json={"log_id": "x"})).status_code == 404
        assert (await c.get(f"{url}/runs")).status_code == 404
        assert (await c.delete(url)).status_code == 404
        assert theirs.id not in {f["id"] for f in (await c.get(FLOWS)).json()}
        foreign = f"flow-foreign-log-{fid[:8]}"
        await _foreign_log(foreign)
        resp = await c.post(f"{FLOWS}/{fid}/runs", json={"log_id": foreign})
        assert resp.status_code == 404
        resp = await c.post(f"{FLOWS}/{fid}/validate", json={"log_id": foreign})
        assert resp.status_code == 404

        assert (await c.delete(f"{FLOWS}/{fid}")).status_code == 204
        assert (await c.get(f"{FLOWS}/{fid}")).status_code == 404


@pytest.mark.asyncio
async def test_validate_lists_every_problem_and_a_run_refuses_them(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from mate.api.modules import get_module_loader

    async with mods_client(tmp_path, *PAIR) as c:
        log_id = await import_sample(c, name="flow validate")
        graph = {
            "nodes": [
                _node("log", "source", {"kind": "log"}),
                _node("a1", "source", {"kind": "artifact", "ref": ""}),
                _node("a2", "source", {"kind": "artifact", "ref": f"{log_id}/nobody/x"}),
                _node("ghost", "module", {"module_id": "ghost_mod"}),
                _node("prod", "module", {"module_id": "artifact_producer", "params": {"x": 1}}),
                _node("cons", "module", {"module_id": "artifact_consumer"}),
                _node("out", "output", {}),
                _node("out2", "output", {}),
            ],
            "edges": [
                _edge("typed", "log", "log", "cons", "reference_model"),
                _edge("nohandle", "prod", "nope", "out", "in"),
                _edge("noport", "prod", "model", "ghost", "whatever"),
            ],
        }
        flow = await _create(c, graph)
        resp = await c.post(f"{FLOWS}/{flow['id']}/validate", json={"log_id": log_id})
        assert resp.status_code == 200
        body = resp.json()
        assert body["ok"] is False
        found = {(e["node_id"], e["edge_id"]): e["message"] for e in body["errors"]}
        assert found == {
            ("a1", None): "Pick an artifact from your workspace.",
            ("a2", None): f"No artifact '{log_id}/nobody/x' in your workspace.",
            ("ghost", None): "Module 'ghost_mod' is not installed.",
            ("prod", None): "Not a per-log parameter of Artifact Producer: x.",
            (None, "typed"): "Artifact Consumer input 'reference_model' takes petri_net, "
            "not event_log.",
            (None, "nohandle"): "Artifact Producer has no output 'nope'.",
            ("out2", None): "Connect a module output to this output.",
        }
        resp = await c.post(f"{FLOWS}/{flow['id']}/runs", json={"log_id": log_id})
        assert resp.status_code == 422
        assert len(resp.json()["detail"]["errors"]) == len(found)

        # A required port with no edge and no installed `from:` producer.
        lonely = {"nodes": [_node("cons", "module", {"module_id": "artifact_consumer"})]}
        lone = await _create(c, {**lonely, "edges": []})
        ok = await c.post(f"{FLOWS}/{lone['id']}/validate", json={"log_id": log_id})
        assert ok.json() == {"ok": True, "errors": []}  # the default binding covers it
        port = get_module_loader().loaded["artifact_consumer"].manifest.inputs[0]
        monkeypatch.setattr(port, "from_", None)
        resp = await c.post(f"{FLOWS}/{lone['id']}/validate", json={})
        assert resp.json()["errors"] == [
            {
                "node_id": "cons",
                "edge_id": None,
                "message": "Connect Artifact Consumer's required input 'reference_model' "
                "(petri_net).",
            }
        ]

        # An object-centric flow: case-centric modules and logs don't fit it.
        oc = await _create(c, {**lonely, "edges": []}, log_model="object_centric")
        resp = await c.post(f"{FLOWS}/{oc['id']}/validate", json={"log_id": log_id})
        assert [e["message"] for e in resp.json()["errors"]] == [
            "This flow runs on object-centric logs.",
            "Artifact Consumer does not work on this flow's kind of log.",
        ]
        empty = await _create(c, {"nodes": [], "edges": []})
        resp = await c.post(f"{FLOWS}/{empty['id']}/validate")
        assert [e["message"] for e in resp.json()["errors"]] == [
            "Add a module: the flow has nothing to run."
        ]


# --------------------------------------------------------------------------- #
# Runs
# --------------------------------------------------------------------------- #


@pytest.mark.asyncio
async def test_run_chains_variants_memoises_and_reruns_downstream(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    async with mods_client(tmp_path, *PAIR) as c:
        log_id = await import_sample(c, name="flow run")
        _taggable(monkeypatch, "artifact_producer")
        flow = await _create(c, _pair_graph(params={"tag": 1}))
        fid = flow["id"]
        async with _flow_events() as events:
            run = await _run(c, fid, log_id)
        assert run["status"] == "ready", run
        assert _statuses(run) == {"log": "ready", "prod": "ready", "cons": "ready", "out": "ready"}
        prod, cons, out = run["nodes"]["prod"], run["nodes"]["cons"], run["nodes"]["out"]
        ref = f"{log_id}/artifact_producer@{prod['variant']}/model"
        assert prod["refs"] == [{"dataset_id": "model", "artifact_type": "petri_net", "ref": ref}]
        assert not prod["reused"] and not cons["reused"]
        # The consumer's variant read the producer's variant through the edge.
        seen = await ResultCache(
            log_id, "artifact_consumer", TEST_USER_ID, run_variant=cons["variant"]
        ).get("seen")
        assert seen["ref"] == ref and seen["places"] == 4
        # The output node named and pinned the producer's artifact.
        assert out["refs"] == prod["refs"]
        async with get_sessionmaker()() as session:
            row = await session.get(Artifact, out["artifact_id"])
        assert row is not None and (row.ref, row.name, row.pinned) == (ref, "My net", True)
        # One detached parent job; the variant jobs are its children.
        parent = await _job(run["job_id"])
        assert (parent.type, parent.status) == ("flow.run", "completed")
        [pjob] = await module_jobs(log_id, "artifact_producer")
        [cjob] = await module_jobs(log_id, "artifact_consumer")
        assert pjob.parent_job_id == cjob.parent_job_id == run["job_id"]
        assert await run_row(log_id, "artifact_producer") is None  # no default run
        # Every node change was published, for the owner only.
        assert {e["user_id"] for e in events} == {TEST_USER_ID}
        assert {"run_id": run["id"], "node_id": "out", "status": "ready"}.items() <= next(
            e for e in events if e["node_id"] == "out" and e["status"] == "ready"
        ).items()
        assert events[-1]["node_id"] is None and events[-1]["status"] == "ready"
        listed = next(f for f in (await c.get(FLOWS)).json() if f["id"] == fid)
        assert (listed["last_run"]["id"], listed["last_run"]["status"]) == (run["id"], "ready")

        # Nothing changed: every module node reuses its fresh variant, no new jobs.
        again = await _run(c, fid, log_id)
        assert again["status"] == "ready"
        assert again["nodes"]["prod"]["reused"] and again["nodes"]["cons"]["reused"]
        assert again["nodes"]["cons"]["variant"] == cons["variant"]
        assert len(await module_jobs(log_id, "artifact_producer")) == 1
        assert len(await module_jobs(log_id, "artifact_consumer")) == 1
        runs = (await c.get(f"{FLOWS}/{fid}/runs")).json()
        assert [r["id"] for r in runs] == [again["id"], run["id"]]

        # Only the producer re-runs (its variant was deleted): the consumer's ref is
        # unchanged, so only `rerun` makes it read the new result.
        url = f"/api/v1/event-logs/{log_id}/module-runs/artifact_producer/variants"
        assert (await c.delete(f"{url}/{prod['variant']}")).status_code == 204
        third = await _run(c, fid, log_id)
        assert third["status"] == "ready", third
        assert not third["nodes"]["prod"]["reused"] and not third["nodes"]["cons"]["reused"]
        assert len(await module_jobs(log_id, "artifact_consumer")) == 2

        # A data change makes every variant stale: both run again.
        async with get_sessionmaker()() as session:
            log_row = await session.get(EventLog, log_id)
            assert log_row is not None
            log_row.data_revision = int(log_row.data_revision or 0) + 1
            await session.commit()
        fourth = await _run(c, fid, log_id)
        assert fourth["status"] == "ready", fourth
        assert not fourth["nodes"]["prod"]["reused"] and not fourth["nodes"]["cons"]["reused"]
        assert len(await module_jobs(log_id, "artifact_producer")) == 3
        assert len(await module_jobs(log_id, "artifact_consumer")) == 3

        # MCP: list, run and read.
        await _seed_log(log_id)  # egress consent
        await _seed_user(OTHER_ID)
        await _seed_log(f"flow-mcp-other-{log_id[:8]}", OTHER_ID)
        listed_mcp = await mcp_flows.list_flows(_ctx(_principal()))
        assert fid in {f["id"] for f in listed_mcp}
        started = await mcp_flows.run_flow(_ctx(_principal()), flow_id=fid, log_id=log_id)
        assert started["status"] == "queued" and started["job_id"]
        done = await _settled(c, fid, started["id"])
        got = await mcp_flows.get_flow_run(_ctx(_principal()), run_id=started["id"])
        assert got["status"] == done["status"] == "ready"
        assert got["nodes"]["cons"]["reused"]
        with pytest.raises(MCPToolError, match="not found"):
            await mcp_flows.get_flow_run(_ctx(_principal(OTHER_ID)), run_id=started["id"])
        with pytest.raises(MCPToolError, match="not found"):
            await mcp_flows.run_flow(_ctx(_principal(OTHER_ID)), flow_id=fid, log_id=log_id)
        await _unpin()


@pytest.mark.asyncio
async def test_a_plain_module_node_is_the_modules_default_run(tmp_path: Path) -> None:
    async with mods_client(tmp_path, *PAIR) as c:
        log_id = await import_sample(c, name="flow default runs")
        flow = await _create(c)  # no params; the consumer reads what it reads unconnected
        run = await _run(c, flow["id"], log_id)
        assert run["status"] == "ready", run
        prod, cons, out = run["nodes"]["prod"], run["nodes"]["cons"], run["nodes"]["out"]
        ref = f"{log_id}/artifact_producer/model"
        assert prod["variant"] is None and cons["variant"] is None
        assert prod["refs"] == [{"dataset_id": "model", "artifact_type": "petri_net", "ref": ref}]
        assert not prod["reused"] and not cons["reused"]
        for mid in PAIR:
            row = await run_row(log_id, mid)
            assert row is not None and row.status == "ready"
            [job] = await module_jobs(log_id, mid)
            assert job.id == row.job_id and job.parent_job_id is None
            # No variant: nothing computed twice, no second copy in the workspace.
            assert not list(ResultCache(log_id, mid, TEST_USER_ID).dir.glob("_r_*"))
        seen = await ResultCache(log_id, "artifact_consumer", TEST_USER_ID).get("seen")
        assert seen["ref"] == ref
        async with get_sessionmaker()() as session:
            rows = await session.execute(
                sa.select(Artifact).where(Artifact.ref.like(f"{log_id}/artifact_producer%"))
            )
            [artifact] = rows.scalars().all()
        assert (artifact.id, artifact.ref, artifact.name, artifact.pinned) == (
            out["artifact_id"],
            ref,
            "My net",
            True,
        )

        again = await _run(c, flow["id"], log_id)
        assert again["status"] == "ready", again
        assert again["nodes"]["prod"]["reused"] and again["nodes"]["cons"]["reused"]
        assert [len(await module_jobs(log_id, mid)) for mid in PAIR] == [1, 1]
        await _unpin()


@pytest.mark.asyncio
async def test_an_unconnected_required_input_runs_its_default_producer_first(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    async with mods_client(tmp_path, *PAIR) as c:
        log_id = await import_sample(c, name="flow default binding")
        _taggable(monkeypatch, "artifact_consumer")
        cons = {"module_id": "artifact_consumer", "params": {"tag": 1}}
        flow = await _create(c, {"nodes": [_node("cons", "module", cons)], "edges": []})
        run = await _run(c, flow["id"], log_id)
        assert run["status"] == "ready", run
        assert run["nodes"]["cons"]["variant"] is not None
        row = await run_row(log_id, "artifact_producer")
        assert row is not None and row.status == "ready"
        seen = await ResultCache(
            log_id, "artifact_consumer", TEST_USER_ID, run_variant=run["nodes"]["cons"]["variant"]
        ).get("seen")
        assert seen["ref"] == f"{log_id}/artifact_producer/model"


@pytest.mark.asyncio
async def test_artifact_sources_refresh_their_producer_and_rerun_the_consumer(
    tmp_path: Path,
) -> None:
    async with mods_client(tmp_path, *PAIR) as c:
        x = await import_sample(c, name="flow source X")
        y = await import_sample(c, name="flow source Y")
        xref, yref = await _default_ready(c, x), await _default_ready(c, y)
        graph = {
            "nodes": [
                _node("sx", "source", {"kind": "artifact", "ref": xref}),
                _node("sy", "source", {"kind": "artifact", "ref": yref}),
                _node("cx", "module", {"module_id": "artifact_consumer"}),
                _node("cy", "module", {"module_id": "artifact_consumer"}),
            ],
            "edges": [
                _edge("e1", "sx", xref, "cx", "reference_model"),
                _edge("e2", "sy", yref, "cy", "reference_model"),
            ],
        }
        flow = await _create(c, graph)

        async def _seen(run: Any) -> Any:
            key = run["nodes"]["cx"]["variant"]
            return await ResultCache(y, "artifact_consumer", TEST_USER_ID, run_variant=key).get(
                "seen"
            )

        first = await _run(c, flow["id"], y)
        assert first["status"] == "ready", first
        # Another log's model is an explicit input (a variant); the run log's own is
        # what the port reads unconnected (the default run).
        assert (
            first["nodes"]["cx"]["variant"] is not None and first["nodes"]["cy"]["variant"] is None
        )
        assert (await _seen(first))["ref"] == xref

        # X's data changes: the run refreshes X's producer before the consumer reads it,
        # and the consumer re-runs though nothing of its own changed.
        revision = await _bump(x)
        second = await _run(c, flow["id"], y)
        assert second["status"] == "ready", second
        row = await run_row(x, "artifact_producer")
        assert row is not None and (row.status, row.data_revision) == ("ready", revision)
        assert not second["nodes"]["cx"]["reused"] and second["nodes"]["cy"]["reused"]

        # X's model is rewritten outside the flow: the consumer re-runs and reads it.
        await _bump(x)
        await _default_ready(c, x)
        cache = ResultCache(x, "artifact_producer", TEST_USER_ID)
        model = await cache.get("model")
        await cache.set("model", {**model, "places": [{"id": f"p{i}"} for i in range(9)]})
        third = await _run(c, flow["id"], y)
        assert third["status"] == "ready", third
        assert not third["nodes"]["cx"]["reused"]
        assert (await _seen(third))["places"] == 9

        # Y's data changes: its producer refreshes before Y's own consumer re-runs.
        revision = await _bump(y)
        fourth = await _run(c, flow["id"], y)
        assert fourth["status"] == "ready", fourth
        row = await run_row(y, "artifact_producer")
        assert row is not None and (row.status, row.data_revision) == ("ready", revision)
        assert not fourth["nodes"]["cy"]["reused"]


@pytest.mark.asyncio
async def test_a_plain_node_whose_default_run_read_an_older_input_runs_anew(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from mate.api.modules import get_module_loader

    async with mods_client(tmp_path, *PAIR) as c:
        log_id = await import_sample(c, name="flow older input")
        _optional(monkeypatch)
        cons = _node("cons", "module", {"module_id": "artifact_consumer"})
        alone = await _create(c, {"nodes": [cons], "edges": []})

        async def _seen(run: Any) -> Any:
            key = run["nodes"]["cons"]["variant"]
            cache = ResultCache(log_id, "artifact_consumer", TEST_USER_ID, run_variant=key)
            return await cache.get("seen")

        first = await _run(c, alone["id"], log_id)
        assert first["status"] == "ready", first
        assert first["nodes"]["cons"]["variant"] is None
        assert (await _seen(first))["places"] is None
        assert await run_row(log_id, "artifact_producer") is None  # never run for it

        # The optional port's producer writes outside the flow, after the default run read.
        await _default_ready(c, log_id)
        second = await _run(c, alone["id"], log_id)
        assert second["status"] == "ready", second
        assert not second["nodes"]["cons"]["reused"] and second["nodes"]["cons"]["variant"]
        assert (await _seen(second))["places"] == 4
        assert (await _run(c, alone["id"], log_id))["nodes"]["cons"]["reused"]  # memoised
        assert [len(await module_jobs(log_id, m)) for m in PAIR] == [1, 2]

        # Its producer re-runs in the flow (a new version): so does the consumer, though
        # its default run is still fresh.
        prod = _node("prod", "module", {"module_id": "artifact_producer"})
        edge = _edge("e1", "prod", "model", "cons", "reference_model")
        pair = await _create(c, {"nodes": [prod, cons], "edges": [edge]})
        manifest = get_module_loader().loaded["artifact_producer"].manifest
        monkeypatch.setattr(manifest, "version", "0.2.0")
        third = await _run(c, pair["id"], log_id)
        assert third["status"] == "ready", third
        assert not third["nodes"]["prod"]["reused"] and not third["nodes"]["cons"]["reused"]
        fourth = await _run(c, pair["id"], log_id)
        assert fourth["nodes"]["prod"]["reused"] and fourth["nodes"]["cons"]["reused"]
        assert [len(await module_jobs(log_id, m)) for m in PAIR] == [2, 3]


@pytest.mark.asyncio
async def test_every_bound_input_is_brought_up_to_date_first(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    async with mods_client(tmp_path, *PAIR) as c:
        x = await import_sample(c, name="flow inputs X")
        y = await import_sample(c, name="flow inputs Y")
        _optional(monkeypatch)
        _taggable(monkeypatch, *PAIR)
        prod = _node("prod", "module", {"module_id": "artifact_producer", "params": {"tag": 3}})
        made_by = await _create(
            c,
            {
                "nodes": [prod, _node("out", "output", {})],
                "edges": [_edge("e1", "prod", "model", "out", "in")],
            },
        )
        made = await _run(c, made_by["id"], x)
        assert made["status"] == "ready", made
        vref, dref = made["nodes"]["prod"]["refs"][0]["ref"], await _default_ready(c, y)
        graph = {
            "nodes": [
                _node("sv", "source", {"kind": "artifact", "ref": vref}),
                _node("sd", "source", {"kind": "artifact", "ref": dref}),
                _node("cv", "module", {"module_id": "artifact_consumer"}),
                _node("cd", "module", {"module_id": "artifact_consumer", "params": {"tag": 1}}),
            ],
            "edges": [
                _edge("e1", "sv", vref, "cv", "reference_model"),
                # What the optional port reads unconnected anyway: no explicit input.
                _edge("e2", "sd", dref, "cd", "reference_model"),
            ],
        }
        flow = await _create(c, graph)
        assert (await _run(c, flow["id"], y))["status"] == "ready"

        await _bump(x)
        revision = await _bump(y)
        again = await _run(c, flow["id"], y)
        assert again["status"] == "ready", again
        assert not again["nodes"]["cv"]["reused"] and not again["nodes"]["cd"]["reused"]
        # The other flow's stale variant re-ran with its own spec; Y's producer refreshed.
        url = f"/api/v1/event-logs/{x}/module-runs/artifact_producer/variants"
        source = (await c.get(f"{url}/{made['nodes']['prod']['variant']}")).json()
        assert source["status"] == "ready"
        row = await run_row(y, "artifact_producer")
        assert row is not None and (row.status, row.data_revision) == ("ready", revision)


@pytest.mark.asyncio
async def test_a_failed_node_skips_its_dependents_and_fails_the_run(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from mate.api.modules import get_module_loader
    from mate.api.modules.availability import Availability

    async with mods_client(tmp_path, *PAIR) as c:
        log_id = await import_sample(c, name="flow failure")
        flow = await _create(c)
        loader = get_module_loader()
        real = loader.availability_for

        def _unavailable(**kw: Any) -> dict[str, Availability]:
            return {**real(**kw), "artifact_producer": Availability("unavailable", ["gone"])}

        monkeypatch.setattr(loader, "availability_for", _unavailable)
        run = await _run(c, flow["id"], log_id)
        assert run["status"] == "failed"
        assert _statuses(run) == {
            "log": "ready",
            "prod": "failed",
            "cons": "skipped",
            "out": "skipped",
        }
        assert run["nodes"]["prod"]["error"] == "gone"
        assert run["error"] == "1 node(s) failed: gone"
        job = await _job(run["job_id"])
        assert (job.status, job.error) == ("failed", "1 node(s) failed: gone")
        assert await module_jobs(log_id, "artifact_consumer") == []


@pytest.mark.asyncio
async def test_any_node_error_fails_that_node_alone(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from mate.api.modules import get_module_loader

    async with mods_client(tmp_path, *PAIR) as c:
        log_id = await import_sample(c, name="flow node error")
        _taggable(monkeypatch, "artifact_producer")
        graph = {
            "nodes": [
                _node("p1", "module", {"module_id": "artifact_producer", "params": {"tag": 1}}),
                _node("out", "output", {}),
                _node("c2", "module", {"module_id": "artifact_consumer"}),
            ],
            "edges": [_edge("e1", "p1", "model", "out", "in")],
        }
        flow = await _create(c, graph)
        loader = get_module_loader()
        real = loader.submit_precompute

        async def _boom(module_id: str, *args: Any, **kw: Any) -> Any:
            if kw.get("run_spec") is not None:  # the variant's submission only
                raise RuntimeError("database is locked")
            return await real(module_id, *args, **kw)

        monkeypatch.setattr(loader, "submit_precompute", _boom)
        run = await _run(c, flow["id"], log_id)
        assert run["status"] == "failed"
        # Failed, not cancelled; its dependent skipped; the other branch still ran.
        assert _statuses(run) == {"p1": "failed", "out": "skipped", "c2": "ready"}
        assert run["nodes"]["p1"]["error"] == "database is locked"
        assert run["error"] == "1 node(s) failed: database is locked"


@pytest.mark.asyncio
async def test_cancel_stops_the_run_and_the_job_it_waits_on(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from mate.api.modules import get_module_loader

    async with mods_client(tmp_path, *PAIR) as c:
        log_id = await import_sample(c, name="flow cancel")
        _taggable(monkeypatch, "artifact_producer")
        flow = await _create(c, _pair_graph(params={"tag": 1}))
        runtime = get_module_loader().runtime
        await runtime.pause_queue(TEST_USER_ID)  # park the variant job; the flow job runs
        try:
            resp = await c.post(f"{FLOWS}/{flow['id']}/runs", json={"log_id": log_id})
            assert resp.status_code == 202
            run_id = resp.json()["id"]
            url = f"{FLOWS}/{flow['id']}/runs/{run_id}"
            waiting: Any = {}

            async def _waiting() -> bool:
                nonlocal waiting
                waiting = (await c.get(url)).json()["nodes"].get("prod", {})
                return bool(waiting.get("job_id"))

            await wait_until(_waiting, what="the producer node to wait on its job")
            assert (await c.post(f"{url}/cancel")).status_code == 200
            run = await _settled(c, flow["id"], run_id)
        finally:
            await runtime.resume_queue(TEST_USER_ID)
        assert run["status"] == "cancelled"
        assert _statuses(run) == {
            "log": "ready",
            "prod": "cancelled",
            "cons": "skipped",
            "out": "skipped",
        }
        assert (await _job(waiting["job_id"])).status == "cancelled"
        assert (await _job(run["job_id"])).status == "cancelled"
        # A finished run is returned as is.
        assert (await c.post(f"{url}/cancel")).json()["status"] == "cancelled"
        await _unpin()


@pytest.mark.asyncio
async def test_cancel_leaves_the_variant_jobs_others_need(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from mate.api.modules import get_module_loader

    async with mods_client(tmp_path, *PAIR) as c:
        log_id = await import_sample(c, name="flow shared cancel")
        _taggable(monkeypatch, "artifact_producer")

        def _graph(tag: int) -> dict[str, Any]:
            prod = {"module_id": "artifact_producer", "params": {"tag": tag}}
            return {"nodes": [_node("prod", "module", prod)], "edges": []}

        a, b, c3 = (
            await _create(c, _graph(1)),
            await _create(c, _graph(1)),
            await _create(c, _graph(2)),
        )
        runtime = get_module_loader().runtime
        await runtime.pause_queue(TEST_USER_ID)
        try:
            # Tag 2's job is a request of the variants API, not of a flow run.
            url = f"/api/v1/event-logs/{log_id}/module-runs/artifact_producer/variants"
            asked = (await c.post(url, json={"params": {"tag": 2}})).json()
            runs: dict[str, str] = {}
            for flow in (a, b, c3):
                resp = await c.post(f"{FLOWS}/{flow['id']}/runs", json={"log_id": log_id})
                assert resp.status_code == 202, resp.text
                runs[flow["id"]] = resp.json()["id"]
            jobs: dict[str, str | None] = {}

            async def _waiting() -> bool:
                for fid, rid in runs.items():
                    nodes = (await c.get(f"{FLOWS}/{fid}/runs/{rid}")).json()["nodes"]
                    jobs[fid] = nodes.get("prod", {}).get("job_id")
                return all(jobs.values())

            await wait_until(_waiting, what="every run to wait on its producer job")
            shared = jobs[a["id"]]
            assert shared is not None and jobs[b["id"]] == shared  # one variant, one job
            assert jobs[c3["id"]] == asked["job_id"]
            for flow in (a, c3):
                await c.post(f"{FLOWS}/{flow['id']}/runs/{runs[flow['id']]}/cancel")
                assert (await _settled(c, flow["id"], runs[flow["id"]]))["status"] == "cancelled"
            # Run b still waits on the shared job; the API's job was never a run's own.
            assert (await _job(shared)).status == "queued"
            assert (await _job(asked["job_id"])).status == "queued"
        finally:
            await runtime.resume_queue(TEST_USER_ID)
        done = await _settled(c, b["id"], runs[b["id"]])
        assert done["status"] == "ready", done
        assert (await _job(shared)).status == "completed"


@pytest.mark.asyncio
async def test_cancel_spares_the_variant_another_run_is_joining(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from mate.api.modules import get_module_loader
    from mate.api.modules.variants import VariantRunner

    async with mods_client(tmp_path, *PAIR) as c:
        log_id = await import_sample(c, name="flow join race")
        _taggable(monkeypatch, "artifact_producer")
        prod = _node("prod", "module", {"module_id": "artifact_producer", "params": {"tag": 1}})
        graph = {"nodes": [prod], "edges": []}
        a, b = await _create(c, graph), await _create(c, graph)
        armed, joining, release = [False], asyncio.Event(), asyncio.Event()
        real = VariantRunner.ensure

        async def _ensure(self: VariantRunner, *args: Any, **kw: Any) -> Any:
            if armed[0]:  # run b, inside `ensure` - not waiting on the job yet
                joining.set()
                await release.wait()
            return await real(self, *args, **kw)

        monkeypatch.setattr(VariantRunner, "ensure", _ensure)
        runtime = get_module_loader().runtime
        await runtime.pause_queue(TEST_USER_ID)
        try:
            ra = (await c.post(f"{FLOWS}/{a['id']}/runs", json={"log_id": log_id})).json()
            url = f"{FLOWS}/{a['id']}/runs/{ra['id']}"
            waiting: Any = {}

            async def _waiting() -> bool:
                nonlocal waiting
                waiting = (await c.get(url)).json()["nodes"].get("prod", {})
                return bool(waiting.get("job_id"))

            await wait_until(_waiting, what="run a to wait on the variant job")
            armed[0] = True
            rb = (await c.post(f"{FLOWS}/{b['id']}/runs", json={"log_id": log_id})).json()
            await asyncio.wait_for(joining.wait(), 20)
            assert (await c.post(f"{url}/cancel")).status_code == 200
            assert (await _settled(c, a["id"], ra["id"]))["status"] == "cancelled"
            assert (await _job(waiting["job_id"])).status == "queued"
        finally:
            release.set()
            await runtime.resume_queue(TEST_USER_ID)
        done = await _settled(c, b["id"], rb["id"])
        assert done["status"] == "ready", done
        assert done["nodes"]["prod"]["job_id"] == waiting["job_id"]


@pytest.mark.asyncio
async def test_a_settled_interrupted_run_settles_its_nodes(tmp_path: Path) -> None:
    from mate.api.flows.executor import INTERRUPTED, get_flow_executor
    from mate.api.uuid7 import uuid7_str

    async with mods_client(tmp_path, *PAIR) as c:
        log_id = await import_sample(c, name="flow interrupted")
        flow = await _create(c, _pair_graph(pin=False))

        async def _seed(job_id: str | None) -> str:
            run_id = uuid7_str()
            async with get_sessionmaker()() as session:
                if job_id is not None:  # a job that ended without the run recording it
                    session.add(
                        Job(
                            id=job_id,
                            user_id=TEST_USER_ID,
                            type="flow.run",
                            title="Flow",
                            payload_json={},
                            status="failed",
                            error="boom",
                        )
                    )
                session.add(
                    FlowRun(
                        id=run_id,
                        flow_id=flow["id"],
                        user_id=TEST_USER_ID,
                        log_id=log_id,
                        job_id=job_id,
                        status="running",
                        nodes_json={
                            "log": {"status": "ready"},
                            "prod": {"status": "running"},
                            "cons": {"status": "queued"},
                        },
                        created_at=_now(),
                    )
                )
                await session.commit()
            return run_id

        booted = await _seed(None)
        executor = get_flow_executor()
        assert executor is not None
        await executor.reconcile_boot()  # a restart
        lost = await _seed(uuid7_str())  # a read reconciles it
        for run_id, error in ((booted, INTERRUPTED), (lost, "boom")):
            got = (await c.get(f"{FLOWS}/{flow['id']}/runs/{run_id}")).json()
            assert (got["status"], got["error"]) == ("failed", error)
            assert _statuses(got) == {"log": "ready", "prod": "failed", "cons": "skipped"}
            assert got["nodes"]["prod"]["error"] == error


@pytest.mark.asyncio
async def test_a_read_while_the_run_starts_leaves_it_queued(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from mate.api.modules import get_module_loader

    async with mods_client(tmp_path, *PAIR) as c:
        log_id = await import_sample(c, name="flow start race")
        flow = await _create(c, _pair_graph(pin=False))
        runtime = get_module_loader().runtime
        real = runtime.submit
        listed: list[Any] = []

        async def _submit(**kw: Any) -> str:
            if kw.get("type_") == "flow.run":  # the run is committed, its job not yet
                listed.extend((await c.get(f"{FLOWS}/{flow['id']}/runs")).json())
            return await real(**kw)

        monkeypatch.setattr(runtime, "submit", _submit)
        resp = await c.post(f"{FLOWS}/{flow['id']}/runs", json={"log_id": log_id})
        assert resp.status_code == 202, resp.text
        done = await _settled(c, flow["id"], resp.json()["id"])
        assert [(r["id"], r["status"], r["error"]) for r in listed] == [
            (done["id"], "queued", None)
        ]
        assert done["status"] == "ready", done


# --------------------------------------------------------------------------- #
# Palette
# --------------------------------------------------------------------------- #


@pytest.mark.asyncio
async def test_palette_lists_typed_ports_and_converters(tmp_path: Path) -> None:
    from .test_shared_read_only import (
        REPO_MODULES,
        _client,  # pyright: ignore[reportPrivateUsage]
    )

    async with _client(tmp_path, (REPO_MODULES / "interop",)) as (c, _who):
        pal = (await c.get(f"{FLOWS}/palette")).json()
        assert pal["log_artifact_type"] == "event_log"
        [interop] = [m for m in pal["modules"] if m["module_id"] == "interop"]
        assert {p["id"]: (p["artifact_type"], p["required"]) for p in interop["inputs"]} == {
            "process_tree": ("process_tree", False),
            "petri_net": ("petri_net", False),
            "bpmn": ("bpmn", False),
        }
        assert {o["dataset_id"]: o["artifact_type"] for o in interop["outputs"]} == {
            "petri_net_from_process_tree": "petri_net",
            "bpmn_from_petri_net": "bpmn",
            "petri_net_from_bpmn": "petri_net",
            "imported_petri_net": "petri_net",
            "imported_bpmn": "bpmn",
        }
        assert interop["params"] == {"type": "object", "properties": {}}
        assert sorted(
            (cv["from_type"], cv["to_type"], cv["module_id"], cv["input"], cv["output"])
            for cv in pal["converters"]
        ) == [
            ("bpmn", "petri_net", "interop", "bpmn", "petri_net_from_bpmn"),
            ("petri_net", "bpmn", "interop", "petri_net", "bpmn_from_petri_net"),
            ("process_tree", "petri_net", "interop", "process_tree", "petri_net_from_process_tree"),
        ]
        oc = (await c.get(f"{FLOWS}/palette", params={"log_model": "object_centric"})).json()
        assert oc["log_artifact_type"] == "ocel"
        assert oc["modules"] == [] and oc["converters"] == []
