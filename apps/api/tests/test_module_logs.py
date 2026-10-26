"""Per-module logs (`mate.api.modules.module_logs` + ``GET /modules/{id}/logs``).

The Logs panel on a module's settings page used to be a live-only tail: a line
logged while the page was closed was gone, and a line logged from a worker thread
(a sync handler) never reached the bus at all - so it read "No log lines yet"
whenever it mattered. These tests pin the replacement: lines are stored per
(user, module) at the moment they are logged, read back over REST, and can be
narrowed by severity.
"""

from __future__ import annotations

import asyncio
import contextlib
import json
import math
from collections.abc import Iterator
from typing import Any

import pytest
import structlog
from httpx import AsyncClient

from mate.api.events import EventBus
from mate.api.modules import module_logs
from mate.api.modules.module_logs import (
    FIELD_STR_MAX,
    BusForwardingLogger,
    ModuleLogStore,
    json_safe,
    record_module_log,
)

from .conftest import TEST_USER_ID


@pytest.fixture(autouse=True)
def fresh_store(monkeypatch: pytest.MonkeyPatch) -> Iterator[ModuleLogStore]:
    """Every test gets its own empty store - the module-level one is process-global."""
    store = ModuleLogStore()
    monkeypatch.setattr(module_logs, "_store", store)
    yield store


# -- the store ------------------------------------------------------------------


def test_append_and_read_preserve_order_and_seq(fresh_store: ModuleLogStore) -> None:
    a = fresh_store.append("u1", "m", "info", "first", {"i": 1})
    b = fresh_store.append("u1", "m", "warning", "second", {"i": 2}, log_id="log-1", job_id="j")

    page = fresh_store.read("u1", "m")
    assert [(ln.level, ln.event, ln.fields) for ln in page.lines] == [
        ("info", "first", {"i": 1}),
        ("warning", "second", {"i": 2}),
    ]
    assert b.seq > a.seq
    assert page.last_seq == b.seq
    assert (page.lines[1].log_id, page.lines[1].job_id) == ("log-1", "j")
    assert page.truncated is False


def test_level_filter_narrows_lines_but_counts_stay_unfiltered(
    fresh_store: ModuleLogStore,
) -> None:
    for level in ("debug", "info", "info", "warning", "error"):
        fresh_store.append("u1", "m", level, f"e-{level}", {})  # type: ignore[arg-type]

    page = fresh_store.read("u1", "m", levels=["warning", "error"])
    assert [ln.level for ln in page.lines] == ["warning", "error"]
    # The chips still advertise the hidden levels.
    assert page.counts == {"debug": 1, "info": 2, "warning": 1, "error": 1}
    # Empty / None means "every level".
    assert len(fresh_store.read("u1", "m", levels=[]).lines) == 5
    assert len(fresh_store.read("u1", "m", levels=None).lines) == 5


def test_users_and_modules_are_isolated(fresh_store: ModuleLogStore) -> None:
    fresh_store.append("alice", "m", "info", "alice-secret", {"row": "alice's data"})
    fresh_store.append("bob", "m", "info", "bob-line", {})
    fresh_store.append("alice", "other", "info", "other-module", {})

    assert [ln.event for ln in fresh_store.read("alice", "m").lines] == ["alice-secret"]
    assert [ln.event for ln in fresh_store.read("bob", "m").lines] == ["bob-line"]
    assert fresh_store.read("carol", "m").lines == []
    assert fresh_store.read("carol", "m").last_seq == 0


def test_cap_evicts_oldest_and_flags_truncated() -> None:
    store = ModuleLogStore(max_lines=3)
    for n in range(5):
        store.append("u", "m", "info", f"e{n}", {})

    page = store.read("u", "m")
    assert [ln.event for ln in page.lines] == ["e2", "e3", "e4"]
    assert page.truncated is True
    assert page.capacity == 3
    # last_seq still points at the newest line, so a cursor keeps working.
    assert page.last_seq == page.lines[-1].seq


