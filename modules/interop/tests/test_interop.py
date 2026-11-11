"""Tests for Model Interop. Run them with `uv run pytest modules/interop/tests`."""

from __future__ import annotations

import asyncio
import io
import sys
from pathlib import Path
from typing import Any

import pytest
from fastapi import HTTPException, UploadFile

from mate.sdk import Manifest
from mate.sdk.artifacts import validate_artifact
from mate.sdk.decorators import get_event_sub, get_job_spec
from mate.sdk.layout import ModuleLayout
from mate.sdk.testing import build_test_context, load_module, sample_log

MODULE_DIR = Path(__file__).resolve().parents[1]

# A discovery-shaped process tree: ->( a, X( b, tau ), *( c, tau ) ).
TREE: dict[str, Any] = {
    "kind": "process_tree",
    "root": {
        "id": "n0",
        "operator": "sequence",
        "label": None,
        "children": [
            {"id": "n1", "operator": None, "label": "a", "children": []},
            {
                "id": "n2",
                "operator": "xor",
                "label": None,
                "children": [
                    {"id": "n3", "operator": None, "label": "b", "children": []},
                    {"id": "n4", "operator": None, "label": None, "children": []},
                ],
            },
            {
                "id": "n5",
                "operator": "loop",
                "label": None,
                "children": [
                    {"id": "n6", "operator": None, "label": "c", "children": []},
                    {"id": "n7", "operator": None, "label": None, "children": []},
                ],
            },
        ],
    },
}


def _labels(petri_net: dict[str, Any]) -> set[str]:
    return {t["label"] for t in petri_net["transitions"] if t["label"]}


async def _precompute(module: Any, inputs: dict[str, Any]) -> dict[str, Any]:
    ctx = await build_test_context(sample_log(), module_id=module.id, inputs=inputs)
    await module.precompute(ctx, {"log_id": ctx.log_id, "user_id": ctx.user_id})
    return ctx.cache.data  # type: ignore[attr-defined]


def test_precompute_converts_each_bound_input_into_a_valid_artifact() -> None:
    async def check() -> None:
        module = load_module(MODULE_DIR)
        # Chained like run variants binding each port to the previous output.
        out = await _precompute(module, {"process_tree": TREE})
        assert set(out) == {"petri_net_from_process_tree"}
        petri = out["petri_net_from_process_tree"]
        out = await _precompute(module, {"petri_net": petri})
        bpmn = out["bpmn_from_petri_net"]
        out = await _precompute(module, {"bpmn": bpmn})
        back = out["petri_net_from_bpmn"]

        assert validate_artifact("petri_net", petri) == []
        assert validate_artifact("bpmn", bpmn) == []
        assert validate_artifact("petri_net", back) == []
        assert _labels(petri) == _labels(back) == {"a", "b", "c"}
        assert all(f'name="{name}"' in bpmn["xml"] for name in ("a", "b", "c"))

    asyncio.run(check())


def test_precompute_drops_an_output_whose_input_is_gone() -> None:
    async def check() -> None:
        module = load_module(MODULE_DIR)
        ctx = await build_test_context(sample_log(), module_id=module.id)
        await ctx.cache.set("bpmn_from_petri_net", {"kind": "bpmn", "xml": "<old/>"})
        await module.precompute(ctx, {"log_id": ctx.log_id, "user_id": ctx.user_id})
        assert not await ctx.cache.exists("bpmn_from_petri_net")

    asyncio.run(check())


def _upload(data: bytes, name: str) -> UploadFile:
    return UploadFile(io.BytesIO(data), filename=name)


def test_precompute_chains_off_discovery_and_reads_its_cached_models() -> None:
    module = load_module(MODULE_DIR)
    manifest = Manifest.load_yaml(MODULE_DIR / "manifest.yaml")
    # One job-backed precompute on `discovery.completed`, so the platform runs
    # Discovery first instead of racing it (optional ports never run first).
    subs = [
        (sub.topic, get_job_spec(fn) is not None)
        for fn in vars(type(module)).values()
        if (sub := get_event_sub(fn)) is not None
    ]
    assert subs == [("discovery.completed", True)]
    assert manifest.consumes == ["discovery.completed"]
    assert [m.id for m in manifest.requirements.optional_modules] == ["discovery"]
    # Each bound port reads a dataset Discovery's precompute caches.
    discovery = Manifest.load_yaml(MODULE_DIR.parent / "discovery" / "manifest.yaml")
    cached = {d.id: d for d in discovery.datasets if d.cache_key}
    for port in manifest.inputs:
        if port.from_ is not None:
            assert port.from_ == "discovery"
            assert port.dataset is not None
            assert cached[port.dataset].artifact_type == port.artifact_type
    assert {p.id for p in manifest.inputs if p.from_} == {"process_tree", "petri_net"}
    assert cached["process_tree"].cache_key == "process_tree"


