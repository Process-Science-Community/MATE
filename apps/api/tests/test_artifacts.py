"""The artifact contract: artifact types, manifest `inputs:`/typed datasets, `ctx.inputs`.

Layers:
  * SDK units - manifest parsing (old manifests unchanged), `check_contract`,
    `ArtifactRef`, `validate_artifact` against real producer outputs;
  * in-process - `artifact_consumer` reads `artifact_producer`'s cached petri net via
    `ctx.inputs`, the producer runs first through `ensure`, and only for its owner;
  * subprocess - the worker proxy reads the ports from the host's ctx snapshot;
  * `mate.sdk.testing` / `mate-module` - fed inputs, `run --input`, `validate`.
"""

from __future__ import annotations

import importlib
import importlib.util
import json
import sys
import uuid
from pathlib import Path
from types import ModuleType
from typing import Any

import pandas as pd
import pytest

from mate.sdk import (
    ArtifactRef,
    Manifest,
    ModuleManifestError,
    check_contract,
    get_artifact_type,
    validate_artifact,
)
from mate.sdk.cli import main
from mate.sdk.testing import build_test_context

from .conftest import TEST_USER_ID
from .test_module_processing import coordinator_or_fail, import_sample, mods_client, state
from .test_module_processing import wait_until as _wait_until

REPO_ROOT = Path(__file__).resolve().parents[3]
FIXTURES = Path(__file__).parent / "fixtures"


def _manifest(mid: str, **fields: Any) -> Manifest:
    return Manifest.model_validate(
        {"id": mid, "name": mid, "version": "0.1.0", "category": "other", **fields}
    )


# --------------------------------------------------------------------------- #
# Manifest parsing
# --------------------------------------------------------------------------- #


def test_typed_dataset_takes_its_shape_from_the_artifact_type() -> None:
    m = _manifest(
        "p",
        datasets=[
            {"id": "net", "artifact_type": "petri_net", "cache_key": "net"},
            {"id": "tree", "artifact_type": "process_tree@1", "route": "/tree"},
            {"id": "flat", "artifact_type": "petri_net", "shape": "table", "route": "/x"},
        ],
    )
    net, tree, flat = m.datasets
    assert (net.shape, net.route, net.cache_key) == ("graph", None, "net")
    assert tree.shape == "tree"
    assert flat.shape == "table"  # an explicit shape still wins


def test_untyped_route_datasets_load_unchanged() -> None:
    [d] = _manifest("p", datasets=[{"id": "dfg", "shape": "graph", "route": "/dfg"}]).datasets
    assert (d.shape, d.route, d.artifact_type, d.cache_key) == ("graph", "/dfg", None, None)
    assert _manifest("p").inputs == []


def test_dataset_needs_a_route_or_a_cache_key() -> None:
    with pytest.raises(ModuleManifestError, match="needs a `route`, a `cache_key`"):
        _manifest("p", datasets=[{"id": "net", "artifact_type": "petri_net"}])


def test_dataset_without_shape_needs_a_known_artifact_type() -> None:
    with pytest.raises(Exception, match="shape"):
        _manifest("p", datasets=[{"id": "x", "artifact_type": "nope", "cache_key": "x"}])
    with pytest.raises(Exception, match="shape"):
        _manifest("p", datasets=[{"id": "x", "route": "/x"}])


def test_input_port_reads_the_yaml_from_key() -> None:
    [port] = _manifest(
        "c", inputs=[{"id": "m", "artifact_type": "petri_net", "from": "p", "dataset": "net"}]
    ).inputs
    assert (port.from_, port.dataset, port.required) == ("p", "net", False)


def test_every_bundled_manifest_loads_and_honours_the_contract() -> None:
    manifests = [
        Manifest.load_yaml(p) for p in sorted((REPO_ROOT / "modules").glob("*/manifest.yaml"))
    ]
    assert manifests
    assert check_contract(manifests) == []


# --------------------------------------------------------------------------- #
# check_contract
# --------------------------------------------------------------------------- #


def _producer(**dataset: Any) -> Manifest:
    return _manifest("prod", datasets=[{"id": "net", "route": "/net", **dataset}])


def _consumer(**port: Any) -> Manifest:
    return _manifest(
        "cons", inputs=[{"id": "model", "artifact_type": "petri_net", "from": "prod", **port}]
    )