def test_key_lru_eviction_drops_least_recently_used_pair() -> None:
    store = ModuleLogStore(max_keys=2)
    store.append("u", "a", "info", "x", {})
    store.append("u", "b", "info", "x", {})
    store.append("u", "a", "info", "y", {})  # refresh "a"
    store.append("u", "c", "info", "x", {})  # evicts the oldest-touched, "b"

    assert store.read("u", "b").lines == []
    assert store.read("u", "b").last_seq == 0
    assert [ln.event for ln in store.read("u", "a").lines] == ["x", "y"]


def test_after_seq_cursor_and_limit_keep_the_newest(fresh_store: ModuleLogStore) -> None:
    lines = [fresh_store.append("u", "m", "info", f"e{n}", {}) for n in range(10)]

    assert [ln.event for ln in fresh_store.read("u", "m", limit=3).lines] == ["e7", "e8", "e9"]
    after = fresh_store.read("u", "m", after_seq=lines[6].seq)
    assert [ln.event for ln in after.lines] == ["e7", "e8", "e9"]
    assert fresh_store.read("u", "m", after_seq=lines[-1].seq).lines == []


def test_log_id_filter(fresh_store: ModuleLogStore) -> None:
    fresh_store.append("u", "m", "info", "on-a", {}, log_id="a")
    fresh_store.append("u", "m", "info", "on-b", {}, log_id="b")
    fresh_store.append("u", "m", "info", "global", {})

    assert [ln.event for ln in fresh_store.read("u", "m", log_id="a").lines] == ["on-a"]


def test_fields_are_coerced_json_safe_and_bounded() -> None:
    class Weird:
        def __str__(self) -> str:
            return "weird-obj"

    cyclic: dict[str, Any] = {}
    cyclic["self"] = cyclic

    safe = json_safe(
        {
            "obj": Weird(),
            "nested": {"k": [Weird(), (1, 2)]},
            "nan": math.nan,
            "inf": math.inf,
            "big": "x" * (FIELD_STR_MAX + 500),
            "wide": list(range(500)),
            "cyclic": cyclic,
        }
    )

    # Round-trips through strict JSON: no NaN/Infinity, no exotic objects, no recursion.
    json.dumps(safe, allow_nan=False)
    assert safe["obj"] == "weird-obj"
    assert safe["nested"] == {"k": ["weird-obj", [1, 2]]}
    assert safe["nan"] == "nan" and safe["inf"] == "inf"
    assert len(safe["big"]) < FIELD_STR_MAX + 40 and safe["big"].endswith("(+500 chars)")
    assert len(safe["wide"]) == 51 and safe["wide"][-1] == "… (+450 more)"


def test_a_line_with_many_wide_fields_is_bounded_as_a_whole(fresh_store: ModuleLogStore) -> None:
    wide = {f"k{n}": "y" * 1500 for n in range(40)}  # each field fits; together they do not
    line = fresh_store.append("u", "m", "info", "wide", wide)

    assert list(line.fields) == ["fields_truncated"]
    assert len(line.fields["fields_truncated"]) <= module_logs.FIELDS_JSON_MAX + 1


# -- the logger -----------------------------------------------------------------


def _logger(bus: EventBus, **kw: Any) -> BusForwardingLogger:
    return BusForwardingLogger(
        structlog.get_logger("test"), bus, "m", "u1", kw.pop("job_id", None), **kw
    )


@pytest.mark.asyncio
async def test_logger_records_every_level_with_bound_values(
    fresh_store: ModuleLogStore,
) -> None:
    bus = EventBus()
    log = _logger(bus, log_id="log-9").bind(stage="load")
    log.debug("d")
    log.info("i", rows=3)
    log.warning("w")
    log.warn("w2")
    log.error("e")
    log.critical("c")

    page = fresh_store.read("u1", "m")
    assert [ln.level for ln in page.lines] == [
        "debug",
        "info",
        "warning",
        "warning",
        "error",
        "error",
    ]
    # `.bind()` values travel with the stored line, as they do on stdout.
    assert page.lines[1].fields == {"stage": "load", "rows": 3}
    assert {ln.log_id for ln in page.lines} == {"log-9"}


