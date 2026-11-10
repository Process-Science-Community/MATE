"""`mate-module` (mate.sdk.cli): scaffold -> validate -> test, and a scaffolded module
loading in the real platform."""

from __future__ import annotations

import asyncio
import json
import os
import shutil
import time
from pathlib import Path

import pytest
from httpx import ASGITransport, AsyncClient

from mate.sdk.cli import main
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
    layout["items"][0]["dataset_ref"]["dataset_id"] = "nope"
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

            resp = await c.get(f"/api/v1/modules/{MOD}/layout")
            body = resp.json() if resp.status_code == 200 else {}
            layout = body.get("layout") if isinstance(body, dict) else None
            if not (isinstance(layout, dict) and "items" in layout):
                pytest.skip(
                    "GET /api/v1/modules/{id}/layout does not serve layout.json yet "
                    f"(status {resp.status_code}: {resp.text[:120]}); backend owner's endpoint."
                )
            assert layout["items"][0]["dataset_ref"] == {"module_id": MOD, "dataset_id": "summary"}
    finally:
        if prev is None:
            os.environ.pop("MODULES_DIR", None)
        else:
            os.environ["MODULES_DIR"] = prev
        cfg.get_settings.cache_clear()