def test_contract_is_clean_for_a_matching_cached_dataset() -> None:
    assert (
        check_contract([_producer(artifact_type="petri_net", cache_key="net"), _consumer()]) == []
    )
    # An optional port with no producer is simply unbound.
    loose = _manifest("cons", inputs=[{"id": "model", "artifact_type": "petri_net"}])
    assert check_contract([loose]) == []
    # The log itself needs no producer, even when required.
    log_port = _manifest(
        "cons", inputs=[{"id": "log", "artifact_type": "event_log", "required": True}]
    )
    assert check_contract([log_port]) == []


@pytest.mark.parametrize(
    ("producer", "consumer", "problem"),
    [
        (
            _producer(artifact_type="petri_nett", shape="graph", cache_key="net"),
            _manifest("cons"),
            "prod: dataset 'net' has unknown artifact type 'petri_nett'",
        ),
        (
            _producer(shape="graph"),
            _consumer(artifact_type="petri_net@2"),
            "cons: input 'model' has unknown artifact type 'petri_net@2'",
        ),
        (
            _producer(shape="graph"),
            _consumer(**{"from": "ghost"}),
            "cons: input 'model' reads from unknown module 'ghost'",
        ),
        (
            _producer(artifact_type="dfg", cache_key="net"),
            _consumer(),
            "cons: input 'model': prod has no petri_net dataset",
        ),
        (
            _producer(artifact_type="dfg", cache_key="net"),
            _consumer(dataset="net"),
            "cons: input 'model': prod.net is dfg, not petri_net",
        ),
        (
            _producer(artifact_type="petri_net"),
            _consumer(),
            "cons: input 'model': prod.net has no cache_key",
        ),
        (
            _producer(artifact_type="petri_net", cache_key="net"),
            _consumer(**{"from": None, "required": True}),
            "cons: required input 'model' names no `from:` module",
        ),
    ],
)
def test_contract_problems(producer: Manifest, consumer: Manifest, problem: str) -> None:
    [found] = check_contract([producer, consumer])
    assert found.startswith(problem)


def test_contract_flags_typed_datasets_sharing_a_cache_key() -> None:
    net = {"artifact_type": "petri_net", "cache_key": "net"}
    untyped = {"id": "c", "shape": "table", "cache_key": "net"}  # another view: fine
    m = _manifest("prod", datasets=[{"id": "a", **net}, {"id": "b", **net}, untyped])
    assert check_contract([m]) == [
        "prod: datasets 'a' and 'b' share cache_key 'net' - one artifact, only 'a' is indexed"
    ]


def test_contract_flags_a_cycle_of_required_inputs() -> None:
    def _module(mid: str, reads: str) -> Manifest:
        port = {"id": "model", "artifact_type": "petri_net", "from": reads, "required": True}
        net = {"id": "net", "artifact_type": "petri_net", "cache_key": "net"}
        return _manifest(mid, datasets=[net], inputs=[port])

    problems = check_contract([_module("a", "b"), _module("b", "a")])
    assert problems == [
        "a: required inputs form a cycle back to 'a'",
        "b: required inputs form a cycle back to 'b'",
    ]


def test_contract_wants_a_named_dataset_when_the_type_is_ambiguous() -> None:
    producer = _manifest(
        "prod",
        datasets=[
            {"id": "a", "artifact_type": "petri_net", "cache_key": "a"},
            {"id": "b", "artifact_type": "petri_net", "cache_key": "b"},
        ],
    )
    [found] = check_contract([producer, _consumer()])
    assert "several petri_net datasets" in found
    assert check_contract([producer, _consumer(dataset="b")]) == []


# --------------------------------------------------------------------------- #
# ArtifactRef / artifact types
# --------------------------------------------------------------------------- #


@pytest.mark.parametrize(
    "ref",
    [ArtifactRef("log-1", "discovery", "petri_net_inductive"), ArtifactRef("l", "m", "k", "v1")],
)
def test_artifact_ref_round_trips(ref: ArtifactRef) -> None:
    assert ArtifactRef.parse(str(ref)) == ref


def test_artifact_ref_text_form() -> None:
    assert str(ArtifactRef("l", "m", "k", "abc")) == "l/m@abc/k"
    assert str(ArtifactRef("l", "m", "k")) == "l/m/k"


@pytest.mark.parametrize("text", ["", "l/m", "l/m/k/x", "/m/k", "l//k", "l/m/", "l/m@/k", "l/@v/k"])
def test_artifact_ref_rejects_malformed_text(text: str) -> None:
    with pytest.raises(ValueError):
        ArtifactRef.parse(text)


