"""Per-log module parameters (`ui.scope: dataset`) and precompute-config staleness
(`ui.affects: precompute`).

Drives the `log_config_mod` fixture: `min_support` (dataset + precompute), `zoom`
(dataset + view), `solver_budget` (module + compute), `label` (module) and a model
store keyed `model`.
"""

from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import pytest
from httpx import AsyncClient

from .conftest import TEST_USER_ID
from .test_module_processing import (
    coordinator_or_fail,
    import_sample,
    mods_client,
    module_jobs,
    run_row,
    set_run,
    state,
    wait_until,
)

MOD = "log_config_mod"
FIXTURES = Path(__file__).parent / "fixtures" / "modules"
OTHER_ID = "00000000-0000-7000-8000-0000000000b1"


def _now() -> datetime:
    return datetime.now(UTC).replace(tzinfo=None)


def _url(log_id: str, module_id: str = MOD) -> str:
    return f"/api/v1/event-logs/{log_id}/modules/{module_id}/config"


async def _module_config(c: AsyncClient, config: dict[str, Any]) -> None:
    """Replace the test user's module-wide config (the DB outlives each test)."""
    resp = await c.put(f"/api/v1/modules/{MOD}/config", json={"config": config, "enabled": True})
    assert resp.status_code == 200, resp.text


async def _overrides(c: AsyncClient, log_id: str, overrides: dict[str, Any]) -> dict[str, Any]:
    resp = await c.put(_url(log_id), json={"overrides": overrides})
    assert resp.status_code == 200, resp.text
    return resp.json()


async def _echo(c: AsyncClient, log_id: str | None = None) -> dict[str, Any]:
    params = {"log_id": log_id} if log_id else {}
    resp = await c.get(f"/api/v1/modules/{MOD}/echo-config", params=params)
    assert resp.status_code == 200, resp.text
    return resp.json()["config"]


async def _seed_user(uid: str) -> None:
    from mate.api.db.engine import get_sessionmaker
    from mate.api.db.models import User

    async with get_sessionmaker()() as s:
        if await s.get(User, uid) is None:
            s.add(
                User(
                    id=uid,
                    email=f"{uid}@mate.local",
                    preferred_username=uid[:8],
                    name=uid[:8],
                    created_at=_now(),
                    last_seen_at=_now(),
                )
            )
            await s.commit()


async def _ready_run(log_id: str) -> None:
    await coordinator_or_fail().ensure(TEST_USER_ID, log_id, MOD)

    async def _ready() -> bool:
        return await state(log_id, MOD) == "ready"

    await wait_until(_ready, what="the run to finish")


# --------------------------------------------------------------------------- #
# Pure helpers
# --------------------------------------------------------------------------- #


def test_fingerprint_covers_only_precompute_config() -> None:
    from mate.api.modules.log_config import (
        dataset_properties,
        precompute_keys,
        run_fingerprint,
    )
    from mate.sdk.manifest import Manifest

    m = Manifest.load_yaml(FIXTURES / MOD / "manifest.yaml")
    assert set(dataset_properties(m)) == {"min_support", "zoom"}
    assert precompute_keys(m) == ["min_support", "model"]

    base = run_fingerprint("h", m, {})
    assert base != "h"
    assert run_fingerprint("h", m, {"min_support": 0.5}) == base  # the schema default
    assert run_fingerprint("h", m, {"zoom": 9, "solver_budget": 1, "label": "x"}) == base
    assert run_fingerprint("h", m, {"min_support": 1}) == run_fingerprint(
        "h", m, {"min_support": 1.0}
    )
    assert run_fingerprint("h", m, {"min_support": True}) != run_fingerprint(
        "h", m, {"min_support": 1.0}
    )
    assert run_fingerprint("h", m, {"min_support": 0.9}) != base
    assert run_fingerprint("h", m, {"model": "m2"}) != base
    assert run_fingerprint("other", m, {}) != base

    # Without precompute config the fingerprint is the compute hash, as before.
    plain = Manifest.load_yaml(FIXTURES / "precompute_mod" / "manifest.yaml")
    assert run_fingerprint("h", plain, {"anything": 1}) == "h"


