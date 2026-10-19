"""Lazy module processing: a log is `ready` the moment it is parsed, and a module
computes for it only when something asks - or, if its manifest says `eager`, at import.

Drives the `ModuleProcessingCoordinator` end to end with real fixture modules:
`precompute_mod` (lazy - the default) and `eager_mod` (`precompute: eager`), both
subscribing to `log.imported` with a `@job`. Chained modules and ordering live in
`test_precompute_ordering.py`.
"""

from __future__ import annotations

import asyncio
import contextlib
import os
import shutil
from collections.abc import AsyncIterator, Awaitable, Callable
from pathlib import Path
from typing import Any

import pytest
from httpx import ASGITransport, AsyncClient

from .conftest import (
    TEST_USER_EMAIL,
    TEST_USER_ID,
    _override_current_user_for_tests,
    _seed_module_installs_for_test_user,
)

FIXTURES = Path(__file__).parent / "fixtures"
IMPORT_JOB_TYPE = "event_log.import"


# --------------------------------------------------------------------------- #
# Harness
# --------------------------------------------------------------------------- #


@contextlib.asynccontextmanager
async def mods_client(
    tmp_path: Path, *mods: str, admin: bool = False
) -> AsyncIterator[AsyncClient]:
    """App with the named fixture modules loaded as defaults and owned by the test user."""
    for name in mods:
        shutil.copytree(FIXTURES / "modules" / name, tmp_path / "modules" / name)

    prev_modules = os.environ.get("MODULES_DIR")
    os.environ["MODULES_DIR"] = str(tmp_path / "modules")

    from mate.api import config as cfg

    cfg.get_settings.cache_clear()
    shutil.rmtree(cfg.get_settings().uploaded_modules_dir, ignore_errors=True)

    try:
        from mate.api.main import create_app

        app = create_app()
        _override_current_user_for_tests(app)
        if admin:
            from mate.api.auth.dependencies import CurrentUser, get_current_user

            admin_user = CurrentUser(
                id=TEST_USER_ID,
                email=TEST_USER_EMAIL,
                preferred_username="test",
                name="Test User",
                roles=("user", "admin"),
            )

            async def _admin_user() -> CurrentUser:
                return admin_user

            app.dependency_overrides[get_current_user] = _admin_user
        transport = ASGITransport(app=app)
        async with (
            AsyncClient(transport=transport, base_url="http://testserver") as c,
            app.router.lifespan_context(app),
        ):
            await _seed_module_installs_for_test_user()
            yield c
    finally:
        if prev_modules is None:
            os.environ.pop("MODULES_DIR", None)
        else:
            os.environ["MODULES_DIR"] = prev_modules
        cfg.get_settings.cache_clear()


async def wait_until(
    check: Callable[[], Awaitable[Any]], *, timeout: float = 20.0, what: str = "condition"
) -> None:
    deadline = asyncio.get_event_loop().time() + timeout
    while asyncio.get_event_loop().time() < deadline:
        if await check():
            return
        await asyncio.sleep(0.05)
    raise AssertionError(f"timed out after {timeout:g}s waiting for {what}")


async def import_sample(c: AsyncClient, name: str = "lazy-test") -> str:
    """Import `sample.csv` and return its log id once it is `ready`."""
    with (FIXTURES / "sample.csv").open("rb") as f:
        resp = await c.post(
            "/api/v1/event-logs",
            files={"file": ("sample.csv", f, "text/csv")},
            data={"name": name},
        )
    assert resp.status_code == 202, resp.text
    log_id = resp.json()["log_id"]

    async def _ready() -> bool:
        detail = (await c.get(f"/api/v1/event-logs/{log_id}")).json()
        assert detail["status"] != "failed", detail.get("error")
        return detail["status"] == "ready"

    await wait_until(_ready, what="the import to finish")
    return log_id


