"""A shared dashboard's recipient reads the owner's log but never changes it.

Loads the real ``interop`` and ``conformance`` modules, shares a dashboard bound to the
owner's log and drives the recipient through their write routes (403, the owner's
models unchanged) and their reads (still served from the owner's data). The contexts
behind it carry ``ctx.read_only`` (``loader._make_context``); the result cache refuses
the owner's reserved ``__*`` entries even to a handler that skips the check.
"""

from __future__ import annotations

import base64
import contextlib
import json
import os
import shutil
from collections.abc import AsyncGenerator
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import pytest
from fastapi import UploadFile
from httpx import ASGITransport, AsyncClient

from mate.sdk.context import ModuleContext

from .conftest import TEST_USER_ID
from .test_module_processing import import_sample, module_jobs, wait_until

REPO_MODULES = Path(__file__).resolve().parents[3] / "modules"
PROBE = Path(__file__).parent / "fixtures" / "modules" / "shared_probe"
PROBE_API = "/api/v1/modules/shared_probe"
BPMN = REPO_MODULES / "conformance" / "tests" / "fixtures" / "reference.bpmn"
VIEWER_ID = "00000000-0000-7000-8000-0000000000c1"
CONF = "/api/v1/modules/conformance"


def _now() -> datetime:
    return datetime.now(UTC).replace(tzinfo=None)


def _pnml(label: str) -> bytes:
    """A one-transition PNML whose transition is labelled *label*."""
    return (
        '<?xml version="1.0" encoding="UTF-8"?><pnml>'
        '<net id="net" type="http://www.pnml.org/version-2009/grammar/pnmlcoremodel">'
        '<page id="p"><place id="start"><name><text>start</text></name>'
        "<initialMarking><text>1</text></initialMarking></place>"
        '<place id="end"><name><text>end</text></name></place>'
        f'<transition id="t"><name><text>{label}</text></name></transition>'
        '<arc id="a1" source="start" target="t"/><arc id="a2" source="t" target="end"/>'
        '</page><finalmarkings><marking><place idref="end"><text>1</text></place></marking>'
        "</finalmarkings></net></pnml>"
    ).encode()


@contextlib.asynccontextmanager
async def _client(
    tmp_path: Path,
    sources: tuple[Path, ...] = (REPO_MODULES / "interop", REPO_MODULES / "conformance"),
) -> AsyncGenerator[tuple[AsyncClient, dict[str, str]], None]:
    """The app with *sources* (default: the real interop + conformance modules) installed
    for owner and viewer; ``who["id"]`` is the caller."""
    for src in sources:
        shutil.copytree(
            src,
            tmp_path / "modules" / src.name,
            ignore=shutil.ignore_patterns(".venv", "__pycache__", "tests", "node_modules"),
        )
    prev = os.environ.get("MODULES_DIR")
    os.environ["MODULES_DIR"] = str(tmp_path / "modules")
    from mate.api import config as cfg

    cfg.get_settings.cache_clear()
    shutil.rmtree(cfg.get_settings().uploaded_modules_dir, ignore_errors=True)
    try:
        from mate.api.auth.dependencies import CurrentUser, get_current_user
        from mate.api.db.engine import get_sessionmaker
        from mate.api.db.models import User
        from mate.api.main import create_app
        from mate.api.modules.installs import record_install

        app = create_app()
        who = {"id": TEST_USER_ID}

        async def _current() -> CurrentUser:
            uid = who["id"]
            return CurrentUser(
                id=uid,
                email=f"{uid}@mate.local",
                preferred_username=uid[:8],
                name=uid[:8],
                roles=("user",),
            )

        app.dependency_overrides[get_current_user] = _current
        async with (
            AsyncClient(transport=ASGITransport(app=app), base_url="http://testserver") as c,
            app.router.lifespan_context(app),
        ):
            async with get_sessionmaker()() as s:
                if await s.get(User, VIEWER_ID) is None:
                    s.add(
                        User(
                            id=VIEWER_ID,
                            email=f"{VIEWER_ID}@mate.local",
                            created_at=_now(),
                            last_seen_at=_now(),
                        )
                    )
                for uid in (TEST_USER_ID, VIEWER_ID):
                    for src in sources:
                        await record_install(s, uid, src.name, "upload")
                await s.commit()
            yield c, who
    finally:
        if prev is None:
            os.environ.pop("MODULES_DIR", None)
        else:
            os.environ["MODULES_DIR"] = prev
        cfg.get_settings.cache_clear()


