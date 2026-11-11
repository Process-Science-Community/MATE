"""Platform consumers of the artifact contract: the dataset adapters dispatch on a
dataset's declared artifact type, cache-only datasets resolve from the producer's
cache, and the catalog / module list expose artifact types and input ports.
(The MCP `list_artifacts` / `get_artifact` tools live in `test_mcp_analysis.py`.)
"""

from __future__ import annotations

import math
from collections.abc import Mapping, Sequence
from pathlib import Path
from typing import Any

import pandas as pd
import pytest
from structlog.testing import capture_logs

from mate.api.datasets.adapters import adapt

from .conftest import TEST_USER_ID
from .test_module_processing import coordinator_or_fail, import_sample, mods_client, state
from .test_module_processing import wait_until as _wait_until

_PETRI = {
    "places": [{"id": "p0"}, {"id": "p1"}],
    "transitions": [{"id": "t0", "label": "a"}, {"id": "t1", "label": "", "is_invisible": True}],
    "arcs": [{"source": "p0", "target": "t0"}, {"source": "t0", "target": "p1"}],
}


def _warnings(logs: Sequence[Mapping[str, Any]]) -> list[str]:
    return [e["event"] for e in logs if e["log_level"] == "warning"]


def test_typed_dataset_dispatches_on_its_artifact_type_not_the_kind_tag() -> None:
    # No `kind` tag at all: the declared type alone picks the petri-net adapter.
    with capture_logs() as logs:
        env = adapt("graph", _PETRI, "petri_net@1")
    assert _warnings(logs) == []
    assert env.meta is not None and env.meta.sourceKind == "petri_net"
    kinds = sorted(n.kind or "" for n in env.data.nodes)  # type: ignore[union-attr]
    assert kinds == ["place", "place", "transition", "transition"]


def test_typed_conformance_result_feeds_kpi_and_table_shapes() -> None:
    raw = {
        "technique": "token_replay",
        "kpis": {"log_fitness": 0.9, "n_traces": 10},
        "per_activity": [{"activity": "a", "deviations": 2}],
    }
    with capture_logs() as logs:
        kpi = adapt("kpi", raw, "conformance_result")
        table = adapt("table", raw, "conformance_result")
    assert _warnings(logs) == []
    assert {m.key for m in kpi.data.items} == {"log_fitness", "n_traces"}  # type: ignore[union-attr]
    assert table.data.rows == [{"activity": "a", "deviations": 2}]  # type: ignore[union-attr]


def test_typed_table_artifact_adapts_a_dataframe() -> None:
    df = pd.DataFrame({"activity": ["a", "b"], "mean": [1.5, math.nan]})
    with capture_logs() as logs:
        env = adapt("table", df, "table")
    assert _warnings(logs) == []
    assert env.data.rows == [{"activity": "a", "mean": 1.5}, {"activity": "b", "mean": None}]  # type: ignore[union-attr]


def test_invalid_typed_payload_warns_but_still_adapts() -> None:
    raw = {"drifts": [{"type": "sudden"}]}  # timestamps + confidence missing
    with capture_logs() as logs:
        env = adapt("table", raw, "drift_points")
    [warning] = [e for e in logs if e["log_level"] == "warning"]
    assert warning["event"] == "datasets.artifact_invalid"
    assert warning["artifact_type"] == "drift_points@1"
    assert env.data.rows == [{"type": "sudden"}]  # type: ignore[union-attr]


def test_unknown_artifact_type_warns_and_falls_back_to_the_kind_tag() -> None:
    with capture_logs() as logs:
        env = adapt("graph", {"kind": "petri_net", **_PETRI}, "no_such_type")
    assert _warnings(logs) == ["datasets.unknown_artifact_type"]
    assert env.meta is not None and env.meta.sourceKind == "petri_net"


@pytest.mark.parametrize(
    ("shape", "raw", "guessed"),
    [
        ("graph", {"kind": "dfg", "activities": [], "edges": []}, False),
        ("graph", {"kind": "heuristics_net", "nodes": []}, True),
        ("table", {"kind": "conformance", "per_activity": [{"a": 1}]}, False),
        ("table", {"drifts": [{"a": 1}]}, False),
        ("table", {"items": [{"a": 1}]}, True),
    ],
)
def test_untyped_datasets_warn_only_on_the_generic_guess(
    shape: str, raw: dict[str, Any], guessed: bool
) -> None:
    with capture_logs() as logs:
        adapt(shape, raw)
    assert _warnings(logs) == (["datasets.adapter_guess"] if guessed else [])


async def test_cache_only_dataset_resolves_and_routes_expose_the_contract(
    tmp_path: Path,
) -> None:
    async with mods_client(tmp_path, "artifact_producer", "artifact_consumer") as c:
        modules = {m["id"]: m for m in (await c.get("/api/v1/modules")).json()}
        assert modules["artifact_producer"]["inputs"] == []
        [port] = modules["artifact_consumer"]["inputs"]
        assert port["id"] == "reference_model"
        assert port["artifact_type"] == "petri_net"
        assert port["required"] is True
        assert port["from"] == "artifact_producer"

        # The producer's only dataset is cache-only: listed with no route and the key
        # the web reads it by; every catalog entry carries the (possibly null) type.
        catalog = (await c.get("/api/v1/datasets/catalog")).json()
        assert all("artifact_type" in e for e in catalog)
        [entry] = [e for e in catalog if e["module_id"] == "artifact_producer"]
        assert (entry["route"], entry["content_ref_key"]) == (None, "model")

        log_id = await import_sample(c)
        url = f"/api/v1/datasets/artifact_producer/model?log_id={log_id}"
        empty = await c.get(url)  # not produced yet: an empty graph, no error
        assert empty.status_code == 200, empty.text
        assert empty.json()["data"]["nodes"] == []

        await coordinator_or_fail().ensure(TEST_USER_ID, log_id, "artifact_producer")

        async def _produced() -> bool:
            return await state(log_id, "artifact_producer") == "ready"

        await _wait_until(_produced, what="the producer to run")
        resp = await c.get(url)
        assert resp.status_code == 200, resp.text
        env = resp.json()
        assert env["shape"] == "graph"
        assert env["meta"]["sourceKind"] == "petri_net"
        # One place per distinct activity in sample.csv, plus the one transition.
        assert sorted(n["kind"] for n in env["data"]["nodes"]) == ["place"] * 4 + ["transition"]
