"""Run variants: extra, parameterised runs of a module's precompute for one log
(`mate.api.modules.variants`), stored as `_r_<key>/` in the module's result cache.

Fixtures: `log_config_mod` (per-log `min_support`, caches its `ctx.config`) and the
`artifact_producer` -> `artifact_consumer` pair (a required `petri_net` input).
"""

from __future__ import annotations

import asyncio
import contextlib
import json
import os
import shutil
import threading
import time
from collections.abc import AsyncIterator
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import pytest
from httpx import AsyncClient
from structlog.testing import capture_logs

from mate.api.modules import variants as variants_mod
from mate.api.modules.cache import ResultCache, is_run_variant, run_variant_key
from mate.api.modules.variants import RunSpec, VariantRunner
from mate.api.storage import sync as storage_sync

from .conftest import TEST_USER_ID
from .test_log_module_config import OTHER_ID, _module_config, _seed_user
from .test_module_processing import (
    coordinator_or_fail,
    import_sample,
    mods_client,
    module_jobs,
    run_row,
    state,
    wait_until,
)

MOD = "log_config_mod"
PAIR = ("artifact_producer", "artifact_consumer")
_ONLY_TWO = [{"field": "activity", "op": "in", "value": ["ship", "cancel"]}]


def _url(log_id: str, module_id: str, variant: str = "") -> str:
    tail = f"/{variant}" if variant else ""
    return f"/api/v1/event-logs/{log_id}/module-runs/{module_id}/variants{tail}"


async def _post(c: AsyncClient, log_id: str, module_id: str, **body: Any) -> dict[str, Any]:
    resp = await c.post(_url(log_id, module_id), json=body)
    assert resp.status_code in (200, 202), resp.text
    return resp.json()


async def _settled(c: AsyncClient, log_id: str, module_id: str, variant: str) -> dict[str, Any]:
    out: dict[str, Any] = {}

    async def _done() -> bool:
        nonlocal out
        out = (await c.get(_url(log_id, module_id, variant))).json()
        return out["status"] not in ("queued", "running")

    await wait_until(_done, what=f"variant {variant} to settle")
    return out


async def _ready(c: AsyncClient, log_id: str, module_id: str, **body: Any) -> dict[str, Any]:
    variant = (await _post(c, log_id, module_id, **body))["variant"]
    out = await _settled(c, log_id, module_id, variant)
    assert out["status"] == "ready", out
    return out


def _cache(log_id: str, module_id: str, variant: str | None = None) -> ResultCache:
    return ResultCache(log_id, module_id, TEST_USER_ID, run_variant=variant)


async def _parked(job_id: str) -> None:
    """Wait until `pause_queue` has parked the job."""
    from mate.api.modules import get_module_loader

    runtime = get_module_loader().runtime

    async def _check() -> bool:
        return job_id in runtime._deferred.get(TEST_USER_ID, [])  # type: ignore[attr-defined]

    await wait_until(_check, what="the job to be parked")


async def _forget_job(job_id: str) -> None:
    """What a restart does to a job parked by `pause_queue`: its row stays `queued` but
    no runtime holds it any more."""
    from mate.api.modules import get_module_loader

    runtime = get_module_loader().runtime
    await _parked(job_id)
    runtime._deferred[TEST_USER_ID].remove(job_id)  # type: ignore[attr-defined]
    runtime._live.discard(job_id)  # type: ignore[attr-defined]


@contextlib.asynccontextmanager
async def _variant_events(log_id: str) -> AsyncIterator[list[dict[str, Any]]]:
    """The `module_variant.updated` payloads published for *log_id* while open."""
    from mate.api.events import get_event_bus

    out: list[dict[str, Any]] = []
    subscribed = asyncio.Event()

    async def _collect() -> None:
        async with get_event_bus().subscribe(["module_variant.updated"]) as stream:
            subscribed.set()
            async for env in stream:
                if env.payload["log_id"] == log_id:
                    out.append(env.payload)

    task = asyncio.create_task(_collect())
    await subscribed.wait()
    try:
        yield out
    finally:
        task.cancel()


async def _gc_everything() -> None:
    """Run the variant GC with a ~0 cap (its lock map is all it needs of the runner)."""
    from mate.api.config import get_settings

    settings = get_settings()
    prev = settings.run_variants_max_bytes_per_user
    settings.run_variants_max_bytes_per_user = 1
    try:
        runner = VariantRunner(None, None, None, None)  # type: ignore[arg-type]
        await runner._gc(TEST_USER_ID, set())  # type: ignore[attr-defined]
    finally:
        settings.run_variants_max_bytes_per_user = prev


class _FakeS3:
    """In-memory boto3 client: just the calls `mate.api.storage.s3` makes."""

    def __init__(self) -> None:
        self.objects: dict[str, bytes] = {}
        self.uploads: list[str] = []

    def get_paginator(self, _name: str) -> Any:
        objects = self.objects

        class _Pages:
            def paginate(self, Bucket: str, Prefix: str) -> Any:  # noqa: N803 - boto3 kwargs
                contents = [
                    {"Key": k, "Size": len(v)}
                    for k, v in sorted(objects.items())
                    if k.startswith(Prefix)
                ]
                yield {"Contents": contents}

        return _Pages()

    def list_objects_v2(self, Bucket: str, Prefix: str, MaxKeys: int) -> dict[str, Any]:  # noqa: N803
        keys = sorted(k for k in self.objects if k.startswith(Prefix))[:MaxKeys]
        return {"KeyCount": len(keys), "Contents": [{"Key": k} for k in keys]}

    def upload_file(self, src: str, _bucket: str, key: str) -> None:
        self.objects[key] = Path(src).read_bytes()
        self.uploads.append(key)

    def download_file(self, _bucket: str, key: str, dest: str) -> None:
        Path(dest).write_bytes(self.objects[key])

    def delete_objects(self, Bucket: str, Delete: dict[str, Any]) -> None:  # noqa: N803
        for obj in Delete["Objects"]:
            self.objects.pop(obj["Key"], None)


def _fake_s3(monkeypatch: pytest.MonkeyPatch) -> _FakeS3:
    """Switch the storage sync layer to S3 mode, backed by an in-memory bucket."""
    from mate.api.storage import s3
    from mate.api.storage.config import StorageSettings

    client = _FakeS3()
    settings = StorageSettings(mode="s3", endpoint_url="https://s3", bucket="b")
    monkeypatch.setattr(storage_sync, "is_s3", lambda: True)
    monkeypatch.setattr(storage_sync, "get_storage_settings", lambda: settings)
    monkeypatch.setattr(storage_sync, "_hydrated", set())
    monkeypatch.setattr(s3, "get_storage_settings", lambda: settings)
    monkeypatch.setattr(s3, "make_client", lambda *a, **k: client)
    return client


# --------------------------------------------------------------------------- #
# Pure
# --------------------------------------------------------------------------- #


