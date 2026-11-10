"""Lite dev mode backend: MODULES_ONLY, the dev router, layout.json, hot-reload
fixes (submodule eviction, new folders, folder != id), worker tracebacks and the
env=dev staleness salt."""

from __future__ import annotations

import asyncio
import contextlib
import json
import os
import shutil
import textwrap
from collections.abc import AsyncIterator, Iterator
from pathlib import Path
from types import SimpleNamespace
from typing import Any
from unittest.mock import MagicMock

import pytest
import structlog
from httpx import AsyncClient

from mate.api.modules.discovery import DiscoveredModule, keep_only
from mate.sdk.manifest import Manifest

from .conftest import _sample_mod_client  # pyright: ignore[reportPrivateUsage]

FIXTURES = Path(__file__).parent / "fixtures" / "modules"


@contextlib.contextmanager
def _env(**values: str) -> Iterator[None]:
    from mate.api import config as cfg

    prev = {k: os.environ.get(k) for k in values}
    os.environ.update(values)
    cfg.get_settings.cache_clear()
    try:
        yield
    finally:
        for k, v in prev.items():
            if v is None:
                os.environ.pop(k, None)
            else:
                os.environ[k] = v
        cfg.get_settings.cache_clear()


@contextlib.asynccontextmanager
async def _dev_client(tmp_path: Path, **extra: str) -> AsyncIterator[AsyncClient]:
    with _env(ENV="dev", DEMO_MODE="true", **extra):
        async with _sample_mod_client(tmp_path, seed=True) as c:
            yield c


def _manifest(mid: str, requires: list[str] | None = None) -> Manifest:
    return Manifest.model_validate(
        {
            "id": mid,
            "name": mid,
            "version": "1.0.0",
            "category": "foundation",
            "requirements": {"modules": requires or []},
        }
    )


def _disc(mid: str, requires: list[str] | None = None) -> DiscoveredModule:
    return DiscoveredModule(folder=Path(mid), manifest=_manifest(mid, requires))


# -- MODULES_ONLY -------------------------------------------------------------


def test_keep_only_keeps_transitive_hard_deps() -> None:
    mods = [_disc("a", ["b"]), _disc("b", ["c"]), _disc("c"), _disc("d"), _disc("e", ["a"])]
    assert [d.id for d in keep_only(mods, {"a"})] == ["a", "b", "c"]
    assert [d.id for d in keep_only(mods, {"d", "missing"})] == ["d"]
    assert keep_only(mods, set()) == mods


def test_modules_only_ids_parses_csv() -> None:
    with _env(MODULES_ONLY=" a, b ,,c"):
        from mate.api.config import get_settings

        assert get_settings().modules_only_ids == {"a", "b", "c"}


async def test_load_all_honours_modules_only(tmp_path: Path) -> None:
    async with _dev_client(tmp_path, MODULES_ONLY="something_else") as c:
        from mate.api.modules import get_module_loader

        assert "sample_mod" not in get_module_loader().loaded
        status = (await c.get("/api/v1/dev/status")).json()
        assert status["modules_only"] == ["something_else"]
        assert status["modules"] == []


# -- dev router ---------------------------------------------------------------


async def test_dev_router_absent_when_dev_tools_off(client: AsyncClient) -> None:
    assert (await client.get("/api/v1/dev/status")).status_code == 404


async def test_dev_router_absent_in_dev_without_demo_mode(tmp_path: Path) -> None:
    with _env(ENV="dev", DEMO_MODE="false"):
        async with _sample_mod_client(tmp_path, seed=True) as c:
            assert (await c.get("/api/v1/dev/status")).status_code == 404


_LAYOUT = {
    "version": 1,
    "items": [
        {
            "i": "w1",
            "kind": "widget",
            "module_id": "sample_mod",
            "widget_id": "kpi",
            "x": 0,
            "y": 0,
            "w": 6,
            "h": 4,
        }
    ],
}


async def test_dev_status_and_layout_roundtrip(tmp_path: Path) -> None:
    async with _dev_client(tmp_path) as c:
        status = (await c.get("/api/v1/dev/status")).json()
        assert status["dev"] is True
        (mod,) = [m for m in status["modules"] if m["id"] == "sample_mod"]
        assert mod["has_layout"] is False
        assert mod["has_panel"] is False
        assert mod["load_error"] is None
        assert mod["isolation"] == "in_process"

        # No layout.json yet -> null.
        r = await c.get("/api/v1/modules/sample_mod/layout")
        assert r.status_code == 200, r.text
        assert r.json() == {"layout": None}

        r = await c.put("/api/v1/dev/modules/sample_mod/layout", json=_LAYOUT)
        assert r.status_code == 200, r.text
        written = json.loads((tmp_path / "modules" / "sample_mod" / "layout.json").read_text())
        assert written["items"][0]["widget_id"] == "kpi"
        assert r.json()["layout"]["items"][0]["i"] == "w1"

        r = await c.get("/api/v1/modules/sample_mod/layout")
        assert r.json()["layout"]["items"][0]["widget_id"] == "kpi"
        status = (await c.get("/api/v1/dev/status")).json()
        assert [m["has_layout"] for m in status["modules"] if m["id"] == "sample_mod"] == [True]


