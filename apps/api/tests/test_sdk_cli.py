"""Module-author tooling in the SDK: ``mate.sdk.testing`` and the ``mate-module`` CLI.

The CLI is driven end to end on a freshly scaffolded module - new, validate,
run, pack - so a template that stops validating, running or packing cleanly
fails here, not in an author's first five minutes.
"""

from __future__ import annotations

import json
import subprocess
import sys
import zipfile
from pathlib import Path

import pandas as pd
import pytest

from mate.sdk.cli import PACK_EXCLUDE, main
from mate.sdk.errors import ReadOnlyContextError
from mate.sdk.testing import (
    MemoryCache,
    RecordingProgress,
    build_test_context,
    load_log,
    sample_log,
)


def test_new_validate_run_pack(
    tmp_path: Path, capsys: pytest.CaptureFixture[str], monkeypatch: pytest.MonkeyPatch
) -> None:
    assert main(["new", "demo_mod", "--dir", str(tmp_path)]) == 0
    folder = tmp_path / "demo_mod"
    assert main(["validate", str(folder)]) == 0

    capsys.readouterr()
    assert main(["run", "precompute", str(folder)]) == 0
    out = json.loads(capsys.readouterr().out)
    assert out["result"] is None
    assert out["cache"] == {"summary": {"events": 9, "cases": 3, "activities": 4}}

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


def test_scaffolded_tests_pass(tmp_path: Path) -> None:
    assert main(["new", "demo_mod", "--dir", str(tmp_path)]) == 0
    done = subprocess.run(
        [sys.executable, "-m", "pytest", "-q", "-p", "no:cacheprovider", "tests"],
        cwd=tmp_path / "demo_mod",
        capture_output=True,
        text=True,
        check=False,
    )
    assert done.returncode == 0, done.stdout + done.stderr


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
    assert "the title must read `# How to use Demo Mod`" in out
    assert not (tmp_path / "x.zip").exists()

    assert main(["run", "missing", str(folder)]) == 2
    assert "handlers: precompute, summary" in capsys.readouterr().err

    assert main(["run", "precompute", str(folder), "--log", str(tmp_path / "nope.csv")]) == 2
    assert capsys.readouterr().err.startswith("error: ")
    (tmp_path / "log.xes").write_text("<log/>")
    assert main(["run", "precompute", str(folder), "--log", str(tmp_path / "log.xes")]) == 2
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
    assert rows == [(20, 7)]
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