def test_variant_key_is_stable_and_canonical() -> None:
    a = RunSpec(params={"a": 1, "b": 2}, inputs={"p": "l/m/k"})
    b = RunSpec.model_validate({"inputs": {"p": "l/m/k"}, "params": {"b": 2, "a": 1}})
    key = run_variant_key(a.canonical())
    assert key == run_variant_key(b.canonical())
    assert is_run_variant(key)
    # Pinned: the key names directories on disk, so it must not drift across releases.
    assert run_variant_key(RunSpec().canonical()) == run_variant_key(
        {"filter": None, "inputs": {}, "params": {}}
    )
    assert key != run_variant_key(RunSpec(params={"a": 2, "b": 2}).canonical())
    assert key != run_variant_key(RunSpec(params=a.params, filter=[]).canonical())
    for bad in ("", "..", "../x", "ABCDEF0123456789", "0123456789abcde", "0123456789abcdef0"):
        assert not is_run_variant(bad), bad
    with pytest.raises(ValueError):
        ResultCache("log", "mod", TEST_USER_ID, run_variant="../../escape")
    with pytest.raises(ValueError):  # an unknown op is not a filter
        RunSpec(filter=[{"field": "activity", "op": "drop table"}])


async def test_default_clear_keeps_run_variants(tmp_path: Path) -> None:
    key = run_variant_key(RunSpec().canonical())
    default = ResultCache("log", "mod", TEST_USER_ID, root=tmp_path)
    await default.set("out", {"x": 1})
    await default.set("__ai_guidance", {"g": 1})
    await ResultCache("log", "mod", TEST_USER_ID, root=tmp_path, variant="f1").set("out", 1)
    run = ResultCache("log", "mod", TEST_USER_ID, root=tmp_path, run_variant=key)
    await run.set("out", {"x": 2})

    default.clear(keep_reserved=True)
    assert sorted(p.name for p in default.dir.iterdir()) == ["__ai_guidance.json", f"_r_{key}"]
    assert await run.get("out") == {"x": 2}

    default.clear()  # a full wipe takes everything but the run variants
    assert [p.name for p in default.dir.iterdir()] == [f"_r_{key}"]
    run.remove()  # a variant goes only through its own namespace
    assert list(default.dir.iterdir()) == []


def test_null_params_inherit_and_numbers_key_alike() -> None:
    # `null` inherits like the per-log PUT: the same variant as leaving the key out.
    assert RunSpec(params={"min_support": None}).canonical() == RunSpec().canonical()
    one, one_f = RunSpec(params={"a": 1}), RunSpec(params={"a": 1.0})
    assert run_variant_key(one.canonical()) == run_variant_key(one_f.canonical())
    assert one.canonical()["params"] == {"a": 1}  # the job still gets the int
    assert run_variant_key(RunSpec(params={"a": True}).canonical()) != run_variant_key(
        one.canonical()
    )

    def key(value: Any) -> str:
        return run_variant_key(RunSpec(params={"a": value}).canonical())

    assert key([1, {"b": 2}]) == key([1.0, {"b": 2.0}])
    # Ints stay exact: no float rounding above 2**53, no OverflowError on huge ones.
    assert key(2**53) != key(2**53 + 1)
    assert key(10**400) != key(10**400 + 1)
    assert key(0.5) != key(1)