def test_a_viewer_takes_only_result_config_from_the_owner() -> None:
    """`owner_keys`: the result-determining keys and the model selection - never a
    connection setting, ``ai`` or a secret, however deeply nested."""
    from mate.api.modules.log_config import owner_keys
    from mate.sdk.manifest import Manifest

    m = Manifest.load_yaml(FIXTURES / MOD / "manifest.yaml")
    assert owner_keys(m) == {"min_support", "zoom", "solver_budget", "model"}
    repo = Path(__file__).resolve().parents[3] / "modules"
    # The viewer's password must never go to the owner's bolt_uri as the owner's username.
    assert owner_keys(Manifest.load_yaml(repo / "actor_performance" / "manifest.yaml")) == set()
    assert owner_keys(Manifest.load_yaml(repo / "cv4cdd" / "manifest.yaml")) == {
        "n_windows",
        "confidence_threshold",
        "model",
    }

    compute = {"affects": "compute"}
    props = {
        "plain": {"type": "object", "ui": compute, "properties": {"url": {"type": "string"}}},
        "nested": {
            "type": "object",
            "ui": compute,
            "properties": {"key": {"type": "string", "ui": {"widget": "secret"}}},
        },
        "listed": {"ui": compute, "items": {"properties": {"pw": {"format": "password"}}}},
        "mapped": {"ui": compute, "additionalProperties": {"writeOnly": True}},
        "either": {"ui": compute, "anyOf": [{"type": "null"}, {"secret": True}]},
        "ai": {"type": "object", "ui": compute},
    }
    schema = {"type": "object", "properties": props}
    probe = Manifest.model_validate(
        {**m.model_dump(), "config_schema": schema, "model_store": None}
    )
    assert owner_keys(probe) == {"plain"}


# --------------------------------------------------------------------------- #
# API
# --------------------------------------------------------------------------- #


@pytest.mark.asyncio
async def test_get_put_replace_semantics_and_ctx_config(tmp_path: Path) -> None:
    async with mods_client(tmp_path, MOD) as c:
        await _module_config(c, {})
        log_id = await import_sample(c)

        body = (await c.get(_url(log_id))).json()
        assert set(body["schema"]["properties"]) == {"min_support", "zoom"}
        assert body["overrides"] == {}
        assert body["module_values"] == {"min_support": 0.5, "zoom": 1}
        assert body["effective"] == body["module_values"]

        await _module_config(c, {"min_support": 0.7, "label": "wide"})
        assert (await c.get(_url(log_id))).json()["module_values"]["min_support"] == 0.7

        body = await _overrides(c, log_id, {"min_support": 0.9, "zoom": 2})
        assert body["overrides"] == {"min_support": 0.9, "zoom": 2}
        assert body["effective"] == {"min_support": 0.9, "zoom": 2}
        assert body["module_values"] == {"min_support": 0.7, "zoom": 1}

        # ctx.config: module config -> per-log overrides; no schema defaults merged in.
        assert await _echo(c, log_id) == {"min_support": 0.9, "zoom": 2, "label": "wide"}
        assert await _echo(c) == {"min_support": 0.7, "label": "wide"}

        # PUT replaces the whole object: what is left out inherits again.
        body = await _overrides(c, log_id, {"zoom": 3})
        assert body["overrides"] == {"zoom": 3}
        assert body["effective"] == {"min_support": 0.7, "zoom": 3}
        body = await _overrides(c, log_id, {"zoom": None})  # null inherits too
        assert body["overrides"] == {}
        assert (await c.get(_url(log_id))).json()["overrides"] == {}

        # Only `scope: dataset` keys may be overridden per log.
        for key in ("solver_budget", "label", "model", "nope"):
            resp = await c.put(_url(log_id), json={"overrides": {key: 1}})
            assert resp.status_code == 422, (key, resp.text)
        assert (await c.get(_url(log_id))).json()["overrides"] == {}