async def test_dev_put_layout_validates_and_guards(tmp_path: Path) -> None:
    async with _dev_client(tmp_path) as c:
        bad = {"version": 1, "items": [{"i": "v", "kind": "viz", "x": 0, "y": 0, "w": 4, "h": 2}]}
        r = await c.put("/api/v1/dev/modules/sample_mod/layout", json=bad)
        assert r.status_code == 422
        assert not (tmp_path / "modules" / "sample_mod" / "layout.json").exists()

        r = await c.put("/api/v1/dev/modules/nope/layout", json=_LAYOUT)
        assert r.status_code == 404

        # A module whose folder is outside modules_dir (an upload) is never written.
        from mate.api.modules import get_module_loader

        lm = get_module_loader().loaded["sample_mod"]
        upload = tmp_path / "uploads" / "sample_mod"
        upload.mkdir(parents=True)
        lm.discovered = DiscoveredModule(folder=upload, manifest=lm.manifest)
        r = await c.put("/api/v1/dev/modules/sample_mod/layout", json=_LAYOUT)
        assert r.status_code == 403
        assert not (upload / "layout.json").exists()


async def test_dev_reload_endpoint(tmp_path: Path) -> None:
    async with _dev_client(tmp_path) as c:
        r = await c.post("/api/v1/dev/modules/sample_mod/reload")
        assert r.status_code == 200, r.text
        assert r.json()["id"] == "sample_mod"
        assert r.json()["load_error"] is None
        assert (await c.post("/api/v1/dev/modules/nope/reload")).status_code == 404


# -- hot reload ---------------------------------------------------------------


async def test_hot_reload_adds_new_folder_as_default_and_unloads_by_folder(
    tmp_path: Path,
) -> None:
    async with _dev_client(tmp_path) as c:
        from mate.api.modules import get_module_loader
        from mate.api.modules.hot_reload import _ModuleEventHandler

        loader = get_module_loader()
        handler = _ModuleEventHandler(loader, asyncio.get_running_loop())
        # Folder name deliberately differs from the manifest id.
        shutil.copytree(FIXTURES / "eager_mod", tmp_path / "modules" / "renamed_folder")
        await handler._reload("renamed_folder")
        assert "eager_mod" in loader.loaded
        assert "eager_mod" in loader.default_module_ids
        ids = [m["id"] for m in (await c.get("/api/v1/modules")).json()]
        assert "eager_mod" in ids

        shutil.rmtree(tmp_path / "modules" / "renamed_folder")
        await handler._reload("renamed_folder")
        assert "eager_mod" not in loader.loaded


async def test_hot_reload_skips_new_folder_outside_modules_only(tmp_path: Path) -> None:
    async with _dev_client(tmp_path, MODULES_ONLY="sample_mod"):
        from mate.api.modules import get_module_loader
        from mate.api.modules.hot_reload import _is_ignored, _ModuleEventHandler

        loader = get_module_loader()
        shutil.copytree(FIXTURES / "eager_mod", tmp_path / "modules" / "eager_mod")
        handler = _ModuleEventHandler(loader, asyncio.get_running_loop())
        await handler._reload("eager_mod")
        assert "eager_mod" not in loader.loaded
        assert "sample_mod" in loader.loaded
        assert _is_ignored(Path("sample_mod/layout.json"))
        assert _is_ignored(Path("sample_mod/.pytest_cache/v/cache/lastfailed"))
        # A dep the boot closure kept but failed to load is retried once fixed.
        loader.load_errors["eager_mod"] = "ImportError at boot"
        await handler._reload("eager_mod")
        assert "eager_mod" in loader.loaded


def test_in_process_reload_evicts_stale_submodules(tmp_path: Path) -> None:
    from mate.api.modules.loader import ModuleLoader

    folder = tmp_path / "evict_mod"
    folder.mkdir()
    (folder / "helper.py").write_text("VALUE = 1\n")
    (folder / "module.py").write_text(
        textwrap.dedent(
            """
            from mate.sdk import Module

            from .helper import VALUE


            class EvictMod(Module):
                id = "evict_mod"
                value = VALUE
            """
        )
    )
    loader = ModuleLoader(
        modules_dir=tmp_path, bus=MagicMock(), runtime=MagicMock(), registry=MagicMock()
    )
    d = DiscoveredModule(folder=folder, manifest=_manifest("evict_mod"))
    assert loader._import_module_class(d).value == 1  # type: ignore[attr-defined]
    (folder / "helper.py").write_text("VALUE = 2222\n")  # size differs: no stale .pyc
    assert loader._import_module_class(d).value == 2222  # type: ignore[attr-defined]


