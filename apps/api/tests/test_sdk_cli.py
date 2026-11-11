"""`mate-module` (mate.sdk.cli): scaffold -> validate -> test / run -> pack, a scaffolded
module loading in the real platform, and the ``mate.sdk.testing`` fakes."""

from __future__ import annotations

import asyncio
import json
import os
import re
import shutil
import time
import zipfile
from pathlib import Path
from typing import Any

import pandas as pd
import pytest
from httpx import ASGITransport, AsyncClient
from pydantic import ValidationError

from mate.sdk.cli import PACK_EXCLUDE, main
from mate.sdk.errors import ReadOnlyContextError
from mate.sdk.layout import ModuleLayout
from mate.sdk.testing import (
    MemoryCache,
    RecordingProgress,
    build_test_context,
    load_log,
    sample_log,
)
from tests.conftest import (
    _override_current_user_for_tests,  # pyright: ignore[reportPrivateUsage]
    _seed_module_installs_for_test_user,  # pyright: ignore[reportPrivateUsage]
)

FIXTURES = Path(__file__).parent / "fixtures"
MOD = "lite_probe"


def test_new_then_validate_passes(tmp_path: Path) -> None:
    assert main(["new", MOD, "--panel", "--dir", str(tmp_path)]) == 0
    folder = tmp_path / MOD
    for rel in ("manifest.yaml", "module.py", "HOW_TO_USE.md", "layout.json", "panel/index.tsx"):
        assert (folder / rel).is_file(), rel
    assert (folder / "tests" / f"test_{MOD}.py").is_file()
    assert "panel: ./panel/index.tsx" in (folder / "manifest.yaml").read_text()
    assert main(["validate", str(folder)]) == 0