@pytest.mark.asyncio
async def test_stored_non_dataset_keys_never_reach_ctx_config(tmp_path: Path) -> None:
    from mate.api.db.engine import get_sessionmaker
    from mate.api.db.models import LogModuleConfig

    async with mods_client(tmp_path, MOD) as c:
        await _module_config(c, {"label": "wide"})
        log_id = await import_sample(c)
        async with get_sessionmaker()() as s:
            s.add(
                LogModuleConfig(
                    user_id=TEST_USER_ID,
                    log_id=log_id,
                    module_id=MOD,
                    config_json={"label": "sneaky", "min_support": 0.3},
                )
            )
            await s.commit()

        assert await _echo(c, log_id) == {"label": "wide", "min_support": 0.3}
        assert (await c.get(_url(log_id))).json()["overrides"] == {"min_support": 0.3}


@pytest.mark.asyncio
async def test_ownership_is_404(tmp_path: Path) -> None:
    from sqlalchemy import delete

    from mate.api.db.engine import get_sessionmaker
    from mate.api.db.models import EventLog, ModuleInstall
    from mate.api.uuid7 import uuid7_str

    async with mods_client(tmp_path, MOD) as c:
        log_id = await import_sample(c)
        await _seed_user(OTHER_ID)
        foreign = uuid7_str()
        async with get_sessionmaker()() as s:
            s.add(EventLog(id=foreign, user_id=OTHER_ID, name="theirs", created_at=_now()))
            await s.commit()

        for url in (_url(foreign), _url(log_id, "no_such_module"), _url("missing")):
            assert (await c.get(url)).status_code == 404, url
            assert (await c.put(url, json={"overrides": {}})).status_code == 404, url

        # A loaded module the user has not installed is invisible too.
        async with get_sessionmaker()() as s:
            await s.execute(
                delete(ModuleInstall).where(
                    ModuleInstall.user_id == TEST_USER_ID, ModuleInstall.module_id == MOD
                )
            )
            await s.commit()
        assert (await c.get(_url(log_id))).status_code == 404
        put = await c.put(_url(log_id), json={"overrides": {"zoom": 2}})
        assert put.status_code == 404


# --------------------------------------------------------------------------- #
# Merge order
# --------------------------------------------------------------------------- #


@pytest.mark.asyncio
async def test_admin_lock_beats_per_log_override(tmp_path: Path) -> None:
    async with mods_client(tmp_path, MOD, admin=True) as c:
        await _module_config(c, {})
        log_id = await import_sample(c)
        await _overrides(c, log_id, {"min_support": 0.9, "zoom": 2})

        lock = f"/api/v1/admin/controls/items/card/{MOD}:config"
        resp = await c.put(
            lock, json={"control_mode": "admin", "admin_value": {"min_support": 0.2}}
        )
        assert resp.status_code == 200, resp.text
        try:
            cfg = await _echo(c, log_id)
            assert cfg["min_support"] == 0.2  # the lock wins
            assert cfg["zoom"] == 2  # a key the lock does not set keeps the override
            body = (await c.get(_url(log_id))).json()
            assert body["overrides"]["min_support"] == 0.9
            assert body["effective"]["min_support"] == 0.2
            assert body["module_values"]["min_support"] == 0.2
            assert body["locked"] == ["min_support", "zoom"]
        finally:
            resp = await c.put(lock, json={"control_mode": "user"})
            assert resp.status_code == 200, resp.text


@pytest.mark.asyncio
async def test_admin_lock_makes_per_log_keys_read_only(tmp_path: Path) -> None:
    async with mods_client(tmp_path, MOD, admin=True) as c:
        await _module_config(c, {})
        log_id = await import_sample(c)
        assert (await _overrides(c, log_id, {"min_support": 0.9}))["locked"] == []

        lock = f"/api/v1/admin/controls/items/card/{MOD}:config"
        resp = await c.put(
            lock, json={"control_mode": "admin", "admin_value": {"min_support": 0.2}}
        )
        assert resp.status_code == 200, resp.text
        try:
            # The config card owns every schema key, not only those its value sets.
            assert (await c.get(_url(log_id))).json()["locked"] == ["min_support", "zoom"]
            # Like the module-wide PUT: a stored override survives (even a null), a
            # new one on a locked key is dropped.
            body = await _overrides(c, log_id, {"min_support": None, "zoom": 5})
            assert body["overrides"] == {"min_support": 0.9}
            assert body["effective"] == {"min_support": 0.2, "zoom": 1}
            assert await _echo(c, log_id) == {"min_support": 0.2}
        finally:
            resp = await c.put(lock, json={"control_mode": "user"})
            assert resp.status_code == 200, resp.text

        body = await _overrides(c, log_id, {"zoom": 5})
        assert body["overrides"] == {"zoom": 5}
        assert body["locked"] == []