async def module_jobs(log_id: str, module_id: str) -> list[Any]:
    """Every `Job` row of `module_id` for `log_id`, oldest first."""
    from sqlalchemy import select

    from mate.api.db.engine import get_sessionmaker
    from mate.api.db.models import Job

    async with get_sessionmaker()() as session:
        rows = (
            (
                await session.execute(
                    select(Job).where(Job.module_id == module_id).order_by(Job.created_at, Job.id)
                )
            )
            .scalars()
            .all()
        )
    return [j for j in rows if (j.payload_json or {}).get("log_id") == log_id]


async def import_job(log_id: str) -> Any:
    from sqlalchemy import select

    from mate.api.db.engine import get_sessionmaker
    from mate.api.db.models import Job

    async with get_sessionmaker()() as session:
        rows = (
            (await session.execute(select(Job).where(Job.type == IMPORT_JOB_TYPE))).scalars().all()
        )
    return next(j for j in rows if (j.payload_json or {}).get("log_id") == log_id)


async def run_row(log_id: str, module_id: str) -> Any:
    from mate.api.db.engine import get_sessionmaker
    from mate.api.db.models import LogModuleRun

    async with get_sessionmaker()() as session:
        return await session.get(LogModuleRun, (log_id, module_id))


async def set_run(log_id: str, module_id: str, **fields: Any) -> None:
    """Upsert a run row for the log's *current* revision (unless overridden)."""
    from mate.api.db.engine import get_sessionmaker
    from mate.api.db.models import EventLog, LogModuleRun

    async with get_sessionmaker()() as session:
        log_row = await session.get(EventLog, log_id)
        assert log_row is not None
        row = await session.get(LogModuleRun, (log_id, module_id))
        if row is None:
            row = LogModuleRun(log_id=log_id, module_id=module_id, user_id=TEST_USER_ID)
            session.add(row)
        row.status = "queued"
        row.data_revision = log_row.data_revision
        for key, value in fields.items():
            setattr(row, key, value)
        await session.commit()


async def state(log_id: str, module_id: str) -> str:
    from mate.api.modules.processing import get_coordinator

    coordinator = get_coordinator()
    assert coordinator is not None
    states = await coordinator.list_states(TEST_USER_ID, log_id)
    return next(s.status for s in states if s.module_id == module_id)


async def data_revision(log_id: str) -> int:
    from mate.api.db.engine import get_sessionmaker
    from mate.api.db.models import EventLog

    async with get_sessionmaker()() as session:
        row = await session.get(EventLog, log_id)
        assert row is not None
        return int(row.data_revision)


def coordinator_or_fail() -> Any:
    from mate.api.modules.processing import get_coordinator

    coordinator = get_coordinator()
    assert coordinator is not None
    return coordinator


# --------------------------------------------------------------------------- #
# Pure helpers
# --------------------------------------------------------------------------- #


def _row(status: str, revision: int = 1, version: str | None = "1.0.0", job: str | None = "j"):
    from mate.api.modules.processing import _Row

    return _Row(
        status=status,
        job_id=job,
        data_revision=revision,
        module_version=version,
        error=None,
        finished_at=None,
    )


@pytest.mark.parametrize(
    ("row", "expected"),
    [
        (None, "idle"),
        (_row("ready"), "ready"),
        (_row("ready", revision=0), "stale"),  # the data moved on
        (_row("ready", version="0.9.0"), "stale"),  # the module was upgraded
        (_row("ready", version=None), "ready"),  # backfilled row: version unknown
        (_row("failed"), "failed"),
        (_row("failed", revision=0), "stale"),  # a failure before the data changed
        (_row("cancelled"), "cancelled"),
        (_row("queued", revision=0), "queued"),  # in flight is reported as such
        (_row("running", revision=0), "running"),
    ],
)
def test_derive_status(row: Any, expected: str) -> None:
    from mate.api.modules.processing import _derive

    assert _derive(row, 1, "1.0.0") == expected


def test_waiting_row_has_no_job() -> None:
    assert _row("queued", job=None).waiting is True
    assert _row("queued", job="j1").waiting is False
    assert _row("ready", job=None).waiting is False