def test_export_then_import_round_trips_pnml_and_bpmn() -> None:
    async def check() -> None:
        module = load_module(MODULE_DIR)
        petri = (await _precompute(module, {"process_tree": TREE}))["petri_net_from_process_tree"]
        ctx = await build_test_context(
            sample_log(), module_id=module.id, inputs={"process_tree": TREE, "petri_net": petri}
        )
        await module.precompute(ctx, {"log_id": ctx.log_id, "user_id": ctx.user_id})

        pnml = await module.export(ctx, "pnml")  # default: the first stored Petri net
        assert pnml.headers["content-disposition"] == (
            'attachment; filename="petri_net_from_process_tree.pnml"'
        )
        out = await module.import_model(ctx, "pnml", _upload(pnml.body, "net.pnml"))
        assert out == {
            "dataset": "imported_petri_net",
            "artifact_type": "petri_net",
            "ref": f"{ctx.log_id}/interop/imported_petri_net",
        }
        imported = await ctx.cache.get("imported_petri_net")
        assert validate_artifact("petri_net", imported) == []
        assert _labels(imported) == {"a", "b", "c"}

        xml = await module.export(ctx, "bpmn")  # default: bpmn_from_petri_net
        assert xml.media_type == "application/bpmn+xml"
        assert 'filename="bpmn_from_petri_net.bpmn"' in xml.headers["content-disposition"]
        await module.import_model(ctx, "bpmn", _upload(xml.body, "model.bpmn"))
        assert validate_artifact("bpmn", await ctx.cache.get("imported_bpmn")) == []
        again = await module.export(ctx, "bpmn", source="imported_bpmn")
        assert 'filename="imported_bpmn.bpmn"' in again.headers["content-disposition"]

        # Imports outlive the platform emptying the cache (all but `__*`) before a
        # re-run, and come back even when the run has no input at all.
        data: dict[str, Any] = ctx.cache.data  # type: ignore[attr-defined]
        assert {"__interop_imported_petri_net", "__interop_imported_bpmn"} <= set(data)
        for key in [k for k in data if not k.startswith("__")]:
            del data[key]
        ctx.inputs.values.clear()  # type: ignore[attr-defined]
        await module.precompute(ctx, {"log_id": ctx.log_id, "user_id": ctx.user_id})
        assert await ctx.cache.get("imported_petri_net") == imported
        assert await ctx.cache.exists("imported_bpmn")
        assert not await ctx.cache.exists("bpmn_from_petri_net")
        pnml = await module.export(ctx, "pnml")  # falls back to the import
        assert 'filename="imported_petri_net.pnml"' in pnml.headers["content-disposition"]

    asyncio.run(check())


_XXE = (
    b'<?xml version="1.0"?>\n<!DOCTYPE pnml [<!ENTITY xxe SYSTEM "file:///etc/passwd">]>\n'
    b"<pnml><net><name><text>&xxe;</text></name></net></pnml>"
)
_BOMB = (
    b'<?xml version="1.0"?>\n<!DOCTYPE d [<!ENTITY a "aaaaaaaaaa">'
    b'<!ENTITY b "&a;&a;&a;&a;&a;&a;&a;&a;&a;&a;">]>\n<definitions>&b;</definitions>'
)


def _pnml_with_arc(arc_type: str) -> bytes:
    return (
        '<pnml xmlns="http://www.pnml.org/version-2009/grammar/pnml"><net id="n" '
        'type="http://www.pnml.org/version-2009/grammar/pnmlcoremodel"><page id="pg">'
        '<place id="p"><initialMarking><text>1</text></initialMarking></place>'
        '<place id="q"/><transition id="t"><name><text>a</text></name></transition>'
        f'<arc id="a1" source="p" target="t"><arctype><text>{arc_type}</text></arctype></arc>'
        '<arc id="a2" source="t" target="q"/></page></net></pnml>'
    ).encode()