async def _share_with_viewer(log_id: str) -> None:
    from mate.api.db.engine import get_sessionmaker
    from mate.api.db.models import Dashboard, DashboardShare
    from mate.api.uuid7 import uuid7_str

    dash_id = uuid7_str()
    async with get_sessionmaker()() as s:
        s.add(
            Dashboard(
                id=dash_id,
                user_id=TEST_USER_ID,
                name="shared",
                event_log_id=log_id,
                log_model="case_centric",
                layout_json={},
                created_at=_now(),
                updated_at=_now(),
            )
        )
        await s.flush()
        s.add(
            DashboardShare(
                id=uuid7_str(),
                dashboard_id=dash_id,
                target_user_id=VIEWER_ID,
                created_by=TEST_USER_ID,
                created_at=_now(),
            )
        )
        await s.commit()


def _labels(model: Any) -> set[str]:
    return {t["label"] for t in model["transitions"]}


@pytest.mark.asyncio
async def test_a_share_recipient_reads_but_never_changes_the_owners_models(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from mate.api.modules import get_module_loader
    from mate.api.modules.cache import ResultCache

    async with _client(tmp_path) as (c, who):
        log_id = await import_sample(c)
        q = {"log_id": log_id}
        imp = "/api/v1/modules/interop/import/pnml"
        r = await c.post(imp, params=q, files={"file": ("a.pnml", _pnml("owner_task"))})
        assert r.status_code == 200, r.text
        with BPMN.open("rb") as f:
            r = await c.post(f"{CONF}/model", params=q, files={"file": ("reference.bpmn", f)})
        assert r.status_code == 200, r.text
        models = (await c.get(f"{CONF}/models", params=q)).json()
        await _share_with_viewer(log_id)

        owner_cache = ResultCache(log_id, "interop", TEST_USER_ID)
        keys = ("imported_petri_net", "__interop_imported_petri_net")
        before = {k: await owner_cache.get(k) for k in keys}
        assert _labels(before["imported_petri_net"]) == {"owner_task"}

        who["id"] = VIEWER_ID
        # Every write is refused and leaves the owner's models as they were.
        r = await c.post(imp, params=q, files={"file": ("b.pnml", _pnml("viewer_task"))})
        assert r.status_code == 403, r.text
        r = await c.post(f"{CONF}/model", params=q, files={"file": ("x.bpmn", BPMN.read_bytes())})
        assert r.status_code == 403, r.text
        assert (await c.delete(f"{CONF}/model/reference.bpmn", params=q)).status_code == 403
        r = await c.post(f"{CONF}/model/reference.bpmn/activate", params=q)
        assert r.status_code == 403
        assert {k: await owner_cache.get(k) for k in keys} == before
        # A compute run would rewrite the owner's results under the viewer's config:
        # refused by the platform before a job is queued.
        r = await c.post(f"{CONF}/run", params=q)
        assert r.status_code == 403, r.text
        assert r.json()["detail"] == "Read-only: this log is shared with you"
        assert await module_jobs(log_id, "conformance") == []

        # Reads still serve the owner's data: the artifact by ref (how a board card reads
        # a cache-only dataset), the dataset, the models.
        ref = f"{log_id}/interop/imported_petri_net"
        r = await c.get("/api/v1/artifacts/content", params={"ref": ref})
        assert r.status_code == 200, r.text
        assert r.json() == before["imported_petri_net"]
        r = await c.get("/api/v1/datasets/interop/imported_petri_net", params=q)
        assert r.status_code == 200, r.text
        assert "owner_task" in r.text
        assert (await c.get(f"{CONF}/models", params=q)).json() == models

        # A handler without the check cannot write the owner's reserved entries
        # either - the platform's route gate (and behind it the cache) answers 403.
        async def unguarded(ctx: ModuleContext, fmt: str, file: UploadFile) -> dict[str, Any]:
            await ctx.cache.set("__interop_imported_petri_net", {"kind": "petri_net"})
            return {}

        instance = get_module_loader().loaded["interop"].instance
        monkeypatch.setattr(instance, "import_model", unguarded)
        r = await c.post(imp, params=q, files={"file": ("c.pnml", _pnml("viewer_task"))})
        assert r.status_code == 403, r.text
        assert {k: await owner_cache.get(k) for k in keys} == before
        monkeypatch.undo()

        # The owner still can.
        who["id"] = TEST_USER_ID
        r = await c.post(imp, params=q, files={"file": ("d.pnml", _pnml("second"))})
        assert r.status_code == 200, r.text
        assert _labels(await owner_cache.get("imported_petri_net")) == {"second"}


async def _wait_job(c: AsyncClient, job_id: str) -> dict[str, Any]:
    async def _settled() -> bool:
        return (await c.get(f"/api/v1/jobs/{job_id}")).json()["status"] in (
            "completed",
            "failed",
            "cancelled",
        )

    await wait_until(_settled, what=f"job {job_id} to settle")
    return (await c.get(f"/api/v1/jobs/{job_id}")).json()


@pytest.mark.asyncio
async def test_the_platform_refuses_a_viewers_write_routes_unless_viewer_safe(
    tmp_path: Path,
) -> None:
    """Non-GET module routes 403 for a share recipient before they run or queue a job;
    `viewer_safe` routes still serve them; the owner is unaffected."""
    from mate.api.modules.cache import ResultCache

    async with _client(tmp_path, (PROBE,)) as (c, who):
        log_id = await import_sample(c)
        q = {"log_id": log_id}
        assert (await c.post(f"{PROBE_API}/result", params=q)).status_code == 200
        owner_job = (await c.post(f"{PROBE_API}/result-job", params=q)).json()["job_id"]
        assert (await _wait_job(c, owner_job))["status"] == "completed"
        owner_job = (await c.post(f"{PROBE_API}/reserved-job", params=q)).json()["job_id"]
        assert (await _wait_job(c, owner_job))["status"] == "completed"
        owner = ResultCache(log_id, "shared_probe", TEST_USER_ID)
        before = {k: await owner.get(k) for k in ("result", "__probe_state")}
        assert before["result"] == {"by": TEST_USER_ID, "via": "job"}
        jobs_before = len(await module_jobs(log_id, "shared_probe"))
        await _share_with_viewer(log_id)

        who["id"] = VIEWER_ID
        assert (await c.get(f"{PROBE_API}/state", params=q)).json() == {
            "result": before["result"],
            "read_only": True,
        }
        # A plain write route and a job route: 403 up front, no job queued.
        for path in ("/result", "/result-job"):
            r = await c.post(f"{PROBE_API}{path}", params=q)
            assert r.status_code == 403, (path, r.text)
            assert r.json()["detail"] == "Read-only: this log is shared with you"
        assert len(await module_jobs(log_id, "shared_probe")) == jobs_before
        # A viewer-safe route runs for the viewer.
        r = await c.post(f"{PROBE_API}/derived", params=q)
        assert r.status_code == 200 and r.json() == {"read_only": True}
        # A viewer-safe job that still writes owner state fails, saying why.
        job = await _wait_job(
            c, (await c.post(f"{PROBE_API}/reserved-job", params=q)).json()["job_id"]
        )
        assert job["status"] == "failed"
        assert job["error"].startswith("Read-only: this log is shared with you: ")
        # A dataset read that touches owner state is a 403, not a 500.
        r = await c.get("/api/v1/datasets/shared_probe/reserved_read", params=q)
        assert r.status_code == 403, r.text
        assert {k: await owner.get(k) for k in ("result", "__probe_state")} == before

        # The owner is unaffected.
        who["id"] = TEST_USER_ID
        assert (await c.post(f"{PROBE_API}/result", params=q)).status_code == 200
        assert await owner.get("result") == {"by": TEST_USER_ID}
        r = await c.get("/api/v1/datasets/shared_probe/reserved_read", params=q)
        assert r.status_code == 200, r.text


@pytest.mark.asyncio
async def test_a_viewers_own_config_never_reaches_the_owners_cache(tmp_path: Path) -> None:
    """A viewer's compute-on-miss lands in the owner's cache, so it runs with the owner's
    declared config (here: the default k), not the viewer's own module config."""
    from mate.api.modules.cache import ResultCache

    async with _client(tmp_path, (REPO_MODULES / "complexity",)) as (c, who):
        log_id = await import_sample(c)
        q = {"log_id": log_id}
        await _share_with_viewer(log_id)
        owner = ResultCache(log_id, "complexity", TEST_USER_ID)
        assert await owner.get("metrics") is None  # lazy: the owner has not run it

        who["id"] = VIEWER_ID
        put = {"config": {"exponential_k": 5.0}}
        assert (await c.put("/api/v1/modules/complexity/config", json=put)).status_code == 200
        r = await c.get("/api/v1/modules/complexity/metrics", params=q)
        assert r.status_code == 200, r.text
        assert r.json()["exponential_k"] == 1.0

        who["id"] = TEST_USER_ID
        stored = await owner.get("metrics")
        assert stored is not None and stored["exponential_k"] == 1.0
        r = await c.get("/api/v1/modules/complexity/metrics", params=q)
        assert r.json()["exponential_k"] == 1.0


_RELOAD_MANIFEST = """\
id: reload_probe
name: Reload Probe
version: 0.1.0
category: other
description: Test - routes whose method, path and viewer_safe change across a reload.
license: MIT
dependencies:
  python:
    inherit: [duckdb, pandas]
    isolation: in_process
"""

_RELOAD_HEAD = """\
from typing import Any

from mate.sdk import Module, ModuleContext, job, route


class ReloadProbe(Module):
    id = "reload_probe"

    # A GET and a POST job on one path (both versions): each must run its own handler.
    @route.get("/both")
    @job(title="probe read")
    async def both_read(self, ctx: ModuleContext) -> dict[str, Any]:
        return {"v": "read"}

    @route.post("/both")
    @job(title="probe write")
    async def both_write(self, ctx: ModuleContext) -> dict[str, Any]:
        await ctx.cache.set("result", {"by": ctx.user_id})
        return {"v": "write"}
"""

# Before the reload: a GET, a path, a viewer-safe write and a viewer-safe job.
_RELOAD_A = """
    @route.get("/act")
    async def act(self, ctx: ModuleContext) -> dict[str, Any]:
        return {"v": "a"}

    @route.get("/gone")
    async def gone(self, ctx: ModuleContext) -> dict[str, Any]:
        return {"v": "a"}

    @route.post("/derive", viewer_safe=True)
    async def derive(self, ctx: ModuleContext) -> dict[str, Any]:
        return {"v": "a"}

    @route.post("/run", viewer_safe=True)
    @job(title="probe run")
    async def run(self, ctx: ModuleContext) -> dict[str, Any]:
        return {"v": "a"}
"""

# After: /act is a write, /gone moved, /derive and /run lost viewer_safe.
_RELOAD_B = """
    @route.post("/act")
    async def act(self, ctx: ModuleContext) -> dict[str, Any]:
        await ctx.cache.set("result", {"by": ctx.user_id})
        return {"v": "b"}

    @route.get("/moved")
    async def gone(self, ctx: ModuleContext) -> dict[str, Any]:
        return {"v": "b"}

    @route.post("/derive")
    async def derive(self, ctx: ModuleContext) -> dict[str, Any]:
        await ctx.cache.set("result", {"by": ctx.user_id})
        return {"v": "b"}

    @route.post("/run")
    @job(title="probe run")
    async def run(self, ctx: ModuleContext) -> dict[str, Any]:
        await ctx.cache.set("result", {"by": ctx.user_id})
        return {"v": "b"}
"""
RELOAD_API = "/api/v1/modules/reload_probe"


def _reload_probe_src(tmp_path: Path) -> Path:
    """The probe module's source folder, at version A."""
    src = tmp_path / "src" / "reload_probe"
    src.mkdir(parents=True)
    (src / "manifest.yaml").write_text(_RELOAD_MANIFEST)
    (src / "module.py").write_text(_RELOAD_HEAD + _RELOAD_A)
    return src


async def _reload_to_b(tmp_path: Path) -> None:
    """Hot-reload the installed probe module to version B."""
    from mate.api.modules import get_module_loader
    from mate.sdk.manifest import Manifest

    folder = tmp_path / "modules" / "reload_probe"
    (folder / "module.py").write_text(_RELOAD_HEAD + _RELOAD_B)
    await get_module_loader().load_one(folder, Manifest.load_yaml(folder / "manifest.yaml"))


@pytest.mark.asyncio
async def test_a_reloaded_route_serves_and_gates_by_its_current_spec(tmp_path: Path) -> None:
    """FastAPI cannot unmount a route, so the first-bound mount keeps matching after a
    reload: it must answer by what the module declares now - 405 for a moved method,
    404 for a moved path, the viewer gate by the live `viewer_safe` - never run the
    handler under the spec it was first bound with."""
    from mate.api.modules.cache import ResultCache

    src = _reload_probe_src(tmp_path)
    api = RELOAD_API

    async with _client(tmp_path, (src,)) as (c, who):
        log_id = await import_sample(c)
        q = {"log_id": log_id}
        assert (await c.get(f"{api}/act", params=q)).json() == {"v": "a"}
        await _share_with_viewer(log_id)
        await _reload_to_b(tmp_path)

        who["id"] = VIEWER_ID
        r = await c.get(f"{api}/act", params=q)
        assert r.status_code == 405 and r.headers["allow"] == "POST", r.text
        assert (await c.get(f"{api}/gone", params=q)).status_code == 404
        assert (await c.get(f"{api}/moved", params=q)).json() == {"v": "b"}
        for path in ("/act", "/derive", "/run"):
            r = await c.post(f"{api}{path}", params=q)
            assert r.status_code == 403, (path, r.text)
        assert await module_jobs(log_id, "reload_probe") == []
        assert await ResultCache(log_id, "reload_probe", TEST_USER_ID).get("result") is None

        # The owner reaches every live route through the same stale mounts.
        who["id"] = TEST_USER_ID
        assert (await c.post(f"{api}/derive", params=q)).json() == {"v": "b"}
        job_id = (await c.post(f"{api}/run", params=q)).json()["job_id"]
        assert (await _wait_job(c, job_id))["status"] == "completed"


@pytest.mark.asyncio
async def test_a_viewers_get_job_never_runs_the_post_job_on_its_path(tmp_path: Path) -> None:
    """A GET and a POST `@job` on one path are two job types: the viewer's ungated GET
    runs the GET handler, not the (refused) POST one that writes the owner's cache."""
    from mate.api.modules.cache import ResultCache

    async with _client(tmp_path, (_reload_probe_src(tmp_path),)) as (c, who):
        log_id = await import_sample(c)
        q = {"log_id": log_id}
        await _share_with_viewer(log_id)

        who["id"] = VIEWER_ID
        assert (await c.post(f"{RELOAD_API}/both", params=q)).status_code == 403
        job_id = (await c.get(f"{RELOAD_API}/both", params=q)).json()["job_id"]
        assert (await _wait_job(c, job_id))["status"] == "completed"
        assert await ResultCache(log_id, "reload_probe", TEST_USER_ID).get("result") is None


@pytest.mark.asyncio
async def test_a_queued_job_is_gated_by_the_spec_live_when_it_runs(tmp_path: Path) -> None:
    """A viewer's job queued while its route was `viewer_safe` fails read-only, writing
    nothing, once a reload drops `viewer_safe` before it runs."""
    from mate.api.modules.cache import ResultCache
    from mate.api.modules.loader import READ_ONLY_DETAIL

    async with _client(tmp_path, (_reload_probe_src(tmp_path),)) as (c, who):
        log_id = await import_sample(c)
        q = {"log_id": log_id}
        await _share_with_viewer(log_id)

        who["id"] = VIEWER_ID
        # Pausing the viewer's queue parks the job until the reload is done.
        assert (await c.post("/api/v1/jobs/queue/pause")).status_code == 204
        r = await c.post(f"{RELOAD_API}/run", params=q)
        assert r.status_code == 200, r.text
        await _reload_to_b(tmp_path)
        assert (await c.post("/api/v1/jobs/queue/resume")).status_code == 204

        job = await _wait_job(c, r.json()["job_id"])
        assert (job["status"], job["error"]) == ("failed", READ_ONLY_DETAIL)
        assert await ResultCache(log_id, "reload_probe", TEST_USER_ID).get("result") is None


# A second side of the same log needs its own filter (identical sides are a 422).
_ALICE = [{"field": "resource", "op": "equals", "value": "alice"}]


def _sides(a: str, b: str) -> str:
    """process_comparison's `sides` param: log *a* raw against log *b* narrowed to alice."""
    raw = json.dumps([{"log": a, "filter": []}, {"log": b, "filter": _ALICE}]).encode()
    return base64.b64encode(raw).decode()


@pytest.mark.asyncio
async def test_a_viewer_compares_only_the_shared_log(tmp_path: Path) -> None:
    """`ctx.open_event_log` in a viewer's context opens the shared log and nothing else:
    neither the viewer's own logs (their comparison would land in the owner's cache)
    nor the owner's unshared ones."""
    from mate.api.config import get_settings

    async with _client(tmp_path, (REPO_MODULES / "process_comparison",)) as (c, who):
        shared = await import_sample(c, "owner-shared")
        private = await import_sample(c, "owner-private")
        await _share_with_viewer(shared)

        who["id"] = VIEWER_ID
        mine = await import_sample(c, "viewer-own")
        summary = "/api/v1/modules/process_comparison/summary"
        for sides in ((mine, mine), (shared, mine), (shared, private)):
            r = await c.get(summary, params={"log_id": shared, "sides": _sides(*sides)})
            assert r.status_code == 404, (sides, r.text)
        owner_results = get_settings().module_results_dir_for(TEST_USER_ID) / shared
        assert not [p for p in owner_results.rglob("*") if p.is_file()]

        r = await c.get(summary, params={"log_id": shared, "sides": _sides(shared, shared)})
        assert r.status_code == 200, r.text


@pytest.mark.asyncio
async def test_a_viewer_never_reads_a_pcomp_result_against_an_unshared_log(
    tmp_path: Path,
) -> None:
    """pcomp's cached result names the other log; a viewer of the shared log gets 404 for
    one against the owner's unshared log, the owner still reads it."""
    from mate.api.modules.cache import ResultCache

    async with _client(tmp_path, (REPO_MODULES / "pcomp",)) as (c, who):
        shared = await import_sample(c, "shared-a")
        private = await import_sample(c, "private-b")
        await _share_with_viewer(shared)
        # What the owner's POST /permutation-test caches (module.py).
        result = {"other_log_id": private, "pvalue": 0.42}
        await ResultCache(shared, "pcomp", TEST_USER_ID).set(f"permutation__{private}", result)
        q = {"log_id": shared, "other_log_id": private}

        who["id"] = VIEWER_ID
        assert (await c.get(f"/api/v1/event-logs/{private}")).status_code == 404
        r = await c.get("/api/v1/modules/pcomp/results", params=q)
        assert r.status_code == 404, r.text

        who["id"] = TEST_USER_ID
        r = await c.get("/api/v1/modules/pcomp/results", params=q)
        assert r.status_code == 200 and r.json() == result, r.text


@pytest.mark.asyncio
async def test_a_viewers_ai_chat_gets_no_module_context_of_a_shared_log(tmp_path: Path) -> None:
    """MATE AI builds module context only for the caller's own log: a shared log's
    context would come from the owner's cache, which also holds results against the
    owner's other, unshared logs - and the prompt goes to the viewer's own endpoint."""
    import asyncio

    from mate.api.modules.cache import ResultCache

    captured: list[bytes] = []

    async def fake_llm(r: asyncio.StreamReader, w: asyncio.StreamWriter) -> None:
        head = await r.readuntil(b"\r\n\r\n")
        n = next(
            int(line.split(b":")[1])
            for line in head.split(b"\r\n")
            if line.lower().startswith(b"content-length")
        )
        captured.append(await r.readexactly(n))
        w.write(b"HTTP/1.1 200 OK\r\ncontent-type: text/event-stream\r\n\r\ndata: [DONE]\n\n")
        await w.drain()
        w.close()

    server = await asyncio.start_server(fake_llm, "127.0.0.1", 0)
    port = server.sockets[0].getsockname()[1]
    mods = (REPO_MODULES / "pcomp", REPO_MODULES / "process_comparison")
    async with server, _client(tmp_path, mods) as (c, who):
        shared = await import_sample(c, "shared-a")
        private = await import_sample(c, "private-b")
        await _share_with_viewer(shared)
        await ResultCache(shared, "pcomp", TEST_USER_ID).set(
            f"permutation__{private}", {"other_log_id": private, "marker": "PRIVATE-B-STATS"}
        )

        who["id"] = VIEWER_ID
        r = await c.put(
            "/api/v1/ai/config",
            json={
                "custom": {"api_key": "k", "base_url": f"http://127.0.0.1:{port}/v1"},
                "selected_provider": "custom",
                "selected_model": "m",
                "allow_process_data": True,
            },
        )
        assert r.status_code == 200, r.text
        r = await c.post(
            "/api/v1/ai/chat",
            json={
                "messages": [{"role": "user", "content": "hi"}],
                "context": {"log_id": shared, "module_ids": ["pcomp", "process_comparison"]},
            },
        )
        assert r.status_code == 200, r.text
        prompt = captured[0].decode() if captured else ""
        assert captured and private not in prompt and "PRIVATE-B-STATS" not in prompt