@pytest.mark.parametrize(
    ("mode", "used", "allow_eager", "expected"),
    [
        ("lazy", False, True, False),  # never opened, not eager: stays lazy
        ("eager", False, True, True),  # eager, first import
        ("eager", False, False, False),  # eager but only a refilter: not woken
        ("lazy", True, False, True),  # used on the log: refreshed
        (None, True, False, True),  # chained module (no import mode) already used
        (None, False, True, False),
    ],
)
def test_auto_run_rule(mode: str | None, used: bool, allow_eager: bool, expected: bool) -> None:
    from mate.api.modules.processing import _auto_run

    assert _auto_run(mode=mode, in_working_set=used, allow_eager=allow_eager) is expected


# --------------------------------------------------------------------------- #
# Import
# --------------------------------------------------------------------------- #


@pytest.mark.asyncio
async def test_import_is_ready_at_once_and_only_eager_modules_run(tmp_path: Path) -> None:
    async with mods_client(tmp_path, "precompute_mod", "eager_mod") as c:
        log_id = await import_sample(c)
        assert (await data_revision(log_id)) >= 1

        # The eager module runs in the background, grouped under the import job.
        async def _eager_started() -> bool:
            return bool(await module_jobs(log_id, "eager_mod"))

        await wait_until(_eager_started, what="the eager module's job")
        [eager_job] = await module_jobs(log_id, "eager_mod")
        assert eager_job.parent_job_id == (await import_job(log_id)).id

        # The lazy module costs nothing: no job, no run row, state `idle`.
        await asyncio.sleep(0.3)
        assert await module_jobs(log_id, "precompute_mod") == []
        assert await run_row(log_id, "precompute_mod") is None

        states = {
            s.module_id: s for s in await coordinator_or_fail().list_states(TEST_USER_ID, log_id)
        }
        assert states["precompute_mod"].status == "idle"
        assert states["precompute_mod"].precompute == "lazy"
        assert states["eager_mod"].precompute == "eager"

        async def _eager_ready() -> bool:
            return await state(log_id, "eager_mod") == "ready"

        await wait_until(_eager_ready, what="the eager run to finish")


@pytest.mark.asyncio
async def test_disabled_eager_module_does_not_run_on_import(tmp_path: Path) -> None:
    async with mods_client(tmp_path, "eager_mod") as c:
        resp = await c.put(
            "/api/v1/modules/eager_mod/config", json={"config": {}, "enabled": False}
        )
        assert resp.status_code == 200, resp.text

        log_id = await import_sample(c)
        await asyncio.sleep(0.4)
        assert await module_jobs(log_id, "eager_mod") == []

        await c.put("/api/v1/modules/eager_mod/config", json={"config": {}, "enabled": True})


@pytest.mark.asyncio
async def test_import_without_subscriber_is_ready_immediately(client: AsyncClient) -> None:
    """No module with an import-time precompute installed (the default test client loads
    none) - the import is `ready` as soon as the parse completes."""
    log_id = await import_sample(client, "No-subscriber")
    detail = (await client.get(f"/api/v1/event-logs/{log_id}")).json()
    assert detail["status"] == "ready"


# --------------------------------------------------------------------------- #
# Admin override
# --------------------------------------------------------------------------- #


@pytest.mark.asyncio
async def test_admin_override_makes_a_lazy_module_eager(tmp_path: Path) -> None:
    async with mods_client(tmp_path, "precompute_mod", "sample_mod", admin=True) as c:
        rows = {r["id"]: r for r in (await c.get("/api/v1/admin/modules")).json()}
        assert rows["precompute_mod"]["precompute"] == {
            "manifest": "lazy",
            "effective": "lazy",
            "overridden": False,
        }
        # A module with no import-time precompute has nothing to switch.
        assert rows["sample_mod"]["precompute"] is None
        refused = await c.put("/api/v1/admin/modules/sample_mod/precompute", json={"mode": "eager"})
        assert refused.status_code == 400

        resp = await c.put(
            "/api/v1/admin/modules/precompute_mod/precompute", json={"mode": "eager"}
        )
        assert resp.status_code == 200, resp.text
        assert resp.json()["precompute"] == {
            "manifest": "lazy",
            "effective": "eager",
            "overridden": True,
        }

        log_id = await import_sample(c)

        async def _started() -> bool:
            return bool(await module_jobs(log_id, "precompute_mod"))

        await wait_until(_started, what="the overridden module's job")

        # Choosing the manifest's own value clears the override rather than storing it.
        back = await c.put("/api/v1/admin/modules/precompute_mod/precompute", json={"mode": "lazy"})
        assert back.json()["precompute"]["overridden"] is False