@pytest.mark.parametrize(
    ("fmt", "data", "status", "detail"),
    [
        ("pnml", _XXE, 422, "DOCTYPE"),
        ("bpmn", _BOMB, 422, "DOCTYPE"),
        ("pnml", b"not xml", 422, "Not well-formed XML"),
        ("pnml", b"<pnml><net/></pnml>", 422, "Not a readable PNML file"),
        ("pnml", _pnml_with_arc("inhibitor"), 422, "has inhibitor arcs"),
        ("pnml", _pnml_with_arc("reset"), 422, "has reset arcs"),
        ("pnml", b"<pnml/>" + b" " * (10 * 1024 * 1024), 413, "larger than 10 MB"),
        ("xes", b"<log/>", 404, "Unknown format"),
    ],
)
def test_import_refuses_unsafe_or_unreadable_files(
    fmt: str, data: bytes, status: int, detail: str
) -> None:
    async def check() -> None:
        module = load_module(MODULE_DIR)
        ctx = await build_test_context(sample_log(), module_id=module.id)
        with pytest.raises(HTTPException) as exc:
            await module.import_model(ctx, fmt, _upload(data, f"model.{fmt}"))
        assert exc.value.status_code == status
        assert detail in str(exc.value.detail)
        assert not ctx.cache.data  # type: ignore[attr-defined]

    asyncio.run(check())


def test_import_keeps_a_normal_arc_pnml() -> None:
    async def check() -> None:
        module = load_module(MODULE_DIR)
        ctx = await build_test_context(sample_log(), module_id=module.id)
        await module.import_model(ctx, "pnml", _upload(_pnml_with_arc("normal"), "n.pnml"))
        assert _labels(await ctx.cache.get("imported_petri_net")) == {"a"}

    asyncio.run(check())


def test_a_read_only_viewer_cannot_import() -> None:
    # A shared dashboard's viewer: refused before the upload is read, nothing stored.
    async def check() -> None:
        module: Any = load_module(MODULE_DIR)
        ctx = await build_test_context(sample_log(), module_id=module.id, read_only=True)
        upload = _upload(_pnml_with_arc("normal"), "n.pnml")
        with pytest.raises(HTTPException) as exc:
            await module.import_model(ctx, "pnml", upload)
        assert exc.value.status_code == 403
        assert upload.file.tell() == 0
        assert not ctx.cache.data  # type: ignore[attr-defined]

    asyncio.run(check())


def test_export_answers_404_naming_what_is_missing() -> None:
    async def check() -> None:
        module = load_module(MODULE_DIR)
        # A bound input is not exported: only the module's own stored results are.
        ctx = await build_test_context(
            sample_log(), module_id=module.id, inputs={"petri_net": {"kind": "petri_net"}}
        )
        cases = (
            ("pnml", None, "petri_net_from_process_tree / petri_net_from_bpmn"),
            ("bpmn", None, "bpmn_from_petri_net / imported_bpmn"),
            ("bpmn", "imported_bpmn", "imported_bpmn is not there"),
            ("pnml", "petri_net", "no petri_net named 'petri_net'"),
            ("pnml", "dfg", "no petri_net named 'dfg'"),
        )
        for fmt, source, detail in cases:
            with pytest.raises(HTTPException) as exc:
                await module.export(ctx, fmt, source=source)
            assert exc.value.status_code == 404
            assert detail in str(exc.value.detail)
            if source in (None, "imported_bpmn"):
                assert ctx.log_id in str(exc.value.detail)

    asyncio.run(check())


def test_manifest_matches_the_code() -> None:
    load_module(MODULE_DIR)
    code = sys.modules["mate_mod_interop"]
    manifest = Manifest.load_yaml(MODULE_DIR / "manifest.yaml")
    assert all(p.id == p.artifact_type for p in manifest.inputs)
    datasets = {d.id: d for d in manifest.datasets}
    for d in datasets.values():
        assert d.cache_key == d.id
        assert d.id in code._SOURCES[d.artifact_type]
    outputs = [key for _port, key, _fn in code._CONVERTERS]
    outputs += [key for _type, key, _media, _fn in code._FORMATS.values()]
    assert sorted(outputs) == sorted(datasets)


def test_page_cards_render_the_cache_only_results_with_their_viewer() -> None:
    manifest = Manifest.load_yaml(MODULE_DIR / "manifest.yaml")
    # Cache-only datasets: the page reads them through the artifact content endpoint.
    assert all(d.route is None and d.cache_key == d.id for d in manifest.datasets)
    layout = ModuleLayout.load(MODULE_DIR)
    assert layout is not None and len(layout.items) == 4
    viewers = {"petri_net": "petri-net-view", "bpmn": "bpmn-view"}
    types = {d.id: d.artifact_type or "" for d in manifest.datasets}
    for item in layout.items:
        assert item.dataset_ref is not None
        assert item.viz_id == viewers[types[item.dataset_ref.dataset_id]]