def test_artifact_type_lookup_checks_the_version() -> None:
    petri = get_artifact_type("petri_net")
    assert petri is not None and str(petri) == "petri_net@1"
    assert get_artifact_type("petri_net@1") is petri
    assert get_artifact_type("petri_net@2") is None
    assert get_artifact_type("kind_of_net") is None


# --------------------------------------------------------------------------- #
# validate_artifact against what the bundled modules really emit
# --------------------------------------------------------------------------- #

_VARIANTS = [
    ["receive", "check", "approve", "pay", "close"],
    ["receive", "check", "reject", "close"],
    ["receive", "check", "approve", "close"],
]


def _log_frame() -> pd.DataFrame:
    rows = [
        {
            "case_id": f"c{v}-{r}",
            "activity": act,
            "timestamp": pd.Timestamp("2024-01-01") + pd.Timedelta(days=r, hours=v * 10 + step),
        }
        for v, trace in enumerate(_VARIANTS)
        for r in range(4)
        for step, act in enumerate(trace)
    ]
    return pd.DataFrame(rows)


def _import_file(name: str, path: Path) -> ModuleType:
    spec = importlib.util.spec_from_file_location(name, path)
    assert spec is not None and spec.loader is not None
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def _import_package(name: str, folder: Path) -> None:
    # The conformance module uses package-relative imports.
    spec = importlib.util.spec_from_file_location(
        name, folder / "__init__.py", submodule_search_locations=[str(folder)]
    )
    assert spec is not None and spec.loader is not None
    pkg = importlib.util.module_from_spec(spec)
    sys.modules[name] = pkg
    spec.loader.exec_module(pkg)


def test_discovery_outputs_validate() -> None:
    import pm4py

    ser = _import_file("_p2_discovery_serializers", REPO_ROOT / "modules/discovery/serializers.py")
    log = pm4py.format_dataframe(
        _log_frame(), case_id="case_id", activity_key="activity", timestamp_key="timestamp"
    )
    dfg, start, end = pm4py.discover_dfg(log)
    net, im, fm = pm4py.discover_petri_net_inductive(log)
    samples = {
        "dfg": ser.serialize_dfg(dfg, start, end),
        "petri_net": ser.serialize_petri_net(net, im, fm),
        "process_tree": ser.serialize_process_tree(pm4py.discover_process_tree_inductive(log)),
        "bpmn": ser.serialize_bpmn(pm4py.discover_bpmn_inductive(log)),
    }
    for type_id, payload in samples.items():
        # Through JSON, as the result cache stores it.
        assert validate_artifact(type_id, json.loads(json.dumps(payload))) == [], type_id


def test_conformance_output_validates(tmp_path: Path) -> None:
    folder = REPO_ROOT / "modules" / "conformance"
    _import_package("_p2_conformance", folder)
    worker = importlib.import_module("_p2_conformance.conformance")._conformance_worker
    events = tmp_path / "events.parquet"
    _log_frame().to_parquet(events, index=False)
    out = worker(str(events), str(folder / "tests/fixtures/reference.bpmn"), "token_replay", 1.0)
    assert validate_artifact("conformance_result", json.loads(json.dumps(out))) == []


def test_drift_points_and_tables_validate() -> None:
    # Shape of cv4cdd's cached `detections` (module.py `_do_detect` + cv4cdd_core.predict_drifts).
    detections = {
        "kind": "cv4cdd_detections",
        "drifts": [
            {
                "type": "sudden",
                "start_timestamp": "2024-03-01 00:00:00",
                "end_timestamp": "2024-03-01 00:00:00",
                "start_window": 12,
                "end_window": 12,
                "confidence": 0.91,
                "bbox": [0.1, 0.2, 0.3, 0.4],
            }
        ],
        "n_windows": 200,
        "confidence_threshold": 0.5,
    }
    assert validate_artifact("drift_points", detections) == []
    assert validate_artifact("table", _log_frame()) == []


def test_validate_artifact_reports_problems() -> None:
    assert validate_artifact("petri_net", {"places": [], "transitions": []}) == [
        "arcs: Field required"
    ]
    assert validate_artifact("table", {"a": [1]}) == [
        "table@1 is stored as Parquet and needs a DataFrame, got dict"
    ]
    assert validate_artifact("nope", {}) == ["unknown artifact type 'nope'"]