# -- worker tracebacks --------------------------------------------------------


async def test_wire_error_keeps_remote_traceback() -> None:
    from mate.api.modules.subprocess_worker import WireConnection

    class _W:
        def write(self, _data: bytes) -> None: ...

        async def drain(self) -> None: ...

    reader = asyncio.StreamReader()
    conn = WireConnection(reader, _W())  # type: ignore[arg-type]
    fut = asyncio.ensure_future(conn.send_request("call", {}))
    await asyncio.sleep(0)
    reader.feed_data(
        json.dumps({"id": 1, "error": {"message": "ValueError: x", "traceback": "TB!"}}).encode()
        + b"\n"
    )
    reader.feed_eof()
    await conn.run()
    with pytest.raises(RuntimeError, match="ValueError: x") as info:
        await fut
    assert info.value.remote_traceback == "TB!"  # type: ignore[attr-defined]


async def test_report_worker_error_reaches_job_ring_and_bus() -> None:
    from mate.api.modules.ctx_rpc import report_worker_error
    from mate.api.modules.job_logs import get_job_log_buffer
    from mate.api.modules.loader import _BusForwardingLogger

    published: list[tuple[str, dict[str, Any]]] = []

    class _Bus:
        async def publish(self, topic: str, payload: dict[str, Any]) -> None:
            published.append((topic, payload))

    logger = _BusForwardingLogger(
        structlog.get_logger().bind(module_id="m1"), _Bus(), "m1", "u1", job_id="job-tb"
    )
    exc = RuntimeError("ValueError: boom")
    exc.remote_traceback = "Traceback (most recent call last):\n  boom"  # type: ignore[attr-defined]
    report_worker_error(exc, SimpleNamespace(logger=logger), "m1", "compute")
    await asyncio.sleep(0.01)

    (line,) = get_job_log_buffer().get("job-tb")
    assert line.level == "error"
    assert line.event == "modules.worker.handler_error"
    assert "boom" in line.fields["traceback"]
    assert line.fields["handler"] == "compute"
    assert published[0][0] == "module.log.error"
    assert published[0][1]["fields"]["traceback"].startswith("Traceback")

    # No traceback attached (e.g. a cancel) -> nothing logged.
    report_worker_error(RuntimeError("plain"), SimpleNamespace(logger=logger), "m1", "x")
    assert len(get_job_log_buffer().get("job-tb")) == 1


# -- dev staleness salt -------------------------------------------------------


async def test_dev_fingerprint_salts_version_only_in_dev(tmp_path: Path) -> None:
    from mate.api.modules.loader import _dev_fingerprint
    from mate.api.modules.processing import ModuleProcessingCoordinator

    (tmp_path / "module.py").write_text("x = 1\n")
    with _env(ENV="prod"):
        assert await _dev_fingerprint(tmp_path) is None
    with _env(ENV="dev"):
        fp1 = await _dev_fingerprint(tmp_path)
        (tmp_path / "module.py").write_text("x = 2\n")
        fp2 = await _dev_fingerprint(tmp_path)
    assert fp1 and fp2 and fp1 != fp2

    manifest = _manifest("m")
    loader = SimpleNamespace(
        loaded={
            "dev": SimpleNamespace(manifest=manifest, dev_fingerprint=fp1),
            "prod": SimpleNamespace(manifest=manifest, dev_fingerprint=None),
        }
    )
    coord = ModuleProcessingCoordinator(loader, MagicMock(), MagicMock())  # type: ignore[arg-type]
    assert coord._version("dev") == f"1.0.0+dev.{fp1}"
    assert coord._version("prod") == "1.0.0"
    assert coord._version("missing") is None


def test_dev_force_thread_keeps_worker_jobs_in_process(tmp_path: Path) -> None:
    from mate.api.modules.loader import ModuleLoader

    loader = ModuleLoader(
        modules_dir=tmp_path, bus=MagicMock(), runtime=MagicMock(), registry=MagicMock()
    )
    manifest = Manifest.model_validate(
        {
            "id": "w",
            "name": "w",
            "version": "1.0.0",
            "category": "foundation",
            "dependencies": {"python": {"execution": "worker"}},
        }
    )
    loader.loaded["w"] = SimpleNamespace(manifest=manifest)  # type: ignore[assignment]
    with _env(DEV_FORCE_THREAD="false"):
        assert loader._uses_worker_execution("w") is True
    with _env(DEV_FORCE_THREAD="true"):
        assert loader._uses_worker_execution("w") is False