# --------------------------------------------------------------------------- #
# On use
# --------------------------------------------------------------------------- #


@pytest.mark.asyncio
async def test_ensure_runs_a_lazy_module_once_even_when_called_concurrently(
    tmp_path: Path,
) -> None:
    async with mods_client(tmp_path, "precompute_mod") as c:
        log_id = await import_sample(c)
        coordinator = coordinator_or_fail()

        first, second = await asyncio.gather(
            coordinator.ensure(TEST_USER_ID, log_id, "precompute_mod"),
            coordinator.ensure(TEST_USER_ID, log_id, "precompute_mod"),
        )
        assert first[0].job_id is not None
        assert first[0].job_id == second[0].job_id
        assert len(await module_jobs(log_id, "precompute_mod")) == 1

        async def _ready() -> bool:
            return await state(log_id, "precompute_mod") == "ready"

        await wait_until(_ready, what="the run to finish")

        # A finished, fresh result is never recomputed - not even with `force`.
        again = await coordinator.ensure(TEST_USER_ID, log_id, "precompute_mod", force=True)
        assert again[0].status == "ready"
        assert len(await module_jobs(log_id, "precompute_mod")) == 1


@pytest.mark.asyncio
async def test_failed_run_is_not_retried_unless_forced_nor_refreshed(tmp_path: Path) -> None:
    async with mods_client(tmp_path, "precompute_mod") as c:
        log_id = await import_sample(c)
        coordinator = coordinator_or_fail()
        await set_run(log_id, "precompute_mod", status="failed", error="boom")

        # Opening the module again does not loop on a failure...
        [failed] = await coordinator.ensure(TEST_USER_ID, log_id, "precompute_mod")
        assert (failed.status, failed.error) == ("failed", "boom")
        assert await module_jobs(log_id, "precompute_mod") == []

        # ...and a committed filter leaves it alone (it is not in the working set).
        resp = await c.put(
            "/api/v1/event-logs/" + log_id + "/active-filter",
            json={"filter": [{"field": "activity", "op": "equals", "value": "register order"}]},
        )
        assert resp.status_code == 200, resp.text
        await asyncio.sleep(0.4)
        assert await module_jobs(log_id, "precompute_mod") == []

        # An explicit retry does run it.
        [retry] = await coordinator.ensure(TEST_USER_ID, log_id, "precompute_mod", force=True)
        assert retry.job_id is not None

        async def _ready() -> bool:
            return await state(log_id, "precompute_mod") == "ready"

        await wait_until(_ready, what="the retried run to finish")


# --------------------------------------------------------------------------- #
# Data changes
# --------------------------------------------------------------------------- #


@pytest.mark.asyncio
async def test_filter_commit_refreshes_only_modules_already_used(tmp_path: Path) -> None:
    from mate.api.modules.cache import ResultCache

    async with mods_client(tmp_path, "precompute_mod", "eager_mod") as c:
        log_id = await import_sample(c)

        async def _eager_ready() -> bool:
            return await state(log_id, "eager_mod") == "ready"

        await wait_until(_eager_ready, what="the eager run to finish")
        revision_before = await data_revision(log_id)
        sentinel = ResultCache(log_id, "eager_mod", TEST_USER_ID).dir / "left-over.json"
        sentinel.write_text("{}")

        resp = await c.put(
            f"/api/v1/event-logs/{log_id}/active-filter",
            json={"filter": [{"field": "activity", "op": "equals", "value": "register order"}]},
        )
        assert resp.status_code == 200, resp.text
        assert resp.json()["modules_retriggered"] is True
        assert await data_revision(log_id) == revision_before + 1

        # The used module recomputes against the new data, from an emptied cache...
        async def _refreshed() -> bool:
            row = await run_row(log_id, "eager_mod")
            return (
                row is not None
                and row.status == "ready"
                and row.data_revision == revision_before + 1
            )

        await wait_until(_refreshed, what="the used module to refresh")
        assert len(await module_jobs(log_id, "eager_mod")) == 2
        assert not sentinel.exists()

        # ...and the module nobody opened stays lazy.
        assert await module_jobs(log_id, "precompute_mod") == []
        assert await state(log_id, "precompute_mod") == "idle"