def test_list_shows_modules_and_flags_invalid(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    assert main(["new", MOD, "--panel", "--dir", str(tmp_path)]) == 0
    broken = tmp_path / "broken"
    broken.mkdir()
    (broken / "manifest.yaml").write_text("id: [not, valid\n")
    assert main(["list", str(tmp_path)]) == 0
    out = capsys.readouterr().out
    line = next(ln for ln in out.splitlines() if ln.startswith(MOD))
    assert "in_process/thread" in line and "panel, layout, 1 dataset" in line
    assert "invalid manifest" in out and "2 module(s)." in out
    assert main(["list", str(tmp_path / "missing")]) == 1


def test_new_without_panel_has_no_frontend(tmp_path: Path) -> None:
    assert main(["new", MOD, "--name", 'Probe: "quoted"', "--dir", str(tmp_path)]) == 0
    assert not (tmp_path / MOD / "panel").exists()
    assert "frontend" not in (tmp_path / MOD / "manifest.yaml").read_text()
    # A name with YAML/Python-special characters still yields a valid module.
    assert main(["validate", str(tmp_path)]) == 0


def test_generated_module_tests_pass(tmp_path: Path) -> None:
    assert main(["new", MOD, "--dir", str(tmp_path)]) == 0
    assert main(["test", MOD, "--dir", str(tmp_path), "-q", "-p", "no:cacheprovider"]) == 0


def test_new_refuses_to_overwrite(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    (tmp_path / MOD).mkdir()
    (tmp_path / MOD / "keep.txt").write_text("mine")
    assert main(["new", MOD, "--dir", str(tmp_path)]) == 1
    assert "already exists" in capsys.readouterr().err
    assert [p.name for p in (tmp_path / MOD).iterdir()] == ["keep.txt"]


@pytest.mark.parametrize("bad", ["Bad", "with-dash", "1abc", "_lead", "sp ace", ""])
def test_new_refuses_bad_id(tmp_path: Path, bad: str) -> None:
    assert main(["new", bad, "--dir", str(tmp_path)]) == 2
    assert list(tmp_path.iterdir()) == []


def test_validate_catches_layout_with_missing_dataset(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    assert main(["new", MOD, "--dir", str(tmp_path)]) == 0
    path = tmp_path / MOD / "layout.json"
    layout = json.loads(path.read_text())
    _card(layout, "viz")["dataset_ref"]["dataset_id"] = "nope"
    path.write_text(json.dumps(layout))
    capsys.readouterr()
    assert main(["validate", str(tmp_path)]) == 1
    out = capsys.readouterr().out
    assert f"FAIL {MOD}" in out and "'nope'" in out


def test_validate_catches_broken_layout_schema(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    assert main(["new", MOD, "--dir", str(tmp_path)]) == 0
    (tmp_path / MOD / "layout.json").write_text('{"version": 1, "items": [{"i": "a"}]}')
    assert main(["validate", str(tmp_path / MOD)]) == 1
    assert "layout.json is invalid" in capsys.readouterr().out


async def _seed_sample_log(client: AsyncClient) -> str:
    with (FIXTURES / "sample.csv").open("rb") as f:
        resp = await client.post(
            "/api/v1/event-logs",
            files={"file": ("sample.csv", f, "text/csv")},
            data={"name": "Sample CSV"},
        )
    log_id = resp.json()["log_id"]
    deadline = time.time() + 10.0
    while time.time() < deadline:
        status = (await client.get(f"/api/v1/event-logs/{log_id}")).json()["status"]
        if status == "ready":
            return log_id
        assert status != "failed", "import failed"
        await asyncio.sleep(0.05)
    raise AssertionError("import did not finish")


@pytest.mark.asyncio
async def test_scaffolded_module_loads_in_the_platform(tmp_path: Path) -> None:
    """The scaffold is a real module: the platform loader mounts it, its /summary route
    answers over an imported log, and (once the backend serves it) its layout.json."""
    from mate.api import config as cfg

    modules = tmp_path / "modules"
    assert main(["new", MOD, "--dir", str(modules)]) == 0

    prev = os.environ.get("MODULES_DIR")
    os.environ["MODULES_DIR"] = str(modules)
    cfg.get_settings.cache_clear()
    shutil.rmtree(cfg.get_settings().uploaded_modules_dir, ignore_errors=True)
    try:
        from mate.api.main import create_app

        app = create_app()
        _override_current_user_for_tests(app)
        async with (
            AsyncClient(transport=ASGITransport(app=app), base_url="http://testserver") as c,
            app.router.lifespan_context(app),
        ):
            await _seed_module_installs_for_test_user()
            log_id = await _seed_sample_log(c)

            resp = await c.get(f"/api/v1/modules/{MOD}/summary?log_id={log_id}")
            assert resp.status_code == 200, resp.text
            rows = resp.json()["rows"]
            assert sum(r["events"] for r in rows) == 9  # sample.csv has 9 events
            assert {"activity", "events", "cases"} <= set(rows[0])
            # The `top` page param (params_schema) reaches the handler as a query param.
            resp = await c.get(f"/api/v1/modules/{MOD}/summary?log_id={log_id}&top=1")
            assert len(resp.json()["rows"]) == 1

            resp = await c.get(f"/api/v1/modules/{MOD}/layout")
            body = resp.json() if resp.status_code == 200 else {}
            layout = body.get("layout") if isinstance(body, dict) else None
            if not (isinstance(layout, dict) and "items" in layout):
                pytest.skip(
                    "GET /api/v1/modules/{id}/layout does not serve layout.json yet "
                    f"(status {resp.status_code}: {resp.text[:120]}); backend owner's endpoint."
                )
            assert _card(layout, "viz")["dataset_ref"] == {
                "module_id": MOD,
                "dataset_id": "summary",
            }
            assert [it["kind"] for it in layout["items"]] == ["text", "controls", "action", "viz"]
    finally:
        if prev is None:
            os.environ.pop("MODULES_DIR", None)
        else:
            os.environ["MODULES_DIR"] = prev
        cfg.get_settings.cache_clear()


# -- layout.json: tabs and element kinds --------------------------------------


def _card(layout: dict[str, Any], kind: str) -> dict[str, Any]:
    return next(it for it in layout["items"] if it["kind"] == kind)


def _scaffold_layout(tmp_path: Path) -> tuple[Path, dict[str, Any]]:
    assert main(["new", MOD, "--dir", str(tmp_path)]) == 0
    path = tmp_path / MOD / "layout.json"
    return path, json.loads(path.read_text())


def test_scaffold_layout_shows_every_kind_and_validates_with_tabs(tmp_path: Path) -> None:
    path, layout = _scaffold_layout(tmp_path)
    assert {it["kind"] for it in layout["items"]} == {"text", "controls", "action", "viz"}
    assert _card(layout, "controls")["config"] == {"source": "params", "keys": ["top"]}
    assert _card(layout, "action")["config"]["route"] == "/recount"
    layout["tabs"] = [{"id": "overview", "title": "Overview"}, {"id": "data", "title": "Data"}]
    _card(layout, "viz")["tab"] = "data"
    path.write_text(json.dumps(layout))
    assert main(["validate", str(tmp_path / MOD)]) == 0
    loaded = ModuleLayout.load(tmp_path / MOD)
    assert loaded is not None and [t.id for t in loaded.tabs or []] == ["overview", "data"]


def _item(**kw: Any) -> dict[str, Any]:
    return {"i": "c", "x": 0, "y": 0, "w": 4, "h": 2, **kw}


_TEXT = {"kind": "text", "config": {"markdown": "x"}}
_TAB_A = [{"id": "a", "title": "A"}]


@pytest.mark.parametrize(
    ("layout", "match"),
    [
        ({"tabs": _TAB_A, "items": [_item(**_TEXT, tab="nope")]}, "tab 'nope'"),
        ({"items": [_item(**_TEXT, tab="a")]}, "declares no `tabs`"),
        ({"tabs": [*_TAB_A, {"id": "a", "title": "B"}]}, "tab ids"),
        ({"tabs": [{"id": "Bad Id", "title": "A"}]}, "pattern"),
        ({"tabs": [{"id": "panel", "title": "Charts"}]}, "reserved"),
        ({"items": [], "theme": "dark"}, "theme"),
        ({"items": [_item(kind="text")]}, "markdown"),
        ({"items": [_item(kind="controls")]}, "module_id"),
        ({"items": [_item(kind="controls", module_id="m", config={"source": "x"})]}, "source"),
        ({"items": [_item(kind="controls", module_id="m", config={"keys": "top"})]}, "keys"),
        ({"items": [_item(kind="action", module_id="m")]}, "route"),
        ({"items": [_item(kind="action", module_id="m", config={"route": "/r?x=1"})]}, "route"),
        ({"items": [_item(kind="action", module_id="m", config={"route": "r"})]}, "route"),
    ],
)
def test_layout_model_rejects(layout: dict[str, Any], match: str) -> None:
    with pytest.raises(ValidationError, match=match):
        ModuleLayout.model_validate({"version": 1, **layout})


def test_old_v1_layout_dumps_byte_identical(tmp_path: Path) -> None:
    item = {"i": "w1", "kind": "widget", "module_id": "m", "widget_id": "kpi", "mapping": {}}
    old = json.dumps(
        {"version": 1, "items": [{**item, "x": 0, "y": 0, "w": 6, "h": 4, "config": {}}]},
        indent=2,
    )
    (tmp_path / "layout.json").write_text(old + "\n")
    layout = ModuleLayout.load(tmp_path)
    assert layout is not None and layout.tabs is None
    layout.dump(tmp_path)
    assert (tmp_path / "layout.json").read_text() == old + "\n"


def _set(kind: str, **config: Any) -> Any:
    def mutate(layout: dict[str, Any]) -> None:
        _card(layout, kind)["config"].update(config)

    return mutate


@pytest.mark.parametrize(
    ("mutate", "expect"),
    [
        (_set("controls", keys=["nope"]), "controls 'nope'"),
        (_set("controls", source="config"), "nothing to control; declare `config_schema`"),
        (_set("action", route="/missing"), "'/missing', which no .py file"),
    ],
)
def test_validate_checks_controls_and_action_routes(
    tmp_path: Path, capsys: pytest.CaptureFixture[str], mutate: Any, expect: str
) -> None:
    path, layout = _scaffold_layout(tmp_path)
    mutate(layout)
    path.write_text(json.dumps(layout))
    capsys.readouterr()
    assert main(["validate", str(tmp_path / MOD)]) == 1
    assert expect in capsys.readouterr().out


def test_validate_action_route_ignores_venv_and_tests(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    path, layout = _scaffold_layout(tmp_path)
    _card(layout, "action")["config"]["route"] = "/stop"
    path.write_text(json.dumps(layout))
    for sub in (".venv/lib/site.py", "tests/test_stop.py"):
        (tmp_path / MOD / sub).parent.mkdir(parents=True, exist_ok=True)
        (tmp_path / MOD / sub).write_text('client.post("/stop")\n')
    capsys.readouterr()
    assert main(["validate", str(tmp_path / MOD)]) == 1
    assert "'/stop', which no .py file" in capsys.readouterr().out


def test_validate_controls_need_a_params_schema(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    _scaffold_layout(tmp_path)
    manifest = tmp_path / MOD / "manifest.yaml"
    text = manifest.read_text()
    manifest.write_text(text[: text.index("    # Query params")])
    capsys.readouterr()
    assert main(["validate", str(tmp_path / MOD)]) == 1
    assert "nothing to control; declare a dataset's `params_schema`" in capsys.readouterr().out


# -- widget scaffold, entry files, placeholder viz cards ----------------------


def _widget_ids(folder: Path) -> list[str]:
    from mate.sdk.manifest import Manifest

    return [w.id for w in Manifest.load_yaml(folder / "manifest.yaml").frontend.widgets]


@pytest.mark.parametrize("panel", [False, True], ids=["no-frontend", "panel-only-frontend"])
def test_widget_adds_tsx_and_manifest_entry(tmp_path: Path, panel: bool) -> None:
    assert main(["new", MOD, "--dir", str(tmp_path), *(["--panel"] if panel else [])]) == 0
    folder = tmp_path / MOD
    assert main(["widget", MOD, "top-activities", "--dir", str(tmp_path)]) == 0
    tsx = (folder / "widgets" / "TopActivities.tsx").read_text()
    assert "export default function TopActivities" in tsx and '"/summary"' in tsx
    assert not re.search(r"__[A-Z_]+__", tsx)  # every placeholder replaced
    assert _widget_ids(folder) == ["top-activities"]
    assert ("panel: ./panel/index.tsx" in (folder / "manifest.yaml").read_text()) is panel
    assert main(["validate", str(folder)]) == 0


def test_widget_appends_to_existing_widgets_keeping_comments(tmp_path: Path) -> None:
    assert main(["new", MOD, "--dir", str(tmp_path)]) == 0
    folder = tmp_path / MOD
    manifest = folder / "manifest.yaml"
    (folder / "widgets").mkdir()
    (folder / "widgets" / "Old.tsx").write_text("export default function Old() { return null; }\n")
    manifest.write_text(
        manifest.read_text()
        + "\nfrontend:\n    log_filter: false  # keep me\n    widgets:\n"
        + "    - id: old\n      entry: ./widgets/Old.tsx\n\n    # trailing comment\n"
        + "permissions:\n  - read:event_log\n"
    )
    assert main(["widget", MOD, "new-one", "--dir", str(tmp_path), "--title", "New: one"]) == 0
    text = manifest.read_text()
    assert "# keep me" in text and "# trailing comment" in text
    assert _widget_ids(folder) == ["old", "new-one"]
    assert main(["validate", str(folder)]) == 0


def test_widget_refuses_duplicate_bad_id_and_restores_on_failure(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    assert main(["new", MOD, "--dir", str(tmp_path)]) == 0
    folder = tmp_path / MOD
    manifest = folder / "manifest.yaml"
    assert main(["widget", MOD, "chart", "--dir", str(tmp_path)]) == 0
    before = manifest.read_text()
    assert main(["widget", MOD, "chart", "--dir", str(tmp_path)]) == 1
    assert "already declares" in capsys.readouterr().err
    for bad in ("Chart", "my_chart", "1x", ""):
        assert main(["widget", MOD, bad, "--dir", str(tmp_path)]) == 2
    assert manifest.read_text() == before
    # A shape the text edit cannot handle: the manifest is restored, the snippet printed.
    broken = before[: before.index("frontend:")] + "frontend:\n  widgets: []\n"
    manifest.write_text(broken)
    capsys.readouterr()
    assert main(["widget", MOD, "other", "--dir", str(tmp_path)]) == 1
    assert manifest.read_text() == broken
    assert "- id: other" in capsys.readouterr().err
    assert not (folder / "widgets" / "Other.tsx").exists()


def test_widget_flow_style_frontend_is_restored_and_ids_do_not_shadow_imports(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    assert main(["new", MOD, "--dir", str(tmp_path)]) == 0
    folder = tmp_path / MOD
    manifest = folder / "manifest.yaml"
    flow = manifest.read_text() + "\nfrontend: {log_filter: false}\n"
    manifest.write_text(flow)
    capsys.readouterr()
    assert main(["widget", MOD, "chart", "--dir", str(tmp_path)]) == 1
    assert manifest.read_text() == flow
    assert "- id: chart" in capsys.readouterr().err
    manifest.write_text(flow.replace("\nfrontend: {log_filter: false}\n", ""))
    assert main(["widget", MOD, "bar-chart", "--dir", str(tmp_path)]) == 0
    tsx = (folder / "widgets" / "BarChart.tsx").read_text()
    assert "export default function BarChartWidget(" in tsx


def test_validate_catches_missing_widget_entry(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    assert main(["new", MOD, "--dir", str(tmp_path)]) == 0
    assert main(["widget", MOD, "gone", "--dir", str(tmp_path)]) == 0
    (tmp_path / MOD / "widgets" / "Gone.tsx").unlink()
    capsys.readouterr()
    assert main(["validate", str(tmp_path / MOD)]) == 1
    assert "widget 'gone' entry './widgets/Gone.tsx' does not exist" in capsys.readouterr().out


def test_placeholder_viz_loads_but_fails_validate(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    path, layout = _scaffold_layout(tmp_path)
    viz = _card(layout, "viz")
    del viz["dataset_ref"]
    viz["viz_id"] = "petri-net"
    path.write_text(json.dumps(layout))
    assert ModuleLayout.load(tmp_path / MOD) is not None  # the Studio may save it
    capsys.readouterr()
    assert main(["validate", str(tmp_path / MOD)]) == 1
    assert "has no data yet; declare a graph dataset" in capsys.readouterr().out
    with pytest.raises(ValidationError, match="viz_id"):
        ModuleLayout.model_validate({"items": [_item(kind="viz")]})


# -- run / pack and the mate.sdk.testing fakes ---------------------------------


def test_new_validate_run_pack(
    tmp_path: Path, capsys: pytest.CaptureFixture[str], monkeypatch: pytest.MonkeyPatch
) -> None:
    assert main(["new", "demo_mod", "--dir", str(tmp_path)]) == 0
    folder = tmp_path / "demo_mod"
    assert main(["validate", str(folder)]) == 0

    capsys.readouterr()
    assert main(["run", "recount", str(folder)]) == 0
    out = json.loads(capsys.readouterr().out)
    assert out["result"] is None
    rows = out["cache"]["summary"]["rows"]
    assert sum(r["events"] for r in rows) == 44  # the bundled sample log
    assert {"activity", "events", "cases"} <= set(rows[0])

    for artifact in (
        ".venv/bin/python",
        "node_modules/x/index.js",
        ".pytest_cache/v/x",
        ".ruff_cache/x",
        ".mypy_cache/x",
        "tests/.DS_Store",
        ".installed-hash",
        ".dist/panel.js",
        "demo_mod-0.0.9.zip",  # an older pack, left in the folder
        "tests/fixture.zip",
    ):
        (folder / artifact).parent.mkdir(parents=True, exist_ok=True)
        (folder / artifact).write_text("x")
    monkeypatch.chdir(folder)  # default output lands inside the module folder
    assert main(["pack"]) == 0
    names = zipfile.ZipFile(folder / "demo_mod-0.1.0.zip").namelist()
    assert sorted(names) == [
        ".dist/panel.js",
        "HOW_TO_USE.md",
        "layout.json",
        "manifest.yaml",
        "module.py",
        "tests/fixture.zip",
        "tests/test_demo_mod.py",
    ]
    assert not {part for name in names for part in name.split("/")} & PACK_EXCLUDE


def test_pack_needs_the_frontend_bundles(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    assert main(["new", "demo_mod", "--dir", str(tmp_path)]) == 0
    folder = tmp_path / "demo_mod"
    with (folder / "manifest.yaml").open("a", encoding="utf-8") as f:
        f.write(
            "\nfrontend:\n  panel: ./panel/index.tsx\n  widgets:\n"
            "    - id: trend\n      entry: ./widgets/Trend.tsx\n"
            "      settings_entry: ./widgets/TrendSettings.tsx\n"
        )
    # validate checks the entry files exist; only the built bundles are missing.
    for entry in ("panel/index.tsx", "widgets/Trend.tsx", "widgets/TrendSettings.tsx"):
        (folder / entry).parent.mkdir(parents=True, exist_ok=True)
        (folder / entry).write_text("export default function X() { return null; }\n")
    archive = tmp_path / "demo.zip"
    capsys.readouterr()
    assert main(["pack", str(folder), "-o", str(archive)]) == 1
    err = capsys.readouterr().err
    assert ".dist/ lacks panel.js, widget-trend.js, widget-trend-settings.js" in err
    assert f"FF_MODULES_DIR={tmp_path.resolve()} node apps/web/scripts/bundle-modules.mjs" in err
    assert not archive.exists()

    for bundle in ("panel.js", "widget-trend.js", "widget-trend-settings.js"):
        (folder / ".dist").mkdir(exist_ok=True)
        (folder / ".dist" / bundle).write_text("x")
    assert main(["pack", str(folder), "-o", str(archive)]) == 0
    assert {n for n in zipfile.ZipFile(archive).namelist() if n.startswith(".dist/")} == {
        ".dist/panel.js",
        ".dist/widget-trend.js",
        ".dist/widget-trend-settings.js",
    }


_PARAM_MODULE = """
from typing import Any

from fastapi import Query
from pydantic import BaseModel

from mate.sdk import Module, ModuleContext, on_event, route


class Body(BaseModel):
    names: list[str]


class DemoModModule(Module):
    id = "demo_mod"

    @route.get("/top")
    async def top(
        self, ctx: ModuleContext, limit: int = Query(...), label: str = Query("all")
    ) -> dict[str, Any]:
        return {"limit": limit, "label": label}

    @route.post("/pick")
    async def pick(self, ctx: ModuleContext, body: Body) -> dict[str, Any]:
        return {"model": type(body).__name__, "names": body.names}

    @on_event("log.imported")
    async def precompute(self, ctx: ModuleContext, payload: dict[str, Any]) -> None:
        await ctx.cache.set("payload", payload)
"""


def test_run_params(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    assert main(["new", "demo_mod", "--dir", str(tmp_path)]) == 0
    folder = str(tmp_path / "demo_mod")
    (tmp_path / "demo_mod" / "module.py").write_text(_PARAM_MODULE, encoding="utf-8")
    capsys.readouterr()

    assert main(["run", "top", folder]) == 2
    assert capsys.readouterr().err == "error: handler top needs --param limit=...\n"
    assert main(["run", "top", folder, "--param", "limit=3", "--param", "nope=1"]) == 2
    assert "takes no parameter nope" in capsys.readouterr().err

    assert main(["run", "top", folder, "--param", "limit=3"]) == 0
    assert json.loads(capsys.readouterr().out)["result"] == {"limit": 3, "label": "all"}
    assert main(["run", "top", folder, "--param", "limit=3", "--param", "label=a=b"]) == 0
    assert json.loads(capsys.readouterr().out)["result"] == {"limit": 3, "label": "a=b"}
    # Typed like the platform: a `str` keeps its text, a model param gets a model.
    assert main(["run", "top", folder, "--param", "limit=3", "--param", "label=007"]) == 0
    assert json.loads(capsys.readouterr().out)["result"]["label"] == "007"
    assert main(["run", "pick", folder, "--param", 'body={"names": ["a"]}']) == 0
    assert json.loads(capsys.readouterr().out)["result"] == {"model": "Body", "names": ["a"]}
    assert main(["run", "top", folder, "--param", "limit=many"]) == 2
    assert "--param limit:" in capsys.readouterr().err

    assert main(["run", "precompute", folder, "--param", "k=[1, 2]"]) == 0
    payload = json.loads(capsys.readouterr().out)["cache"]["payload"]
    assert payload["k"] == [1, 2] and {"log_id", "user_id"} <= payload.keys()


def test_cli_reports_problems(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    assert main(["new", "Bad-Id", "--dir", str(tmp_path)]) == 2
    assert main(["new", "demo_mod", "--dir", str(tmp_path)]) == 0
    assert main(["new", "demo_mod", "--dir", str(tmp_path)]) == 1

    folder = tmp_path / "demo_mod"
    (folder / "HOW_TO_USE.md").write_text("# How to use Something Else\n", encoding="utf-8")
    capsys.readouterr()
    assert main(["validate", str(folder)]) == 1
    assert main(["pack", str(folder), "-o", str(tmp_path / "x.zip")]) == 1
    out = capsys.readouterr().out
    assert "FAIL demo_mod" in out
    assert "the title must read `# How to use Demo mod`" in out
    assert not (tmp_path / "x.zip").exists()

    assert main(["run", "missing", str(folder)]) == 2
    assert "handlers: recount, summary" in capsys.readouterr().err

    assert main(["run", "recount", str(folder), "--log", str(tmp_path / "nope.csv")]) == 2
    assert capsys.readouterr().err.startswith("error: ")
    (tmp_path / "log.xes").write_text("<log/>")
    assert main(["run", "recount", str(folder), "--log", str(tmp_path / "log.xes")]) == 2
    assert "reads .csv or .parquet only" in capsys.readouterr().err


async def test_fake_context_serves_the_sample_log(tmp_path: Path) -> None:
    log = sample_log(rows=20)
    ctx = await build_test_context(
        log_id=log.id, module_id="demo_mod", config={"k": 1}, workdir=tmp_path
    )
    # A second context on the same workdir must not overwrite the first one's log.
    await build_test_context(sample_log(), workdir=tmp_path)

    async with ctx.event_log as events:
        frame = await events.pandas()
        rows = await events.duckdb_fetch("SELECT count(*), count(DISTINCT case_id) FROM events")
    assert len(frame) == 20
    assert rows == [(20, log.frame["case_id"].nunique())]
    assert ctx.config.get("k") == 1

    await ctx.progress.update(0.5, "half")
    assert isinstance(ctx.progress, RecordingProgress)
    assert ctx.progress.calls[0]["message"] == "half"


async def test_fake_log_matches_the_importers_events_parquet(tmp_path: Path) -> None:
    source = tmp_path / "log.csv"
    source.write_text(
        "case_id,activity,timestamp,amount\n"
        "9,1,2026-01-01T10:00:00+02:00,5\n"
        "10,2,2026-01-01T09:00:00+00:00,7\n"
        "9,3,2026-01-01T07:00:00+00:00,6\n"
    )
    ctx = await build_test_context(load_log(source))
    async with ctx.event_log as events:
        frame = await events.pandas()
        (ts,) = (await events.duckdb_fetch("SELECT timestamp FROM events LIMIT 1"))[0]

    # Like mate.api.ingest.dispatch: string ids, naive UTC, rows by case then time.
    assert list(frame.columns) == ["case_id", "activity", "timestamp", "amount"]
    assert frame["case_id"].tolist() == ["10", "9", "9"]
    assert frame["activity"].tolist() == ["2", "3", "1"]
    assert frame["timestamp"].dt.tz is None
    assert frame["timestamp"].tolist()[1:] == [
        pd.Timestamp("2026-01-01 07:00"),
        pd.Timestamp("2026-01-01 08:00"),
    ]
    assert ts.tzinfo is None


async def test_memory_cache_matches_the_platform_cache() -> None:
    cache = MemoryCache()
    for key in ("a/b", "..", "up..", "trailing."):
        with pytest.raises(ValueError, match="flat name"):
            await cache.set(key, 1)
        with pytest.raises(ValueError, match="flat name"):
            await cache.get(key)
    assert not await cache.exists(".hidden")

    await cache.set("pair", (1, 2))
    assert await cache.get("pair") == [1, 2]  # JSON round trip
    await cache.set("raw", bytearray(b"x"))
    assert await cache.get("raw") == b"x"
    await cache.set("frame", pd.DataFrame({"a": [1, 2]}, index=[5, 7]))
    frame = await cache.get("frame")
    assert frame.index.tolist() == [0, 1]  # to_parquet(index=False) drops the index
    assert frame["a"].tolist() == [1, 2]


async def test_read_only_caches_refuse_only_the_reserved_keys(tmp_path: Path) -> None:
    from mate.api.modules.cache import ResultCache

    ctx = await build_test_context(sample_log(), read_only=True)
    assert ctx.read_only is True
    assert (await build_test_context(sample_log())).read_only is False
    platform = ResultCache("log", "mod", "owner", root=tmp_path, read_only=True)
    for cache in (ctx.cache, platform):
        await cache.set("result", {"n": 1})  # a viewer's computed result still caches
        await cache.delete("result")
        with pytest.raises(ReadOnlyContextError):
            await cache.set("__upload", {"n": 1})
        with pytest.raises(ReadOnlyContextError):
            await cache.delete("__upload")
    with pytest.raises(ReadOnlyContextError):
        platform.clear()