def test_touch_never_races_remove(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    from mate.api.modules import cache as cache_mod

    key = run_variant_key(RunSpec(params={"t": 1}).canonical())
    run = ResultCache("log", "mod", TEST_USER_ID, root=tmp_path, run_variant=key)
    stamp = {"last_used_at": "2000-01-01T00:00:00"}

    # remove/clear wait for a touch's read-modify-write of `__meta.json`.
    for op in (run.clear, run.remove):
        run.write_meta(stamp)
        with cache_mod._meta_lock:  # type: ignore[attr-defined]
            worker = threading.Thread(target=op)
            worker.start()
            worker.join(0.2)
            assert worker.is_alive() and (run.dir / "__meta.json").exists()
        worker.join(5)
    assert not run.dir.exists()

    # A dir deleted between the touch's read and write (an eviction) stays deleted.
    run.write_meta(stamp)
    real_read = Path.read_text

    def _read_then_evict(self: Path, *a: Any, **k: Any) -> str:
        text = real_read(self, *a, **k)
        shutil.rmtree(run.dir)
        return text

    monkeypatch.setattr(cache_mod, "_touched", {})
    monkeypatch.setattr(Path, "read_text", _read_then_evict)
    run.touch()
    monkeypatch.undo()
    assert not run.dir.exists()


# --------------------------------------------------------------------------- #
# Lifecycle
# --------------------------------------------------------------------------- #


@pytest.mark.asyncio
async def test_variants_coexist_and_leave_the_default_run_alone(tmp_path: Path) -> None:
    from mate.api.events import get_event_bus

    async with mods_client(tmp_path, MOD) as c:
        await _module_config(c, {})  # the DB outlives each test
        log_id = await import_sample(c)
        for bad in ({"solver_budget": 1}, {"label": "x"}, {"nope": 1}, {"nope": 10**400}):
            resp = await c.post(_url(log_id, MOD), json={"params": bad})
            assert resp.status_code == 422, resp.text
        resp = await c.post(_url(log_id, MOD), json={"params": {}, "extra": 1})
        assert resp.status_code == 422  # unknown spec keys are rejected too

        events: list[dict[str, Any]] = []

        async def _collect() -> None:
            async with get_event_bus().subscribe(["module_variant.updated"]) as stream:
                subscribed.set()
                async for env in stream:
                    events.append(env.payload)

        subscribed = asyncio.Event()
        collector = asyncio.create_task(_collect())
        await subscribed.wait()
        try:
            first = await c.post(_url(log_id, MOD), json={"params": {"min_support": 0.1}})
            assert first.status_code == 202, first.text
            a = await _settled(c, log_id, MOD, first.json()["variant"])
            b = await _ready(c, log_id, MOD, params={"min_support": 0.9, "zoom": 3})
        finally:
            collector.cancel()
        assert a["status"] == "ready" and a["variant"] != b["variant"]
        assert a["spec"] == {"params": {"min_support": 0.1}, "inputs": {}, "filter": None}
        assert a["refs"] == []  # log_config_mod has no typed outputs
        assert (await _cache(log_id, MOD, a["variant"]).get("config"))["min_support"] == 0.1
        cfg_b = await _cache(log_id, MOD, b["variant"]).get("config")
        assert (cfg_b["min_support"], cfg_b["zoom"]) == (0.9, 3)
        # Tenant invariant: every variant event names its owner.
        mine = [e for e in events if e["log_id"] == log_id]
        assert mine and all(e["user_id"] == TEST_USER_ID for e in mine)
        assert {e["variant"] for e in mine} == {a["variant"], b["variant"]}

        # The default run never ran: no row, nothing in the default cache.
        assert await run_row(log_id, MOD) is None
        assert await _cache(log_id, MOD).get("config") is None

        listed = (await c.get(_url(log_id, MOD))).json()
        assert sorted(v["variant"] for v in listed) == sorted([a["variant"], b["variant"]])

        # Idempotent: asking again starts nothing.
        again = await c.post(_url(log_id, MOD), json={"params": {"min_support": 0.1}})
        assert again.status_code == 200
        assert again.json()["job_id"] == a["job_id"]
        assert len(await module_jobs(log_id, MOD)) == 2

        # The default run computes and reruns (clearing its cache) around the variants.
        await coordinator_or_fail().ensure(TEST_USER_ID, log_id, MOD)

        async def _default_ready() -> bool:
            return await state(log_id, MOD) == "ready"

        await wait_until(_default_ready, what="the default run")
        resp = await c.put(
            f"/api/v1/event-logs/{log_id}/modules/{MOD}/config",
            json={"overrides": {"min_support": 0.6}},
        )
        assert resp.status_code == 200
        assert await state(log_id, MOD) == "stale"
        await coordinator_or_fail().ensure(TEST_USER_ID, log_id, MOD)
        await wait_until(_default_ready, what="the default rerun")
        assert (await _cache(log_id, MOD).get("config"))["min_support"] == 0.6
        # The variants survived the rerun's cache clear, and the spec's param beats the
        # per-log override, so the override change did not stale them.
        for v in (a, b):
            got = (await c.get(_url(log_id, MOD, v["variant"]))).json()
            assert got["status"] == "ready", got
        assert (await _cache(log_id, MOD, a["variant"]).get("config"))["min_support"] == 0.1


@pytest.mark.asyncio
async def test_data_change_makes_a_variant_stale(tmp_path: Path) -> None:
    from mate.api.db.engine import get_sessionmaker
    from mate.api.db.models import EventLog

    async with mods_client(tmp_path, MOD) as c:
        log_id = await import_sample(c)
        v = await _ready(c, log_id, MOD, params={"min_support": 0.2})
        async with get_sessionmaker()() as session:
            row = await session.get(EventLog, log_id)
            assert row is not None
            row.data_revision = int(row.data_revision or 0) + 1
            await session.commit()
        assert (await c.get(_url(log_id, MOD, v["variant"]))).json()["status"] == "stale"
        resp = await c.post(_url(log_id, MOD), json={"params": {"min_support": 0.2}})
        assert resp.status_code == 202
        assert resp.json()["job_id"] != v["job_id"]
        assert (await _settled(c, log_id, MOD, v["variant"]))["status"] == "ready"


@pytest.mark.asyncio
async def test_a_variant_job_lost_in_a_restart_is_resubmitted(tmp_path: Path) -> None:
    from mate.api.modules import get_module_loader

    async with mods_client(tmp_path, MOD) as c:
        log_id = await import_sample(c)
        runtime = get_module_loader().runtime
        await runtime.pause_queue(TEST_USER_ID)
        try:
            at_boot = await _post(c, log_id, MOD, params={"min_support": 0.41})
            forced = await _post(c, log_id, MOD, params={"min_support": 0.42})
            for v in (at_boot, forced):
                await _forget_job(v["job_id"])
            # Without force an in-flight variant is left alone, even one with a dead job...
            again = await _post(c, log_id, MOD, params={"min_support": 0.42})
            assert (again["status"], again["job_id"]) == ("queued", forced["job_id"])
            # ...force resubmits a variant whose job no runtime holds.
            retried = await _post(c, log_id, MOD, params={"min_support": 0.42}, force=True)
            assert retried["status"] == "queued" and retried["job_id"] != forced["job_id"]
            # Boot recovery resubmits the other, failing its lost job as interrupted.
            await coordinator_or_fail().reconcile_boot()
            resumed = (await c.get(_url(log_id, MOD, at_boot["variant"]))).json()
            assert resumed["status"] == "queued" and resumed["job_id"] != at_boot["job_id"]
        finally:
            await runtime.resume_queue(TEST_USER_ID)
        jobs = {j.id: j for j in await module_jobs(log_id, MOD)}
        lost = jobs[at_boot["job_id"]]
        assert (lost.status, lost.error) == ("failed", "Interrupted by a restart.")
        assert jobs[forced["job_id"]].status == "cancelled"
        for v in (at_boot, forced):
            assert (await _settled(c, log_id, MOD, v["variant"]))["status"] == "ready"


@pytest.mark.asyncio
async def test_a_read_that_settles_a_variant_publishes_it_once(tmp_path: Path) -> None:
    from sqlalchemy import update

    from mate.api.db.engine import get_sessionmaker
    from mate.api.db.models import Job
    from mate.api.events import get_event_bus
    from mate.api.modules import get_module_loader

    async with mods_client(tmp_path, MOD) as c:
        log_id = await import_sample(c)
        runtime = get_module_loader().runtime
        statuses: list[str] = []
        subscribed = asyncio.Event()

        async def _collect() -> None:
            async with get_event_bus().subscribe(["module_variant.updated"]) as stream:
                subscribed.set()
                async for env in stream:
                    if env.payload["log_id"] == log_id:
                        statuses.append(env.payload["status"])

        collector = asyncio.create_task(_collect())
        await subscribed.wait()
        await runtime.pause_queue(TEST_USER_ID)
        try:
            v = await _post(c, log_id, MOD, params={"min_support": 0.51})
            await _forget_job(v["job_id"])  # only this test finishes the job
            async with get_sessionmaker()() as session:
                await session.execute(
                    update(Job).where(Job.id == v["job_id"]).values(status="failed", error="boom")
                )
                await session.commit()
            # A read sees the job first: it writes the terminal state - and publishes it.
            got = (await c.get(_url(log_id, MOD, v["variant"]))).json()
            assert (got["status"], got["error"]) == ("failed", "boom")
            # The terminal-job hook arriving after it publishes nothing more.
            await coordinator_or_fail().variants.settle(
                v["job_id"], TEST_USER_ID, log_id, MOD, got["spec"]
            )

            async def _two() -> bool:
                return len(statuses) >= 2

            await wait_until(_two, what="the variant's events")
            await asyncio.sleep(0.1)
        finally:
            await runtime.resume_queue(TEST_USER_ID)
            collector.cancel()
        assert statuses == ["queued", "failed"]


@pytest.mark.asyncio
async def test_a_variant_killed_mid_run_restarts_like_the_default_run(tmp_path: Path) -> None:
    from sqlalchemy import update

    from mate.api.db.engine import get_sessionmaker
    from mate.api.db.models import Job
    from mate.api.modules import get_module_loader

    async with mods_client(tmp_path / "boot1", MOD) as c:
        log_id = await import_sample(c, name="killed mid-run")
        await get_module_loader().runtime.pause_queue(TEST_USER_ID)
        v = await _post(c, log_id, MOD, params={"min_support": 0.62})
        await _parked(v["job_id"])
    # What a crash mid-run leaves: the next boot's runtime fails it as worker-lost
    # before the variants' boot hook runs.
    async with get_sessionmaker()() as session:
        await session.execute(
            update(Job)
            .where(Job.id == v["job_id"])
            .values(status="running", started_at=datetime.now(UTC).replace(tzinfo=None))
        )
        await session.commit()

    async with mods_client(tmp_path / "boot2", MOD) as c:
        got = await _settled(c, log_id, MOD, v["variant"])
    assert got["status"] == "ready" and got["job_id"] != v["job_id"], got


@pytest.mark.asyncio
async def test_boot_recovery_publishes_only_the_resubmission(tmp_path: Path) -> None:
    from mate.api.modules import get_module_loader

    async with mods_client(tmp_path, MOD) as c:
        log_id = await import_sample(c, name="boot events")
        runtime = get_module_loader().runtime
        async with _variant_events(log_id) as events:
            await runtime.pause_queue(TEST_USER_ID)
            try:
                v = await _post(c, log_id, MOD, params={"min_support": 0.73})
                await _forget_job(v["job_id"])
                await coordinator_or_fail().reconcile_boot()
            finally:
                await runtime.resume_queue(TEST_USER_ID)
            await _settled(c, log_id, MOD, v["variant"])
            await asyncio.sleep(0.2)
        # The lost run's transient `failed` never reaches clients.
        assert [e["status"] for e in events] == ["queued", "queued", "ready"]


@pytest.mark.asyncio
async def test_a_submission_cut_short_never_stays_queued(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from mate.api.modules import get_module_loader

    async with mods_client(tmp_path, MOD) as c:
        log_id = await import_sample(c, name="cut short")
        # A crash between the `queued` meta and the submit: in flight until boot
        # recovery resubmits it.
        spec = RunSpec(params={"min_support": 0.64})
        key = run_variant_key(spec.canonical())
        _cache(log_id, MOD, key).write_meta(
            {"spec": spec.canonical(), "status": "queued", "job_id": None}
        )
        assert (await c.get(_url(log_id, MOD, key))).json()["status"] == "queued"
        await coordinator_or_fail().reconcile_boot()
        done = await _settled(c, log_id, MOD, key)
        assert done["status"] == "ready" and done["job_id"], done

        # A submit that raises fails the variant instead.
        async def _boom(*_a: Any, **_k: Any) -> str:
            raise RuntimeError("queue down")

        monkeypatch.setattr(get_module_loader(), "submit_precompute", _boom)
        other = RunSpec(params={"min_support": 0.65})
        with pytest.raises(RuntimeError, match="queue down"):
            await coordinator_or_fail().variants.ensure(TEST_USER_ID, log_id, MOD, other)
        got = (await c.get(_url(log_id, MOD, run_variant_key(other.canonical())))).json()
        assert (got["status"], got["error"]) == ("failed", "queue down")


@pytest.mark.asyncio
async def test_an_input_check_that_raises_never_leaves_it_queued(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """`inputs.missing()` (S3 hydrate, mkdir, touch - disk I/O) raising after the
    `queued` meta is written fails the variant like a submit that raises."""
    from mate.api.modules.inputs import ModuleInputs

    async with mods_client(tmp_path, MOD) as c:
        log_id = await import_sample(c, name="input check raises")
        spec = RunSpec(params={"min_support": 0.41})

        def _disk_full(_self: ModuleInputs) -> list[str]:
            raise OSError(28, "No space left on device")

        monkeypatch.setattr(ModuleInputs, "missing", _disk_full)
        with pytest.raises(OSError):
            await coordinator_or_fail().variants.ensure(TEST_USER_ID, log_id, MOD, spec)
        got = (await c.get(_url(log_id, MOD, run_variant_key(spec.canonical())))).json()
        assert (got["status"], got["job_id"]) == ("failed", None), got
        assert "No space left on device" in got["error"]


@pytest.mark.asyncio
async def test_boot_recovery_that_cannot_resubmit_publishes_the_failure(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The lost run is settled unpublished, expecting a resubmission - when none
    happens (the module is unavailable for the log now), clients hear `failed`."""
    from mate.api.modules import get_module_loader
    from mate.api.modules.availability import Availability

    async with mods_client(tmp_path, MOD) as c:
        log_id = await import_sample(c, name="boot no resubmit")
        loader = get_module_loader()
        real = loader.availability_for

        def _unavailable(**kw: Any) -> Any:
            return {**real(**kw), MOD: Availability("unavailable", ["gone"])}

        await loader.runtime.pause_queue(TEST_USER_ID)
        try:
            v = await _post(c, log_id, MOD, params={"min_support": 0.77})
            await _forget_job(v["job_id"])
            monkeypatch.setattr(loader, "availability_for", _unavailable)
            async with _variant_events(log_id) as events:
                await coordinator_or_fail().reconcile_boot()
                await asyncio.sleep(0.2)
        finally:
            await loader.runtime.resume_queue(TEST_USER_ID)
        got = (await c.get(_url(log_id, MOD, v["variant"]))).json()
        assert got["status"] == "failed", got
        assert [e["status"] for e in events] == ["failed"], events


# --------------------------------------------------------------------------- #
# Inputs
# --------------------------------------------------------------------------- #


@pytest.mark.asyncio
async def test_consumer_variant_reads_a_producer_variant_by_ref(tmp_path: Path) -> None:
    from mate.api.mcp.toolsets import analysis

    from .test_mcp_analysis import _ctx, _principal, _seed_log

    async with mods_client(tmp_path, *PAIR) as c:
        log_id = await import_sample(c)
        prod = await _ready(c, log_id, "artifact_producer", filter=_ONLY_TWO)
        pv = prod["variant"]
        [ref] = prod["refs"]
        assert ref == {
            "dataset_id": "model",
            "artifact_type": "petri_net@1",
            "ref": f"{log_id}/artifact_producer@{pv}/model",
        }
        cons = await _ready(c, log_id, "artifact_consumer", inputs={"reference_model": ref["ref"]})
        assert await _cache(log_id, "artifact_consumer", cons["variant"]).get("seen") == {
            "ports": ["reference_model"],
            "ref": ref["ref"],
            "places": 2,  # the producer variant only saw the two filtered activities
        }
        # Nothing ran a default run for either module.
        assert await run_row(log_id, "artifact_producer") is None
        assert await run_row(log_id, "artifact_consumer") is None

        # The dataset route and MCP serve the variant's artifact.
        url = f"/api/v1/datasets/artifact_producer/model?log_id={log_id}"
        env = (await c.get(f"{url}&variant={pv}")).json()
        assert [n["kind"] for n in env["data"]["nodes"]].count("place") == 2
        assert (await c.get(f"{url}&variant=..")).status_code == 404
        await _seed_log(log_id)  # MCP egress consent
        listed = await analysis.list_artifacts(_ctx(_principal()), log_id=log_id)
        assert [(e["ref"], e["variant"]) for e in listed] == [(ref["ref"], pv)]
        got = await analysis.get_artifact(_ctx(_principal()), ref=ref["ref"])
        assert len(got["artifact"]["places"]) == 2

        # A read stamps the producer variant's `last_used_at` (the GC's LRU key).
        meta = _cache(log_id, "artifact_producer", pv).read_meta()
        assert meta is not None and meta["last_used_at"] > meta["requested_at"]


@pytest.mark.asyncio
async def test_missing_required_input_fails_without_a_job(tmp_path: Path) -> None:
    async with mods_client(tmp_path, *PAIR) as c:
        log_id = await import_sample(c)
        out = await _post(c, log_id, "artifact_consumer")
        assert out["status"] == "failed"
        assert out["job_id"] is None
        assert out["error"] == "input 'reference_model' not produced"
        assert await module_jobs(log_id, "artifact_consumer") == []
        assert await module_jobs(log_id, "artifact_producer") == []

        # Once the producer has produced, a forced retry runs it on the default binding.
        await coordinator_or_fail().ensure(TEST_USER_ID, log_id, "artifact_producer")

        async def _produced() -> bool:
            return await state(log_id, "artifact_producer") == "ready"

        await wait_until(_produced, what="the producer")
        resp = await c.post(_url(log_id, "artifact_consumer"), json={})
        assert resp.json()["status"] == "failed"  # settled; no force, no rerun
        resp = await c.post(_url(log_id, "artifact_consumer"), json={"force": True})
        assert resp.status_code == 202
        done = await _settled(c, log_id, "artifact_consumer", out["variant"])
        assert done["status"] == "ready"
        seen = await _cache(log_id, "artifact_consumer", out["variant"]).get("seen")
        assert seen["ref"] == f"{log_id}/artifact_producer/model"


@pytest.mark.asyncio
async def test_an_explicit_ref_on_an_optional_port_must_be_produced(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A variant runs with its spec or not at all: an explicitly bound artifact that was
    never produced fails it, at request and at job start, required port or not."""
    from mate.api.modules import get_module_loader

    async with mods_client(tmp_path, *PAIR) as c:
        log_id = await import_sample(c, name="optional explicit")
        loader = get_module_loader()
        monkeypatch.setattr(
            loader.loaded["artifact_consumer"].manifest.inputs[0], "required", False
        )
        never = run_variant_key({"params": {"never": 1}})
        ref = f"{log_id}/artifact_producer@{never}/model"
        out = await _post(c, log_id, "artifact_consumer", inputs={"reference_model": ref})
        assert (out["status"], out["job_id"]) == ("failed", None), out
        assert out["error"] == "input 'reference_model' not produced"
        spec = {"params": {}, "inputs": {"reference_model": ref}, "filter": None}
        with pytest.raises(RuntimeError, match="'reference_model' not produced"):
            await loader._make_context("artifact_consumer", log_id, TEST_USER_ID, run_spec=spec)


@pytest.mark.asyncio
async def test_refs_and_variant_keys_are_checked(tmp_path: Path) -> None:
    from mate.api.db.engine import get_sessionmaker
    from mate.api.db.models import EventLog

    async with mods_client(tmp_path, *PAIR) as c:
        log_id = await import_sample(c)
        other_log = await import_sample(c, name="second")
        await _seed_user(OTHER_ID)
        foreign = f"p3b-foreign-{log_id[:8]}"
        async with get_sessionmaker()() as session:
            session.add(
                EventLog(
                    id=foreign,
                    user_id=OTHER_ID,
                    name="theirs",
                    status="ready",
                    created_at=datetime.now(UTC).replace(tzinfo=None),
                )
            )
            await session.commit()
        # The other user's producer output sits where a naive resolver would look.
        await ResultCache(foreign, "artifact_producer", OTHER_ID).set("model", {"places": []})

        for inputs in (
            {"reference_model": f"{foreign}/artifact_producer/model"},  # someone else's log
            {"reference_model": f"{log_id}/artifact_producer@../model"},  # traversal
            {"reference_model": f"{log_id}/artifact_producer@ABCDEF0123456789/model"},
            {"reference_model": f"{log_id}/artifact_producer/../model"},
            {"reference_model": f"{log_id}/artifact_producer/other_key"},
            {"reference_model": f"{log_id}/artifact_consumer/seen"},  # not a petri_net output
            {"nope": f"{log_id}/artifact_producer/model"},  # no such port
        ):
            resp = await c.post(_url(log_id, "artifact_consumer"), json={"inputs": inputs})
            assert resp.status_code == 422, (inputs, resp.text)

        for bad in ("%2e%2e", "..x", "ABCDEF0123456789", "0" * 15):
            assert (await c.get(_url(log_id, "artifact_consumer", bad))).status_code == 404
            assert (await c.delete(_url(log_id, "artifact_consumer", bad))).status_code == 404
        # Someone else's log, or a module the user doesn't have: 404 like module-runs.
        assert (await c.get(_url(foreign, "artifact_consumer"))).status_code == 404
        assert (await c.post(_url(log_id, "ghost_mod"), json={})).status_code == 404

        # Another log the user owns is fine.
        await coordinator_or_fail().ensure(TEST_USER_ID, other_log, "artifact_producer")

        async def _produced() -> bool:
            return await state(other_log, "artifact_producer") == "ready"

        await wait_until(_produced, what="the producer on the other log")
        cross = f"{other_log}/artifact_producer/model"
        v = await _ready(c, log_id, "artifact_consumer", inputs={"reference_model": cross})
        seen = await _cache(log_id, "artifact_consumer", v["variant"]).get("seen")
        assert seen == {"ports": ["reference_model"], "ref": cross, "places": 4}


@pytest.mark.asyncio
async def test_a_ref_that_stops_resolving_fails_the_variant(tmp_path: Path) -> None:
    from mate.api.db.engine import get_sessionmaker
    from mate.api.db.models import EventLog
    from mate.api.modules import get_module_loader

    async with mods_client(tmp_path, *PAIR) as c:
        log_id = await import_sample(c)
        other_log = await import_sample(c, name="deleted later")
        await coordinator_or_fail().ensure(TEST_USER_ID, other_log, "artifact_producer")

        async def _produced() -> bool:
            return await state(other_log, "artifact_producer") == "ready"

        await wait_until(_produced, what="the producer on the other log")
        runtime = get_module_loader().runtime
        await runtime.pause_queue(TEST_USER_ID)
        try:
            cross = {"reference_model": f"{other_log}/artifact_producer/model"}
            v = await _post(c, log_id, "artifact_consumer", inputs=cross)
            assert v["status"] == "queued"
            async with get_sessionmaker()() as session:
                row = await session.get(EventLog, other_log)
                assert row is not None
                row.deleted_at = datetime.now(UTC).replace(tzinfo=None)
                await session.commit()
        finally:
            await runtime.resume_queue(TEST_USER_ID)
        # Never quietly run on the default binding (an unproduced default here).
        done = await _settled(c, log_id, "artifact_consumer", v["variant"])
        assert done["status"] == "failed", done
        assert "Cannot apply the run spec" in done["error"] and "reference_model" in done["error"]
        assert await _cache(log_id, "artifact_consumer", v["variant"]).get("seen") is None
        # A plain (non-variant) context stays lenient.
        ctx = await get_module_loader()._make_context("artifact_consumer", log_id, TEST_USER_ID)
        assert str(ctx.inputs.ref("reference_model")) == f"{log_id}/artifact_producer/model"


@pytest.mark.asyncio
async def test_an_input_gone_by_job_start_fails_the_variant(tmp_path: Path) -> None:
    from mate.api.modules import get_module_loader

    async with mods_client(tmp_path, *PAIR) as c:
        log_id = await import_sample(c, name="input deleted")
        prod = await _ready(c, log_id, "artifact_producer", filter=_ONLY_TWO)
        runtime = get_module_loader().runtime
        await runtime.pause_queue(TEST_USER_ID)
        try:
            inputs = {"reference_model": prod["refs"][0]["ref"]}
            cons = await _post(c, log_id, "artifact_consumer", inputs=inputs)
            assert cons["status"] == "queued"
            # The ref still resolves (log + module owned) but its variant is gone.
            resp = await c.delete(_url(log_id, "artifact_producer", prod["variant"]))
            assert resp.status_code == 204
        finally:
            await runtime.resume_queue(TEST_USER_ID)
        done = await _settled(c, log_id, "artifact_consumer", cons["variant"])
        assert done["status"] == "failed", done
        assert done["error"] == "Cannot apply the run spec: input 'reference_model' not produced"
        assert await _cache(log_id, "artifact_consumer", cons["variant"]).get("seen") is None


# --------------------------------------------------------------------------- #
# DELETE + GC
# --------------------------------------------------------------------------- #


@pytest.mark.asyncio
async def test_delete_cancels_the_job_and_removes_the_variant(tmp_path: Path) -> None:
    from mate.api.modules import get_module_loader

    async with mods_client(tmp_path, MOD) as c:
        log_id = await import_sample(c)
        runtime = get_module_loader().runtime
        await runtime.pause_queue(TEST_USER_ID)  # keep the job queued
        try:
            out = await _post(c, log_id, MOD, params={"min_support": 0.3})
            assert out["status"] == "queued" and out["job_id"]
            assert _cache(log_id, MOD, out["variant"]).dir.is_dir()
            resp = await c.delete(_url(log_id, MOD, out["variant"]))
            assert resp.status_code == 204
        finally:
            await runtime.resume_queue(TEST_USER_ID)
        [job] = await module_jobs(log_id, MOD)
        assert job.status == "cancelled"
        assert not _cache(log_id, MOD, out["variant"]).dir.exists()
        assert (await c.get(_url(log_id, MOD, out["variant"]))).status_code == 404
        assert (await c.delete(_url(log_id, MOD, out["variant"]))).status_code == 404


@pytest.mark.asyncio
async def test_gc_evicts_the_least_recently_used_variants(tmp_path: Path) -> None:
    from mate.api.config import get_settings

    async with mods_client(tmp_path, MOD) as c:
        log_id = await import_sample(c)
        old = await _ready(c, log_id, MOD, params={"min_support": 0.11})
        recent = await _ready(c, log_id, MOD, params={"min_support": 0.12})
        # Make `old` the least recently used, whatever the clock did.
        for v, stamp in ((old, "2000-01-01T00:00:00"), (recent, "2100-01-01T00:00:00")):
            meta_path = _cache(log_id, MOD, v["variant"]).dir / "__meta.json"
            meta = json.loads(meta_path.read_text())
            meta["last_used_at"] = stamp
            meta_path.write_text(json.dumps(meta))
        settings = get_settings()
        sizes = [
            sum(f.stat().st_size for f in _cache(log_id, MOD, v["variant"]).dir.iterdir())
            for v in (old, recent)
        ]
        prev = settings.run_variants_max_bytes_per_user
        # Room for one more variant than `recent` alone: `old` has to go.
        settings.run_variants_max_bytes_per_user = sizes[1] + 1
        try:
            with capture_logs() as logs:
                newest = await _ready(c, log_id, MOD, params={"min_support": 0.13})
        finally:
            settings.run_variants_max_bytes_per_user = prev
        assert not _cache(log_id, MOD, old["variant"]).dir.exists()
        assert _cache(log_id, MOD, recent["variant"]).dir.is_dir()
        assert newest["status"] == "ready"
        # Earlier tests' variants (same user, older stamps than `recent`) may go too.
        evicted = {e["variant"]: e for e in logs if e["event"] == "modules.variants.evicted"}
        assert old["variant"] in evicted and recent["variant"] not in evicted
        assert evicted[old["variant"]]["user_id"] == TEST_USER_ID


@pytest.mark.asyncio
async def test_gc_keeps_the_producer_variants_in_flight_consumers_read(tmp_path: Path) -> None:
    from mate.api.config import get_settings
    from mate.api.modules import get_module_loader

    async with mods_client(tmp_path, *PAIR) as c:
        log_id = await import_sample(c)
        prod = await _ready(c, log_id, "artifact_producer", filter=_ONLY_TWO)
        producer = _cache(log_id, "artifact_producer", prod["variant"])
        runtime = get_module_loader().runtime
        settings = get_settings()
        prev = settings.run_variants_max_bytes_per_user
        await runtime.pause_queue(TEST_USER_ID)
        try:
            inputs = {"reference_model": prod["refs"][0]["ref"]}
            cons = await _post(c, log_id, "artifact_consumer", inputs=inputs)
            assert cons["status"] == "queued"
            # The producer variant is the least recently used, and the cap is ~0...
            meta = json.loads((producer.dir / "__meta.json").read_text())
            meta["last_used_at"] = "2000-01-01T00:00:00"
            (producer.dir / "__meta.json").write_text(json.dumps(meta))
            settings.run_variants_max_bytes_per_user = 1
            # ...but the queued consumer reads it, so another request's GC keeps it.
            other = await _post(c, log_id, "artifact_producer")
        finally:
            settings.run_variants_max_bytes_per_user = prev
            await runtime.resume_queue(TEST_USER_ID)
        assert (producer.dir / "model.json").exists()
        done = await _settled(c, log_id, "artifact_consumer", cons["variant"])
        assert done["status"] == "ready", done
        await _settled(c, log_id, "artifact_producer", other["variant"])
        seen = await _cache(log_id, "artifact_consumer", cons["variant"]).get("seen")
        assert seen["places"] == 2


@pytest.mark.asyncio
async def test_gc_keeps_a_producer_while_its_consumer_is_mid_submit(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The consumer has passed its own GC and input check but has no job yet when
    another request's GC runs."""
    from mate.api.config import get_settings
    from mate.api.modules import get_module_loader

    async with mods_client(tmp_path, *PAIR) as c:
        log_id = await import_sample(c, name="gc mid-submit")
        prod = await _ready(c, log_id, "artifact_producer", filter=_ONLY_TWO)
        producer = _cache(log_id, "artifact_producer", prod["variant"])
        loader = get_module_loader()
        real_submit = loader.submit_precompute
        entered, gate = asyncio.Event(), asyncio.Event()

        async def _slow_submit(module_id: str, *a: Any, **k: Any) -> str:
            if module_id == "artifact_consumer":
                entered.set()
                await gate.wait()
            return await real_submit(module_id, *a, **k)

        monkeypatch.setattr(loader, "submit_precompute", _slow_submit)
        settings = get_settings()
        prev = settings.run_variants_max_bytes_per_user
        settings.run_variants_max_bytes_per_user = 1
        try:
            inputs = {"reference_model": prod["refs"][0]["ref"]}
            pending = asyncio.create_task(_post(c, log_id, "artifact_consumer", inputs=inputs))
            await asyncio.wait_for(entered.wait(), 10)
            ship_only = [{"field": "activity", "op": "in", "value": ["ship"]}]
            await _post(c, log_id, "artifact_producer", filter=ship_only)
            assert (producer.dir / "model.json").exists()
        finally:
            gate.set()
            settings.run_variants_max_bytes_per_user = prev
        cons = await pending
        done = await _settled(c, log_id, "artifact_consumer", cons["variant"])
        assert done["status"] == "ready", done


@pytest.mark.asyncio
async def test_gc_never_drops_an_in_flight_consumer_caught_mid_write() -> None:
    """Every `write_meta` makes and renames a `.tmp` beside the meta: a scan that lists
    it and sizes it after the rename must not skip the whole dir - the in-flight
    consumer would then no longer keep the producer variant it reads."""
    from mate.api.config import get_settings

    user = "p3b-scan-user"
    root = get_settings().module_results_dir_for(user)
    shutil.rmtree(root, ignore_errors=True)

    def _variant(module_id: str, seed: int) -> ResultCache:
        key = run_variant_key({"params": {"seed": seed}})
        return ResultCache("scan-log", module_id, user, run_variant=key)

    producer, consumer = _variant("artifact_producer", 1), _variant("artifact_consumer", 2)
    ref = f"scan-log/artifact_producer@{producer.run_variant}/model"
    spec = {"params": {}, "inputs": {"reference_model": ref}, "filter": None}
    c_meta = {"status": "running", "job_id": "j", "spec": spec, "pad": "x" * 2000}
    consumer.write_meta(c_meta)
    stop = threading.Event()

    def _writer() -> None:  # the consumer's reads and settles rewriting its meta
        while not stop.is_set():
            consumer.write_meta(c_meta)

    settings = get_settings()
    prev = settings.run_variants_max_bytes_per_user
    settings.run_variants_max_bytes_per_user = 1
    writer = threading.Thread(target=_writer)
    writer.start()
    misses = evicted = 0
    try:
        runner = VariantRunner(None, None, None, None)  # type: ignore[arg-type]
        for _ in range(1500):
            scanned = {d for d, _s, _m in variants_mod._scan(root)}  # type: ignore[attr-defined]
            misses += consumer.dir not in scanned
            if not producer.dir.exists():
                await producer.set("model", {"places": [1, 2]})
                producer.write_meta({"status": "ready", "spec": {}, "last_used_at": "2000"})
            await runner._gc(user, set())  # type: ignore[attr-defined]
            evicted += not producer.dir.exists()
    finally:
        stop.set()
        writer.join()
        settings.run_variants_max_bytes_per_user = prev
        shutil.rmtree(root, ignore_errors=True)
    assert (misses, evicted) == (0, 0)


@pytest.mark.asyncio
async def test_gc_rereads_the_meta_under_the_lock(monkeypatch: pytest.MonkeyPatch) -> None:
    """The lock-free scan caught `__meta.json` mid-write: the variant is no orphan."""
    key = run_variant_key(RunSpec(params={"torn": 1}).canonical())
    run = _cache("p3b-torn", MOD, key)
    await run.set("out", {"v": 1})
    run.write_meta({"status": "queued", "spec": {}, "job_id": "j"})
    os.utime(run.dir, (time.time() - 3600,) * 2)
    meta_path = run.dir / "__meta.json"
    real_scan = variants_mod._scan  # type: ignore[attr-defined]

    def _torn_scan(root: Path) -> Any:
        good = meta_path.read_text()
        meta_path.write_text("")  # what a non-atomic writer leaves for a moment
        try:
            return real_scan(root)
        finally:
            meta_path.write_text(good)

    monkeypatch.setattr(variants_mod, "_scan", _torn_scan)
    await _gc_everything()
    assert run.dir.is_dir()
    shutil.rmtree(run.dir.parent.parent)

    # And meta writes are atomic: a reader never sees one half written.
    stop = threading.Event()
    meta = {"status": "ready", "spec": {}, "pad": "x" * 4000}
    run.write_meta(meta)

    def _writer() -> None:
        while not stop.is_set():
            run.write_meta(meta)

    writer = threading.Thread(target=_writer)
    writer.start()
    try:
        deadline = time.time() + 0.5
        while time.time() < deadline:
            assert variants_mod._local_meta(run.dir) is not None  # type: ignore[attr-defined]
    finally:
        stop.set()
        writer.join()
    assert sorted(p.name for p in run.dir.iterdir()) == ["__meta.json"]  # no temp left
    shutil.rmtree(run.dir.parent.parent)


@pytest.mark.asyncio
async def test_gc_spares_young_orphans_and_a_mirror_with_meta(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from mate.api.config import get_settings

    async with mods_client(tmp_path, MOD):
        root = get_settings().module_results_dir_for(TEST_USER_ID)

        def _orphan(seed: int, age_s: float) -> Path:
            """A variant dir with outputs but no `__meta.json`."""
            d = root / "p3b-orphans" / MOD / f"_r_{run_variant_key({'params': {'o': seed}})}"
            d.mkdir(parents=True, exist_ok=True)
            (d / "out.json").write_text("{}")
            os.utime(d, (time.time() - age_s,) * 2)
            return d

        young = _orphan(1, 0)  # e.g. a hydrate that has not reached the meta yet
        stray, partial = _orphan(2, 3600), _orphan(3, 3600)
        fake = _fake_s3(monkeypatch)
        storage_sync.persist_dir_sync(stray)  # its mirror has no meta either
        storage_sync.persist_dir_sync(partial)
        partial_meta = storage_sync.rel_key(partial) + "__meta.json"
        fake.objects[partial_meta] = b"{}"  # the whole variant is in S3
        settings = get_settings()
        prev = settings.run_variants_max_bytes_per_user
        settings.run_variants_max_bytes_per_user = 1
        try:
            await coordinator_or_fail().variants._gc(TEST_USER_ID, set())
        finally:
            settings.run_variants_max_bytes_per_user = prev
        assert young.is_dir()
        assert not stray.exists()
        assert not any(k.startswith(storage_sync.rel_key(stray)) for k in fake.objects)
        assert not partial.exists()
        assert partial_meta in fake.objects
        assert storage_sync.rel_key(partial) + "out.json" in fake.objects
        shutil.rmtree(root / "p3b-orphans")


# --------------------------------------------------------------------------- #
# Worker meta
# --------------------------------------------------------------------------- #


@pytest.mark.asyncio
async def test_worker_meta_points_at_the_variant(tmp_path: Path) -> None:
    from mate.api.modules import get_module_loader
    from mate.api.modules.ctx_rpc import build_ctx_meta

    async with mods_client(tmp_path, *PAIR) as c:
        log_id = await import_sample(c)
        prod = await _ready(c, log_id, "artifact_producer", filter=_ONLY_TWO)
        [ref] = prod["refs"]
        spec = RunSpec(inputs={"reference_model": ref["ref"]}, filter=_ONLY_TWO).canonical()
        ctx = await get_module_loader()._make_context(
            "artifact_consumer", log_id, TEST_USER_ID, run_spec=spec
        )
        meta = build_ctx_meta(ctx)
        assert meta["cache_dir"] == str(
            _cache(log_id, "artifact_consumer", run_variant_key(spec)).dir
        )
        entry = meta["inputs"]["reference_model"]
        assert entry["ref"] == ref["ref"]
        assert (
            Path(entry["path"]).parent == _cache(log_id, "artifact_producer", prod["variant"]).dir
        )
        assert len(json.loads(Path(entry["path"]).read_text())["places"]) == 2
        assert meta["active_filter"] == _ONLY_TWO


@pytest.mark.asyncio
async def test_subprocess_worker_reads_a_variant_input(tmp_path: Path) -> None:
    from mate.api.modules.inputs import ModuleInputs
    from mate.api.modules.subprocess_host import SubprocessBridge
    from mate.sdk.artifacts import ArtifactRef
    from mate.sdk.manifest import Manifest

    from .test_artifacts import FIXTURES, _manifest
    from .test_worker_conformance import StubCtx, _launch_for

    folder = FIXTURES / "inputs_worker"
    consumer = Manifest.load_yaml(folder / "manifest.yaml")
    producer = _manifest(
        "artifact_producer",
        datasets=[{"id": "model", "artifact_type": "petri_net", "cache_key": "model"}],
    )
    log_id = "p3b-worker-log"
    key = run_variant_key(RunSpec(params={"k": 1}).canonical())
    net = {"places": [{"id": "v0"}, {"id": "v1"}], "transitions": [], "arcs": []}
    await ResultCache(log_id, "artifact_producer", TEST_USER_ID).set("model", {"places": []})
    await _cache(log_id, "artifact_producer", key).set("model", net)
    ref = ArtifactRef(log_id, "artifact_producer", "model", key)

    ctx = StubCtx(tmp_path)
    ctx.inputs = ModuleInputs.bind(  # type: ignore[attr-defined]
        consumer.inputs, {"artifact_producer": producer}, log_id, TEST_USER_ID, {"model": ref}
    )
    bridge = SubprocessBridge(consumer, folder, _launch_for("python")[0])
    await bridge.start()
    try:
        out = await bridge.call_handler("read_inputs", ctx, (), {})
    finally:
        await bridge.stop()
    assert out["model"] == {"value": net, "ref": f"{log_id}/artifact_producer@{key}/model"}


# --------------------------------------------------------------------------- #
# S3 mode
# --------------------------------------------------------------------------- #


@pytest.mark.asyncio
async def test_s3_default_dir_sync_leaves_run_variants_alone(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    fake = _fake_s3(monkeypatch)
    log_id = "p3b-s3-default"
    key = run_variant_key(RunSpec(params={"s": 1}).canonical())
    default, run = _cache(log_id, MOD), _cache(log_id, MOD, key)
    prefix = storage_sync.rel_key(default.dir)
    await run.set("out", {"v": 1})
    assert fake.uploads == [f"{prefix}_r_{key}/out.json"]

    # A default write uploads the default result only, never the variants beside it.
    fake.uploads.clear()
    await default.set("out", {"d": 1})
    assert fake.uploads == [f"{prefix}out.json"]

    # A cold default read hydrates the default result only; the variant on its own.
    shutil.rmtree(default.dir)
    storage_sync.forget(default.dir)
    assert await default.get("out") == {"d": 1}
    assert not run.dir.exists()
    assert await run.get("out") == {"v": 1}

    # Clearing the default result leaves the variant's mirror alone.
    default.clear()
    assert set(fake.objects) == {f"{prefix}_r_{key}/out.json"}
    run.remove()
    assert fake.objects == {}


@pytest.mark.asyncio
async def test_s3_lists_variants_with_no_local_copy(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from mate.api.mcp.toolsets import analysis

    from .test_mcp_analysis import _ctx, _principal, _seed_log

    async with mods_client(tmp_path, *PAIR) as c:
        log_id = await import_sample(c)
        prod = await _ready(c, log_id, "artifact_producer", filter=_ONLY_TWO)
        run = _cache(log_id, "artifact_producer", prod["variant"])
        _fake_s3(monkeypatch)
        storage_sync.persist_dir_sync(run.dir)  # the mirror holds the variant...
        shutil.rmtree(run.dir)  # ...this host does not (another VM, or the reaper)

        listed = (await c.get(_url(log_id, "artifact_producer"))).json()
        assert [(v["variant"], v["status"]) for v in listed] == [(prod["variant"], "ready")]
        # Only its meta came down for the listing.
        assert [p.name for p in run.dir.iterdir()] == ["__meta.json"]

        await _seed_log(log_id)  # MCP egress consent
        listed_mcp = await analysis.list_artifacts(_ctx(_principal()), log_id=log_id)
        assert [e["ref"] for e in listed_mcp] == [prod["refs"][0]["ref"]]


@pytest.mark.asyncio
async def test_s3_a_read_during_a_clear_never_resurrects_old_outputs(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    fake = _fake_s3(monkeypatch)
    default = _cache("p3b-s3-clear", MOD)
    prefix = storage_sync.rel_key(default.dir)
    await default.set("out", {"stale": 1})
    await default.set("__ai_guidance", {"g": 1})
    real_delete = storage_sync.delete_dir_remote_sync

    def _delete_between_reads(local_dir: Path, keep: Any = None) -> None:
        default._get_sync("out")  # type: ignore[attr-defined] - concurrent reads
        real_delete(local_dir, keep)
        default._get_sync("out")  # type: ignore[attr-defined]

    monkeypatch.setattr(storage_sync, "delete_dir_remote_sync", _delete_between_reads)
    default.clear(keep_reserved=True)
    monkeypatch.setattr(storage_sync, "delete_dir_remote_sync", real_delete)
    assert sorted(p.name for p in default.dir.iterdir()) == ["__ai_guidance.json"]
    await default.set("other", {"new": 1})  # the recompute writes another key
    assert set(fake.objects) == {f"{prefix}__ai_guidance.json", f"{prefix}other.json"}
    default.clear()


@pytest.mark.asyncio
async def test_s3_a_write_during_a_clear_never_resurrects_old_outputs(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A route caching an on-demand result into the module dir while a stale default run
    is cleared: its persist waits for the local wipe instead of landing between it and
    the S3 delete, where it would re-upload the old outputs."""
    fake = _fake_s3(monkeypatch)
    default = _cache("p3b-s3-write", MOD)
    prefix = storage_sync.rel_key(default.dir)
    await default.set("out", {"stale": 1})  # the old precompute result
    real_delete = storage_sync.delete_dir_remote_sync
    write = threading.Thread(
        target=default._set_and_persist,  # type: ignore[attr-defined]
        args=("kpis", {"route": 1}),
    )

    def _write_in_the_window(local_dir: Path, keep: Any = None) -> None:
        real_delete(local_dir, keep)
        write.start()
        write.join(0.2)
        assert write.is_alive()  # held off until the clear is done

    monkeypatch.setattr(storage_sync, "delete_dir_remote_sync", _write_in_the_window)
    default.clear(keep_reserved=True)
    write.join()
    monkeypatch.setattr(storage_sync, "delete_dir_remote_sync", real_delete)
    assert set(fake.objects) == {f"{prefix}kpis.json"}
    # The reaper evicts the local copy; the next read hydrates from the mirror.
    shutil.rmtree(default.dir)
    storage_sync.forget(default.dir)
    assert await default.get("out") is None
    assert await default.get("kpis") == {"route": 1}
    default.clear()


@pytest.mark.asyncio
async def test_s3_a_local_only_gc_lets_the_variant_hydrate_again(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The mirror holds the whole variant; this host a meta-less partial copy a read
    already marked hydrated. GC drops the local copy only - a read must refetch it."""
    fake = _fake_s3(monkeypatch)
    key = run_variant_key(RunSpec(params={"d": 1}).canonical())
    run = _cache("p3b-s3-gc", MOD, key)
    await run.set("out", {"v": 1})
    run.write_meta({"status": "ready", "spec": {}, "job_id": "j"})
    shutil.rmtree(run.dir.parent)
    storage_sync.forget(run.dir.parent)
    real_download = fake.download_file

    def _meta_fails(bucket: str, k: str, dest: str) -> None:
        if k.endswith("__meta.json"):
            raise RuntimeError("transient")
        real_download(bucket, k, dest)

    monkeypatch.setattr(fake, "download_file", _meta_fails)
    run.path("out")  # the hydrate fails half way: the output lands, the meta does not
    monkeypatch.setattr(fake, "download_file", real_download)
    assert run.path("out") is not None  # warm now: marked hydrated
    os.utime(run.dir, (time.time() - 3600,) * 2)

    await _gc_everything()
    assert not run.dir.exists()
    assert storage_sync.rel_key(run.dir) + "__meta.json" in fake.objects
    assert await run.get("out") == {"v": 1}
    run.remove()