@pytest.mark.asyncio
async def test_shared_dashboard_viewer_computes_with_the_owner_overrides(tmp_path: Path) -> None:
    from mate.api.db.engine import get_sessionmaker
    from mate.api.db.models import Dashboard, DashboardShare, LogModuleConfig
    from mate.api.modules import get_module_loader
    from mate.api.uuid7 import uuid7_str

    async with mods_client(tmp_path, MOD) as c:
        log_id = await import_sample(c)
        await _overrides(c, log_id, {"min_support": 0.9})
        await _seed_user(OTHER_ID)
        dash_id = uuid7_str()
        async with get_sessionmaker()() as s:
            s.add(
                Dashboard(
                    id=dash_id,
                    user_id=TEST_USER_ID,
                    name="bound",
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
                    target_user_id=OTHER_ID,
                    created_by=TEST_USER_ID,
                    created_at=_now(),
                )
            )
            # The viewer's own row for the log is never consulted.
            s.add(
                LogModuleConfig(
                    user_id=OTHER_ID, log_id=log_id, module_id=MOD, config_json={"zoom": 7}
                )
            )
            await s.commit()

        ctx = await get_module_loader()._make_context(MOD, log_id, OTHER_ID)
        assert ctx.config.get("min_support") == 0.9
        assert ctx.config.get("zoom") is None


@pytest.mark.asyncio
async def test_shared_dashboard_viewer_takes_the_owner_result_config(tmp_path: Path) -> None:
    """A viewer's results land in the owner's cache, so every result-determining key
    (`owner_keys`) is the owner's (unset there = the default); unflagged settings,
    secrets, ``ai`` and undeclared keys stay the viewer's."""
    from mate.api.db.engine import get_sessionmaker
    from mate.api.db.models import ModuleConfig
    from mate.api.modules import get_module_loader

    from .test_shared_read_only import VIEWER_ID, _share_with_viewer

    viewer = VIEWER_ID

    async with mods_client(tmp_path, MOD) as c:
        log_id = await import_sample(c)
        await _seed_user(viewer)
        await _share_with_viewer(log_id)
        async with get_sessionmaker()() as s:
            for uid, cfg in (
                (
                    TEST_USER_ID,
                    {
                        "label": "owner",
                        "solver_budget": 3,
                        "model": "owner-model",
                        "api_key": "owner-key",
                        "ai": {"m": "o"},
                    },
                ),
                (
                    viewer,
                    {
                        "label": "mine",
                        "solver_budget": 99,
                        "min_support": 0.1,
                        "model": "viewer-model",
                        "api_key": "viewer-key",
                        "ai": {"m": "v"},
                        "extra": 1,
                    },
                ),
            ):
                await s.merge(ModuleConfig(user_id=uid, module_id=MOD, config_json=cfg))
            await s.commit()
        try:
            ctx = await get_module_loader()._make_context(MOD, log_id, viewer)
            assert ctx.config.value == {
                "label": "mine",
                "solver_budget": 3,
                "model": "owner-model",
                "api_key": "viewer-key",
                "ai": {"m": "v"},
                "extra": 1,
            }
        finally:
            await _module_config(c, {})


# --------------------------------------------------------------------------- #
# Run staleness
# --------------------------------------------------------------------------- #