@pytest.mark.asyncio
async def test_cell_edit_makes_runs_stale_then_refreshes_after_the_debounce(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from mate.api.modules import processing

    monkeypatch.setattr(processing, "REFRESH_DELAY_S", 0.6)
    async with mods_client(tmp_path, "precompute_mod") as c:
        log_id = await import_sample(c)
        await coordinator_or_fail().ensure(TEST_USER_ID, log_id, "precompute_mod")

        async def _ready() -> bool:
            return await state(log_id, "precompute_mod") == "ready"

        await wait_until(_ready, what="the first run to finish")
        revision_before = await data_revision(log_id)

        resp = await c.patch(
            f"/api/v1/event-logs/{log_id}/events/0", json={"field": "resource", "value": "carol"}
        )
        assert resp.status_code == 200, resp.text
        assert await data_revision(log_id) == revision_before + 1
        # Immediately after the edit the result is known to be out of date...
        assert await state(log_id, "precompute_mod") == "stale"

        # ...and once the edits go quiet the used module is refreshed on its own.
        async def _refreshed() -> bool:
            row = await run_row(log_id, "precompute_mod")
            return (
                row is not None
                and row.status == "ready"
                and row.data_revision == revision_before + 1
            )

        await wait_until(_refreshed, what="the debounced refresh")
        assert len(await module_jobs(log_id, "precompute_mod")) == 2


# --------------------------------------------------------------------------- #
# Job completion + restart
# --------------------------------------------------------------------------- #


@pytest.mark.asyncio
async def test_terminal_event_of_a_superseded_job_is_ignored(tmp_path: Path) -> None:
    from mate.api.db.engine import get_sessionmaker
    from mate.api.db.models import Job
    from mate.api.uuid7 import uuid7_str

    async with mods_client(tmp_path, "precompute_mod") as c:
        log_id = await import_sample(c)
        await coordinator_or_fail().ensure(TEST_USER_ID, log_id, "precompute_mod")

        async def _ready() -> bool:
            return await state(log_id, "precompute_mod") == "ready"

        await wait_until(_ready, what="the run to finish")
        current = (await run_row(log_id, "precompute_mod")).job_id

        # A job the row no longer points at (it was superseded) reports a failure late.
        stale_id = uuid7_str()
        async with get_sessionmaker()() as session:
            session.add(
                Job(
                    id=stale_id,
                    user_id=TEST_USER_ID,
                    type="module.precompute_mod.event.log_imported",
                    title="old",
                    module_id="precompute_mod",
                    payload_json={"log_id": log_id},
                    status="failed",
                    error="late",
                )
            )
            await session.commit()
        await coordinator_or_fail().on_terminal_job({"id": stale_id})

        row = await run_row(log_id, "precompute_mod")
        assert (row.status, row.job_id, row.error) == ("ready", current, None)


@pytest.mark.asyncio
async def test_reconcile_boot_restarts_a_run_whose_job_was_lost(tmp_path: Path) -> None:
    from mate.api.uuid7 import uuid7_str

    async with mods_client(tmp_path, "precompute_mod") as c:
        log_id = await import_sample(c)
        lost = uuid7_str()  # a queued job the crash took with the in-memory queue
        await set_run(log_id, "precompute_mod", status="queued", job_id=lost)

        await coordinator_or_fail().reconcile_boot()

        row = await run_row(log_id, "precompute_mod")
        assert row.job_id not in (None, lost)

        async def _ready() -> bool:
            return await state(log_id, "precompute_mod") == "ready"

        await wait_until(_ready, what="the restarted run to finish")


@pytest.mark.asyncio
async def test_reconcile_boot_takes_the_outcome_of_a_job_that_finished_while_down(
    tmp_path: Path,
) -> None:
    from mate.api.db.engine import get_sessionmaker
    from mate.api.db.models import Job
    from mate.api.uuid7 import uuid7_str

    async with mods_client(tmp_path, "precompute_mod") as c:
        log_id = await import_sample(c)
        done = uuid7_str()
        async with get_sessionmaker()() as session:
            session.add(
                Job(
                    id=done,
                    user_id=TEST_USER_ID,
                    type="module.precompute_mod.event.log_imported",
                    title="finished offline",
                    module_id="precompute_mod",
                    payload_json={"log_id": log_id},
                    status="completed",
                )
            )
            await session.commit()
        await set_run(log_id, "precompute_mod", status="running", job_id=done)

        await coordinator_or_fail().reconcile_boot()

        row = await run_row(log_id, "precompute_mod")
        assert (row.status, row.job_id) == ("ready", done)
        assert len(await module_jobs(log_id, "precompute_mod")) == 1  # nothing resubmitted


# --------------------------------------------------------------------------- #
# Events
# --------------------------------------------------------------------------- #


@pytest.mark.asyncio
async def test_run_events_carry_the_owner_and_follow_the_run(tmp_path: Path) -> None:
    from mate.api.events import get_event_bus

    async with mods_client(tmp_path, "precompute_mod") as c:
        log_id = await import_sample(c)
        seen: list[dict[str, Any]] = []
        completed: list[dict[str, Any]] = []

        async def _collect() -> None:
            async with get_event_bus().subscribe(
                ["module_run.updated", "precompute_mod.completed"]
            ) as stream:
                ready.set()
                async for env in stream:
                    if env.payload.get("log_id") != log_id:
                        continue
                    if env.topic == "precompute_mod.completed":
                        completed.append(env.payload)
                    else:
                        seen.append(env.payload)
                    if completed and seen and seen[-1]["status"] == "ready":
                        return

        ready = asyncio.Event()
        collector = asyncio.create_task(_collect())
        await ready.wait()
        await coordinator_or_fail().ensure(TEST_USER_ID, log_id, "precompute_mod")
        await asyncio.wait_for(collector, timeout=20)

        # Every user-scoped event names its owner, or the SSE fan-out would leak it.
        assert all(p["user_id"] == TEST_USER_ID for p in [*seen, *completed])
        assert [p["status"] for p in seen] == ["queued", "ready"]
        assert seen[0]["job_id"] == seen[1]["job_id"]
        assert completed[0]["log_id"] == log_id


# --------------------------------------------------------------------------- #
# HTTP surface
# --------------------------------------------------------------------------- #


@pytest.mark.asyncio
async def test_module_runs_routes(tmp_path: Path) -> None:
    async with mods_client(tmp_path, "precompute_mod") as c:
        log_id = await import_sample(c)

        listed = (await c.get(f"/api/v1/event-logs/{log_id}/module-runs")).json()
        assert {r["module_id"]: r["status"] for r in listed}["precompute_mod"] == "idle"

        started = await c.post(f"/api/v1/event-logs/{log_id}/module-runs/precompute_mod")
        assert started.status_code == 200, started.text
        [run] = started.json()
        assert (run["module_id"], run["status"]) == ("precompute_mod", "queued")
        assert run["job_id"]

        # Idempotent: a panel can call it on every open.
        again = await c.post(f"/api/v1/event-logs/{log_id}/module-runs/precompute_mod")
        assert again.json()[0]["job_id"] == run["job_id"]

        unknown = await c.post(f"/api/v1/event-logs/{log_id}/module-runs/nope")
        assert unknown.status_code == 404

        # A module with no precompute anywhere in its closure has nothing to wait for.
        # (`modules` lists it with an empty `precompute_deps`.)
        summaries = {m["id"]: m for m in (await c.get(f"/api/v1/modules?log_id={log_id}")).json()}
        assert summaries["precompute_mod"]["precompute_deps"] == ["precompute_mod"]


@pytest.mark.asyncio
async def test_module_runs_refuses_a_log_that_is_still_importing(tmp_path: Path) -> None:
    from mate.api.db.engine import get_sessionmaker
    from mate.api.db.models import EventLog
    from mate.api.uuid7 import uuid7_str

    async with mods_client(tmp_path, "precompute_mod") as c:
        log_id = uuid7_str()
        async with get_sessionmaker()() as session:
            session.add(
                EventLog(
                    id=log_id,
                    user_id=TEST_USER_ID,
                    name="still importing",
                    source_format="csv",
                    log_model="case_centric",
                    status="importing",
                )
            )
            await session.commit()
        resp = await c.post(f"/api/v1/event-logs/{log_id}/module-runs/precompute_mod")
        assert resp.status_code == 409


# --------------------------------------------------------------------------- #
# MCP: a read computes what is missing, and says which job to wait on
# --------------------------------------------------------------------------- #


def _mcp_ctx() -> Any:
    from types import SimpleNamespace

    from mate.api.auth.dependencies import CurrentUser
    from mate.api.mcp.auth import MCPPrincipal
    from mate.api.mcp.scopes import ALL_SCOPES

    user = CurrentUser(id=TEST_USER_ID, email=None, preferred_username=None, name=None, roles=())
    principal = MCPPrincipal(user=user, token_id="tok", scopes=ALL_SCOPES, auth_type="pat")
    request = SimpleNamespace(scope={"mate_principal": principal})
    return SimpleNamespace(request_context=SimpleNamespace(request=request))


async def _grant_mcp_egress_consent() -> None:
    from mate.api.db.engine import get_sessionmaker
    from mate.api.db.models import UserSetting
    from mate.api.mcp.consent import MCP_EGRESS_CONSENT_KEY

    async with get_sessionmaker()() as session:
        row = await session.get(UserSetting, (TEST_USER_ID, MCP_EGRESS_CONSENT_KEY))
        if row is None:
            session.add(
                UserSetting(user_id=TEST_USER_ID, key=MCP_EGRESS_CONSENT_KEY, value_json=True)
            )
        else:
            row.value_json = True
        await session.commit()


@pytest.mark.asyncio
async def test_mcp_read_of_an_uncomputed_module_starts_it_and_names_the_job(
    tmp_path: Path,
) -> None:
    from mate.api.mcp.errors import MCPToolError
    from mate.api.mcp.toolsets import analysis

    async with mods_client(tmp_path, "precompute_mod", "eager_mod") as c:
        await _grant_mcp_egress_consent()
        log_id = await import_sample(c)
        ctx = _mcp_ctx()

        # The lazy module has not run: the read starts it and points at the job.
        with pytest.raises(MCPToolError) as err:
            await analysis.get_module_results(ctx, log_id=log_id, module_id="precompute_mod")
        message = str(err.value)
        assert "conflict" in message and "computing" in message and "wait_for_job('" in message
        [job] = await module_jobs(log_id, "precompute_mod")
        assert job.id in message

        # Once computed the same read goes through (the eager module cached a result).
        async def _eager_ready() -> bool:
            return await state(log_id, "eager_mod") == "ready"

        await wait_until(_eager_ready, what="the eager module")
        out = await analysis.get_module_results(ctx, log_id=log_id, module_id="eager_mod")
        assert out["results"]["done"] == {"ok": True}

        # `list_modules` reports where each module stands for the process.
        listed = {m["module_id"]: m for m in await analysis.list_modules(ctx, log_id=log_id)}
        assert listed["eager_mod"]["run_status"] == "ready"
        assert listed["precompute_mod"]["run_status"] in ("queued", "running", "ready")


@pytest.mark.asyncio
async def test_mcp_overview_does_not_start_modules(tmp_path: Path) -> None:
    from mate.api.mcp.toolsets import analysis

    async with mods_client(tmp_path, "precompute_mod") as c:
        await _grant_mcp_egress_consent()
        log_id = await import_sample(c)

        await analysis.get_process_overview(_mcp_ctx(), log_id=log_id)

        await asyncio.sleep(0.3)
        assert await module_jobs(log_id, "precompute_mod") == []  # an overview is read-only
