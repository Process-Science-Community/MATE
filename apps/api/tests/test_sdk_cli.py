"""`mate-module` (mate.sdk.cli): scaffold -> validate -> test, and a scaffolded module
loading in the real platform."""

from __future__ import annotations

import asyncio
import json
import os
import shutil
import time
from pathlib import Path
from typing import Any

import pytest
from httpx import ASGITransport, AsyncClient
from pydantic import ValidationError

from mate.sdk.cli import main
from mate.sdk.layout import ModuleLayout
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