@pytest.mark.asyncio
async def test_precompute_config_change_makes_the_run_stale(tmp_path: Path) -> None:
    from mate.api.modules.cache import ResultCache

    async with mods_client(tmp_path, MOD) as c:
        await _module_config(c, {})
        log_id = await import_sample(c)
        await _ready_run(log_id)

        # View, compute and module-only properties do not touch the stored run...
        await _overrides(c, log_id, {"zoom": 3})
        await _module_config(c, {"solver_budget": 99, "label": "x"})
        assert await state(log_id, MOD) == "ready"
        # ...nor does writing out the schema default the run already used.
        await _module_config(c, {"min_support": 0.5, "solver_budget": 99})
        assert await state(log_id, MOD) == "ready"

        # A per-log precompute override does.
        await _overrides(c, log_id, {"zoom": 3, "min_support": 1})
        assert await state(log_id, MOD) == "stale"
        await _ready_run(log_id)
        assert len(await module_jobs(log_id, MOD)) == 2
        cached = await ResultCache(log_id, MOD, TEST_USER_ID).get("config")
        assert cached["min_support"] == 1

        await _overrides(c, log_id, {"zoom": 3, "min_support": 1.0})
        assert await state(log_id, MOD) == "ready"

        # So does the module-wide value of a precompute property the log inherits...
        await _overrides(c, log_id, {})
        assert await state(log_id, MOD) == "stale"
        await _ready_run(log_id)
        await _module_config(c, {"min_support": 0.6})
        assert await state(log_id, MOD) == "stale"
        await _ready_run(log_id)

        # ...and the model-store selection.
        await _module_config(c, {"min_support": 0.6, "model": "m2"})
        assert await state(log_id, MOD) == "stale"


@pytest.mark.asyncio
async def test_pre_0017_ready_run_is_stamped_once_then_goes_stale(tmp_path: Path) -> None:
    async with mods_client(tmp_path, MOD) as c:
        await _module_config(c, {})
        log_id = await import_sample(c)
        await _ready_run(log_id)
        current = (await run_row(log_id, MOD)).fingerprint
        assert current is not None

        # A ready row from before 0017 (NULL fingerprint) gets the current one on its
        # first read - through ensure and through the run list alike - and no new run.
        await set_run(log_id, MOD, status="ready", fingerprint=None)
        [same] = await coordinator_or_fail().ensure(TEST_USER_ID, log_id, MOD)
        assert same.status == "ready"
        assert (await run_row(log_id, MOD)).fingerprint == current
        await set_run(log_id, MOD, status="ready", fingerprint=None)
        assert await state(log_id, MOD) == "ready"
        assert (await run_row(log_id, MOD)).fingerprint == current
        assert len(await module_jobs(log_id, MOD)) == 1

        # Boot stamps it too, before anyone can change a setting.
        await set_run(log_id, MOD, status="ready", fingerprint=None)
        await coordinator_or_fail().reconcile_boot()
        assert (await run_row(log_id, MOD)).fingerprint == current

        # So a later precompute-config change is detected.
        await _overrides(c, log_id, {"min_support": 0.9})
        assert await state(log_id, MOD) == "stale"


@pytest.mark.asyncio
async def test_config_change_while_queued_resubmits(tmp_path: Path) -> None:
    from mate.api.db.engine import get_sessionmaker
    from mate.api.db.models import Job
    from mate.api.uuid7 import uuid7_str

    async with mods_client(tmp_path, MOD) as c:
        await _module_config(c, {})
        log_id = await import_sample(c)
        await _ready_run(log_id)

        # A run in flight for the current data and config (its job never starts).
        queued = uuid7_str()
        async with get_sessionmaker()() as session:
            session.add(
                Job(
                    id=queued,
                    user_id=TEST_USER_ID,
                    type=f"module.{MOD}.event.log_imported",
                    title="queued",
                    module_id=MOD,
                    payload_json={"log_id": log_id},
                    status="queued",
                )
            )
            await session.commit()
        await set_run(log_id, MOD, status="queued", job_id=queued)
        coordinator = coordinator_or_fail()
        [same] = await coordinator.ensure(TEST_USER_ID, log_id, MOD)
        assert same.job_id == queued  # up to date: left alone

        await _overrides(c, log_id, {"min_support": 0.8})
        [fresh] = await coordinator.ensure(TEST_USER_ID, log_id, MOD)
        assert fresh.job_id not in (None, queued)
        async with get_sessionmaker()() as session:
            old = await session.get(Job, queued)
            assert old is not None and old.status == "cancelled"

        async def _ready() -> bool:
            return await state(log_id, MOD) == "ready"

        await wait_until(_ready, what="the resubmitted run to finish")
        row = await run_row(log_id, MOD)
        assert row.job_id == fresh.job_id