@pytest.mark.asyncio
async def test_exception_records_a_traceback_not_exc_info(fresh_store: ModuleLogStore) -> None:
    log = _logger(EventBus())
    try:
        raise ValueError("bad input")
    except ValueError:
        log.exception("m.failed", name="x")
    log.warning("m.degraded", exc_info=True)  # outside an except block: no traceback

    first, second = fresh_store.read("u1", "m").lines
    assert first.level == "error"
    assert first.fields["name"] == "x"
    assert "ValueError: bad input" in first.fields["traceback"]
    assert "exc_info" not in first.fields
    assert "traceback" not in second.fields


@pytest.mark.asyncio
async def test_a_line_logged_from_a_worker_thread_is_stored_and_reaches_the_bus(
    fresh_store: ModuleLogStore,
) -> None:
    """The regression: `asyncio.create_task` in a thread raises, and the old
    forwarder swallowed it - so a sync handler's lines never reached anything."""
    bus = EventBus()
    log = _logger(bus)

    async with bus.subscribe(["module.log.*"]) as stream:
        await asyncio.to_thread(log.error, "thread.line", code=7)
        env = await asyncio.wait_for(anext(stream), timeout=2)

    assert env.topic == "module.log.error"
    assert env.payload["event"] == "thread.line"
    assert env.payload["user_id"] == "u1"
    assert env.payload["fields"] == {"code": 7}
    stored = fresh_store.read("u1", "m").lines
    assert [ln.event for ln in stored] == ["thread.line"]
    # The live frame and the stored line share an id, so the UI can de-duplicate.
    assert env.payload["seq"] == stored[0].seq


@pytest.mark.asyncio
async def test_no_bus_work_is_done_while_nobody_watches_the_live_tail(
    monkeypatch: pytest.MonkeyPatch, fresh_store: ModuleLogStore
) -> None:
    bus = EventBus()
    published: list[str] = []
    real_publish = bus.publish

    async def spy(topic: str, payload: dict[str, Any]) -> None:
        published.append(topic)
        await real_publish(topic, payload)

    monkeypatch.setattr(bus, "publish", spy)
    log = _logger(bus)

    log.info("nobody.listening")
    await asyncio.sleep(0)
    assert published == []  # not even a task was spawned...
    assert [ln.event for ln in fresh_store.read("u1", "m").lines] == [
        "nobody.listening"
    ]  # ...but stored

    async with bus.subscribe(["module.log.*"]) as stream:
        log.info("somebody.listening")
        env = await asyncio.wait_for(anext(stream), timeout=2)
    assert published == ["module.log.info"] and env.payload["event"] == "somebody.listening"