# --------------------------------------------------------------------------- #
# In-process: ctx.inputs, producer-first ordering, ownership
# --------------------------------------------------------------------------- #

_PAIR = ("artifact_producer", "artifact_consumer")


async def _consumer_cache(log_id: str) -> Any:
    from mate.api.modules.cache import ResultCache

    return await ResultCache(log_id, "artifact_consumer", TEST_USER_ID).get("seen")


@pytest.mark.asyncio
async def test_consumer_reads_its_producer_and_the_producer_runs_first(tmp_path: Path) -> None:
    from mate.api.modules import get_module_loader
    from mate.api.modules.ctx_rpc import build_ctx_meta

    async with mods_client(tmp_path, *_PAIR) as c:
        loader = get_module_loader()
        both = set(_PAIR)
        assert loader.precompute_deps("artifact_consumer", both) == list(_PAIR)
        assert loader.precompute_edges(both) == {"artifact_consumer": {"artifact_producer"}}
        # An optional port is best effort: its producer never runs first or gates.
        port = loader.loaded["artifact_consumer"].manifest.inputs[0]
        port.required = False
        try:
            assert loader.precompute_deps("artifact_consumer", both) == ["artifact_consumer"]
        finally:
            port.required = True

        log_id = await import_sample(c)
        states = await coordinator_or_fail().ensure(TEST_USER_ID, log_id, "artifact_consumer")
        assert [s.module_id for s in states] == list(_PAIR)
        assert states[1].job_id is None  # parked until the producer is done

        async def _done() -> bool:
            return await state(log_id, "artifact_consumer") == "ready"

        await _wait_until(_done, what="the consumer to run")
        assert await _consumer_cache(log_id) == {
            "ports": ["reference_model"],
            "ref": f"{log_id}/artifact_producer/model",
            "places": 4,  # one place per distinct activity in sample.csv
        }

        # The worker snapshot hands out the producer's cache file by path.
        ctx = await loader._make_context("artifact_consumer", log_id, TEST_USER_ID)
        [(port, entry)] = build_ctx_meta(ctx)["inputs"].items()
        assert (port, entry["artifact_type"], entry["format"]) == (
            "reference_model",
            "petri_net",
            "json",
        )
        assert entry["ref"] == f"{log_id}/artifact_producer/model"
        assert json.loads(Path(entry["path"]).read_text())["kind"] == "petri_net"


@pytest.mark.asyncio
async def test_consumer_cannot_read_a_producer_the_user_does_not_own(tmp_path: Path) -> None:
    from mate.api.db.engine import get_sessionmaker
    from mate.api.modules import get_module_loader
    from mate.api.modules.installs import record_install, remove_install

    async with mods_client(tmp_path, *_PAIR) as c:
        loader = get_module_loader()
        log_id = await import_sample(c)
        await coordinator_or_fail().ensure(TEST_USER_ID, log_id, "artifact_producer")

        async def _produced() -> bool:
            return await state(log_id, "artifact_producer") == "ready"

        await _wait_until(_produced, what="the producer to run")

        async with get_sessionmaker()() as session:
            await remove_install(session, TEST_USER_ID, "artifact_producer")
            await session.commit()
        try:
            ctx = await loader._make_context("artifact_consumer", log_id, TEST_USER_ID)
            assert ctx.inputs.ports == ["reference_model"]
            assert ctx.inputs.ref("reference_model") is None
            assert await ctx.inputs.get("reference_model") is None
            assert loader.precompute_deps("artifact_consumer", {"artifact_consumer"}) == [
                "artifact_consumer"
            ]
        finally:
            async with get_sessionmaker()() as session:
                await record_install(session, TEST_USER_ID, "artifact_producer", "default")
                await session.commit()


# --------------------------------------------------------------------------- #
# Subprocess worker: ctx.inputs from the ctx snapshot
# --------------------------------------------------------------------------- #


@pytest.mark.asyncio
async def test_subprocess_worker_reads_inputs_by_path(tmp_path: Path) -> None:
    from mate.api.modules.cache import ResultCache
    from mate.api.modules.inputs import ModuleInputs
    from mate.api.modules.subprocess_host import SubprocessBridge

    from .test_worker_conformance import StubCtx, _launch_for

    folder = FIXTURES / "inputs_worker"
    consumer = Manifest.load_yaml(folder / "manifest.yaml")
    producer = _manifest(
        "artifact_producer",
        datasets=[
            {"id": "model", "artifact_type": "petri_net", "cache_key": "model"},
            {"id": "rows", "artifact_type": "table", "cache_key": "rows"},
        ],
    )
    log_id = str(uuid.uuid4())
    net = {"places": [{"id": "p0"}], "transitions": [], "arcs": []}
    cache = ResultCache(log_id, "artifact_producer", TEST_USER_ID)
    await cache.set("model", net)
    await cache.set("rows", pd.DataFrame({"a": [1, 2]}))

    ctx = StubCtx(tmp_path)
    ctx.inputs = ModuleInputs.bind(  # type: ignore[attr-defined]
        consumer.inputs, {"artifact_producer": producer}, log_id, TEST_USER_ID
    )
    bridge = SubprocessBridge(consumer, folder, _launch_for("python")[0])
    await bridge.start()
    try:
        out = await bridge.call_handler("read_inputs", ctx, (), {})
    finally:
        await bridge.stop()

    assert out == {
        "ports": ["model", "rows", "missing"],
        "model": {"value": net, "ref": f"{log_id}/artifact_producer/model"},
        "rows": {"value": {"a": [1, 2]}, "ref": f"{log_id}/artifact_producer/rows"},
        "missing": {"value": None, "ref": None},  # the producer has no dfg
    }


# --------------------------------------------------------------------------- #
# mate.sdk.testing + mate-module
# --------------------------------------------------------------------------- #


async def test_test_context_serves_given_inputs() -> None:
    ctx = await build_test_context(inputs={"reference_model": {"places": []}})
    assert ctx.inputs.ports == ["reference_model"]
    assert await ctx.inputs.get("reference_model") == {"places": []}
    ref = ctx.inputs.ref("reference_model")
    assert ref is not None and ref.key == "reference_model"
    assert ctx.inputs.ref("other") is None and await ctx.inputs.get("other") is None


_INPUT_MODULE = """
from typing import Any

from mate.sdk import Module, ModuleContext, on_event


class DemoModModule(Module):
    id = "demo_mod"

    @on_event("log.imported")
    async def precompute(self, ctx: ModuleContext, payload: dict[str, Any]) -> None:
        model = await ctx.inputs.get("model")
        rows = await ctx.inputs.get("rows")
        await ctx.cache.set("seen", {"model": model, "rows": None if rows is None else len(rows)})
"""

_INPUTS_YAML = """
inputs:
  - id: model
    artifact_type: petri_net
    from: producer_mod
  - id: rows
    artifact_type: table
"""


def test_cli_run_feeds_inputs_and_validate_checks_the_contract(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    assert main(["new", "demo_mod", "--dir", str(tmp_path)]) == 0
    folder = tmp_path / "demo_mod"
    (folder / "module.py").write_text(_INPUT_MODULE, encoding="utf-8")
    # The scaffold's layout.json runs the /recount route this module.py replaces.
    (folder / "layout.json").unlink()
    with (folder / "manifest.yaml").open("a", encoding="utf-8") as f:
        f.write(_INPUTS_YAML)
    model = tmp_path / "model.json"
    model.write_text(json.dumps({"places": [{"id": "p0"}]}), encoding="utf-8")
    pd.DataFrame({"a": [1, 2, 3]}).to_parquet(tmp_path / "rows.parquet")
    capsys.readouterr()

    args = ["run", "precompute", str(folder), "--input", f"model={model}"]
    assert main([*args, "--input", f"rows={tmp_path / 'rows.parquet'}"]) == 0
    seen = json.loads(capsys.readouterr().out)["cache"]["seen"]
    assert seen == {"model": {"places": [{"id": "p0"}]}, "rows": 3}
    assert main([*args, "--input", f"nope={model}"]) == 2
    assert "demo_mod has no input port 'nope' (ports: model, rows)" in capsys.readouterr().err

    # The producer is looked up among the sibling folders.
    assert main(["validate", str(folder)]) == 1
    assert "input 'model' reads from unknown module 'producer_mod'" in capsys.readouterr().out
    producer = tmp_path / "producer_mod"
    producer.mkdir()
    (producer / "manifest.yaml").write_text(
        "id: producer_mod\nname: P\nversion: 0.1.0\ncategory: other\n"
        "datasets:\n  - id: net\n    artifact_type: petri_net\n    cache_key: net\n",
        encoding="utf-8",
    )
    assert main(["validate", str(folder)]) == 0