@pytest.mark.asyncio
async def test_a_logging_failure_never_breaks_the_handler(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def boom(*_a: Any, **_k: Any) -> None:
        raise RuntimeError("store down")

    monkeypatch.setattr(module_logs.ModuleLogStore, "append", boom)
    _logger(EventBus()).info("still fine")  # must not raise


@pytest.mark.asyncio
async def test_record_module_log_marks_source(fresh_store: ModuleLogStore) -> None:
    record_module_log("u1", "m", "info", "job.started", {}, source="platform", bus=EventBus())
    record_module_log("u1", "m", "warning", "TF chatter", {}, source="stderr", bus=EventBus())

    assert [(ln.source, ln.event) for ln in fresh_store.read("u1", "m").lines] == [
        ("platform", "job.started"),
        ("stderr", "TF chatter"),
    ]


# -- worker-side logger (subprocess / JVM-bridged / job-worker modules) -----------


@pytest.mark.asyncio
async def test_worker_logger_ships_a_module_only_traceback_for_exception() -> None:
    """`exc_info=True` is meaningless across the wire - the host is not inside the
    except block - so the worker renders the traceback itself, minus its own frames."""
    from mate.api.modules import subprocess_worker as worker

    sent: list[dict[str, Any]] = []

    class Conn:
        async def send_request(self, method: str, params: dict[str, Any]) -> None:
            assert method == "ctx.logger.log"
            sent.append(params)

    proxy = worker._LoggerProxy(Conn(), "tok").bind(stage="fit")  # type: ignore[reportPrivateUsage]
    # A frame whose file is the worker's own, as one raised through its call path would be.
    code = compile("def boom():\n    raise RuntimeError('x')\n", worker.__file__, "exec")
    scope: dict[str, Any] = {}
    exec(code, scope)
    try:
        scope["boom"]()
    except RuntimeError:
        proxy.exception("w.failed", model="m1")
    proxy.critical("w.fatal")
    proxy.warn("w.careful")
    await asyncio.sleep(0)  # the sends are fire-and-forget tasks

    failed, fatal, careful = (p["payload"] for p in sent)
    assert [p["level"] for p in sent] == ["error", "error", "warning"]
    assert failed["event"] == "w.failed" and failed["model"] == "m1" and failed["stage"] == "fit"
    assert "RuntimeError: x" in failed["traceback"]
    assert "subprocess_worker.py" not in failed["traceback"]
    assert "exc_info" not in failed
    assert fatal["event"] == "w.fatal" and careful["event"] == "w.careful"


# -- REST + loader lifecycle ----------------------------------------------------


@pytest.mark.asyncio
async def test_logs_route_returns_lines_from_async_and_sync_handlers(
    client_with_sample_mod: AsyncClient,
) -> None:
    assert (
        await client_with_sample_mod.get("/api/v1/modules/sample_mod/log-demo")
    ).status_code == 200
    assert (
        await client_with_sample_mod.get("/api/v1/modules/sample_mod/log-demo-sync")
    ).status_code == 200

    resp = await client_with_sample_mod.get("/api/v1/modules/sample_mod/logs")
    assert resp.status_code == 200
    body = resp.json()
    assert body["module_id"] == "sample_mod"
    assert [ln["event"] for ln in body["lines"]] == [
        "sample.debug_line",
        "sample.async_line",
        "sample.async_warning",
        "sample.sync_error",  # from a worker thread
    ]
    assert body["counts"] == {"debug": 1, "info": 1, "warning": 1, "error": 1}
    assert body["capacity"] > 0 and body["truncated"] is False
    assert body["lines"][-1]["fields"] == {"code": 7}
    assert body["lines"][-1]["source"] == "module"
    assert body["last_seq"] == body["lines"][-1]["seq"]


@pytest.mark.asyncio
async def test_logs_route_filters_by_severity(client_with_sample_mod: AsyncClient) -> None:
    await client_with_sample_mod.get("/api/v1/modules/sample_mod/log-demo")
    await client_with_sample_mod.get("/api/v1/modules/sample_mod/log-demo-sync")

    resp = await client_with_sample_mod.get(
        "/api/v1/modules/sample_mod/logs", params=[("level", "warning"), ("level", "error")]
    )
    body = resp.json()
    assert [ln["level"] for ln in body["lines"]] == ["warning", "error"]
    # Counts ignore the filter so the chips keep their numbers.
    assert body["counts"] == {"debug": 1, "info": 1, "warning": 1, "error": 1}

    assert (
        await client_with_sample_mod.get(
            "/api/v1/modules/sample_mod/logs", params={"level": "loud"}
        )
    ).status_code == 422


@pytest.mark.asyncio
async def test_logs_route_after_cursor_returns_only_newer_lines(
    client_with_sample_mod: AsyncClient,
) -> None:
    await client_with_sample_mod.get("/api/v1/modules/sample_mod/log-demo")
    cursor = (await client_with_sample_mod.get("/api/v1/modules/sample_mod/logs")).json()[
        "last_seq"
    ]
    await client_with_sample_mod.get("/api/v1/modules/sample_mod/log-demo-sync")

    body = (
        await client_with_sample_mod.get(
            "/api/v1/modules/sample_mod/logs", params={"after": cursor}
        )
    ).json()
    assert [ln["event"] for ln in body["lines"]] == ["sample.sync_error"]


@pytest.mark.asyncio
async def test_logs_route_is_scoped_to_the_calling_user(
    client_with_sample_mod: AsyncClient,
) -> None:
    record_module_log("someone-else", "sample_mod", "error", "not-yours", {"secret": 1})
    record_module_log(TEST_USER_ID, "sample_mod", "info", "mine", {})

    body = (await client_with_sample_mod.get("/api/v1/modules/sample_mod/logs")).json()
    assert [ln["event"] for ln in body["lines"]] == ["mine"]


@pytest.mark.asyncio
async def test_logs_route_404s_for_a_module_the_user_has_not_installed(
    client_with_sample_mod: AsyncClient,
) -> None:
    resp = await client_with_sample_mod.get("/api/v1/modules/no_such_module/logs")
    assert resp.status_code == 404


@pytest.mark.asyncio
async def test_a_crashing_handler_logs_the_failure_with_a_traceback(
    client_with_sample_mod: AsyncClient,
) -> None:
    # The ASGI transport re-raises the app's unhandled error after the 500 response.
    with pytest.raises(RuntimeError, match="kaboom"):
        await client_with_sample_mod.get("/api/v1/modules/sample_mod/boom")

    lines = (await client_with_sample_mod.get("/api/v1/modules/sample_mod/logs")).json()["lines"]
    assert [ln["event"] for ln in lines] == ["handler.failed"]
    failed = lines[0]
    assert failed["level"] == "error" and failed["source"] == "platform"
    assert failed["fields"]["handler"] == "boom"
    assert failed["fields"]["error"] == "RuntimeError" and failed["fields"]["message"] == "kaboom"
    traceback_text = failed["fields"]["traceback"]
    assert "RuntimeError: kaboom" in traceback_text
    assert "in boom" in traceback_text  # the module's own frame stays
    # ...the platform's frames (and the server's install path) do not.
    assert "loader.py" not in traceback_text and "_dispatch_handler" not in traceback_text
    assert isinstance(failed["fields"]["duration_ms"], int)


@pytest.mark.asyncio
async def test_plain_successful_route_calls_are_not_recorded(
    client_with_sample_mod: AsyncClient,
) -> None:
    """A panel polls routes; recording each success would bury the real lines."""
    for _ in range(3):
        await client_with_sample_mod.get("/api/v1/modules/sample_mod/ping")

    body = (await client_with_sample_mod.get("/api/v1/modules/sample_mod/logs")).json()
    assert body["lines"] == []
    assert body["last_seq"] == 0


@pytest.mark.asyncio
async def test_a_broken_log_store_never_replaces_the_handlers_own_exception(
    client_with_sample_mod: AsyncClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    from mate.api.modules import get_module_loader

    def boom(*_a: Any, **_k: Any) -> None:
        raise RuntimeError("store down")

    monkeypatch.setattr(module_logs.ModuleLogStore, "append", boom)
    loader = get_module_loader()
    ctx = await loader._make_context("sample_mod", "", TEST_USER_ID)  # type: ignore[reportPrivateUsage]

    async def broken(ctx: Any) -> None:
        raise ValueError("the real failure")

    with pytest.raises(ValueError, match="the real failure"):
        await loader._invoke_handler(broken, ctx)  # type: ignore[reportPrivateUsage]


@pytest.mark.asyncio
async def test_job_backed_handlers_log_started_and_the_outcome(
    client_with_sample_mod: AsyncClient,
    fresh_store: ModuleLogStore,
) -> None:
    from mate.api.jobs.runtime import JobCancelled
    from mate.api.modules import get_module_loader

    loader = get_module_loader()

    async def run(fn: Any) -> None:
        ctx = await loader._make_context("sample_mod", "log-1", TEST_USER_ID, job_id="job-1")  # type: ignore[reportPrivateUsage]
        ctx._ff_job_id = "job-1"  # type: ignore[attr-defined]
        with contextlib.suppress(BaseException):  # the failure/cancel cases re-raise
            await loader._invoke_handler(fn, ctx)  # type: ignore[reportPrivateUsage]

    async def fine(ctx: Any) -> None:
        return None

    async def broken(ctx: Any) -> None:
        raise ValueError("nope")

    async def cancelled(ctx: Any) -> None:
        raise JobCancelled()

    await run(fine)
    await run(broken)
    await run(cancelled)

    lines = fresh_store.read(TEST_USER_ID, "sample_mod").lines
    assert [(ln.event, ln.level) for ln in lines] == [
        ("job.started", "info"),
        ("job.completed", "info"),
        ("job.started", "info"),
        ("handler.failed", "error"),
        ("job.started", "info"),
        ("job.cancelled", "info"),
    ]
    assert {ln.job_id for ln in lines} == {"job-1"}
    assert {ln.log_id for ln in lines} == {"log-1"}
    assert lines[3].fields["error"] == "ValueError"
