"""Per-(user, module) log store behind *Settings → Modules → <module> → Logs*.

Every ``ctx.logger`` call - from an in-process module, a subprocess/JVM worker,
a sync handler running in a worker thread - and every platform lifecycle line
(job started/completed/failed, handler crashed) is appended here by the
loader's ``_BusForwardingLogger``. The Logs panel then *reads* it over REST
(``GET /modules/{id}/logs``) and only uses the ``module.log.*`` bus topic as a
live add-on. The panel used to be live-only: a line logged while nobody had the
page open was lost, and a line logged from a worker thread never reached the bus
at all - so the panel was empty whenever it mattered.

Scoped by ``(user_id, module_id)``: log fields can embed a user's data, and a
module's code is shared in-process across tenants, so one user's lines must
never be readable by another. In-memory and bounded on both axes (last N lines
per key, last M keys, LRU) - it dies with the process like the job queue and the
per-job ring (``job_logs``), and cannot grow without limit on a long-lived
deployment.
"""

from __future__ import annotations

import asyncio
import contextlib
import itertools
import json
import math
import os
import sys
import threading
import time
import traceback
from collections import OrderedDict, deque
from collections.abc import Collection
from dataclasses import dataclass
from typing import Any, Final, Literal, get_args

import mate.sdk
from mate.api.events import EventBus, get_event_bus
from mate.api.modules.job_logs import get_job_log_buffer
from mate.api.tasks import spawn

LogLevel = Literal["debug", "info", "warning", "error"]
LOG_LEVELS: Final[tuple[LogLevel, ...]] = get_args(LogLevel)

# Where a line came from: the module's own ``ctx.logger``, a platform lifecycle
# record about the module's handler, or a worker process's raw stdout/stderr.
LogSource = Literal["module", "platform", "stdout", "stderr"]

# Retained lines per (user, module) and number of (user, module) keys kept.
MODULE_LOG_MAX_LINES = 1000
MODULE_LOG_MAX_KEYS = 512

# One field value can be a dataframe repr or a stack trace; cap it so a single
# chatty `ctx.logger.info("x", df=frame)` cannot pin megabytes in the ring or
# blow up the SSE frame. Containers are bounded the same way.
FIELD_STR_MAX = 2_000
FIELD_ITEMS_MAX = 50
_FIELD_DEPTH_MAX = 4
# ...and the whole `fields` blob of one line, so the ring (1000 lines per key)
# and the REST page stay bounded no matter how the author shapes their kwargs.
FIELDS_JSON_MAX = 8_000


def json_safe(value: Any, _depth: int = 0) -> Any:
    """Coerce a log field into something the REST layer can always serialise.

    Module authors pass arbitrary kwargs. Keep JSON primitives as-is, recurse
    into (bounded) lists/dicts, stringify anything else, and cap strings, widths
    and depth so one exotic field can neither 500 the logs endpoint nor recurse
    forever on a self-referencing structure.
    """
    if value is None or isinstance(value, (bool, int)):
        return value
    if isinstance(value, float):
        # NaN/Infinity are not JSON: `JSON.parse` on the SSE frame would throw.
        return value if math.isfinite(value) else str(value)
    if isinstance(value, str):
        if len(value) > FIELD_STR_MAX:
            return f"{value[:FIELD_STR_MAX]}… (+{len(value) - FIELD_STR_MAX} chars)"
        return value
    if _depth >= _FIELD_DEPTH_MAX:
        return json_safe(str(value), _depth)
    if isinstance(value, (list, tuple, set, frozenset)):
        items = list(value)
        out = [json_safe(v, _depth + 1) for v in items[:FIELD_ITEMS_MAX]]
        if len(items) > FIELD_ITEMS_MAX:
            out.append(f"… (+{len(items) - FIELD_ITEMS_MAX} more)")
        return out
    if isinstance(value, dict):
        pairs = list(value.items())
        shown = {str(k): json_safe(v, _depth + 1) for k, v in pairs[:FIELD_ITEMS_MAX]}
        if len(pairs) > FIELD_ITEMS_MAX:
            shown["…"] = f"+{len(pairs) - FIELD_ITEMS_MAX} more"
        return shown
    return json_safe(str(value), _depth)


@dataclass(frozen=True)
class ModuleLogLine:
    # Process-wide monotonic id: lets the client de-duplicate a REST backfill
    # against the live stream and ask for "everything after this" on reconnect.
    seq: int
    ts: float
    level: LogLevel
    event: str
    fields: dict[str, Any]
    source: LogSource = "module"
    log_id: str | None = None
    job_id: str | None = None


@dataclass(frozen=True)
class ModuleLogPage:
    """One read of the store: the (filtered) lines plus unfiltered context."""

    lines: list[ModuleLogLine]
    # Retained lines per level, *before* the level filter - the filter chips'
    # counts, so a hidden level still advertises that it has lines.
    counts: dict[LogLevel, int]
    # True once the per-key cap has evicted older lines: this is a tail.
    truncated: bool
    capacity: int
    # Highest seq ever stored for the key (0 = nothing yet); the cursor a client
    # passes back as ``after_seq``.
    last_seq: int


class ModuleLogStore:
    """Thread-safe, bounded per-(user, module) log store.

    ``append`` runs on the event loop *and* on module worker threads (sync
    handlers, ``asyncio.to_thread`` compute), so every mutation holds a lock.
    Readers get snapshot copies.
    """

    def __init__(
        self,
        max_lines: int = MODULE_LOG_MAX_LINES,
        max_keys: int = MODULE_LOG_MAX_KEYS,
    ) -> None:
        self._lines: OrderedDict[tuple[str, str], deque[ModuleLogLine]] = OrderedDict()
        self._last_seq: dict[tuple[str, str], int] = {}
        self._dropped: set[tuple[str, str]] = set()
        self._max_lines = max_lines
        self._max_keys = max_keys
        self._seq = itertools.count(1)
        self._lock = threading.Lock()

    def append(
        self,
        user_id: str,
        module_id: str,
        level: LogLevel,
        event: str,
        fields: dict[str, Any],
        *,
        source: LogSource = "module",
        log_id: str | None = None,
        job_id: str | None = None,
    ) -> ModuleLogLine:
        # Coerce outside the lock - it can walk a large structure.
        safe_fields = {str(k): json_safe(v) for k, v in fields.items()}
        encoded = json.dumps(safe_fields)
        if len(encoded) > FIELDS_JSON_MAX:
            # Many wide fields add up even with each one capped; keep the head.
            safe_fields = {"fields_truncated": f"{encoded[:FIELDS_JSON_MAX]}…"}
        key = (user_id, module_id)
        with self._lock:
            line = ModuleLogLine(
                seq=next(self._seq),
                ts=time.time(),
                level=level,
                event=event,
                fields=safe_fields,
                source=source,
                log_id=log_id or None,
                job_id=job_id or None,
            )
            dq = self._lines.get(key)
            if dq is None:
                dq = deque(maxlen=self._max_lines)
                self._lines[key] = dq
                while len(self._lines) > self._max_keys:
                    old, _ = self._lines.popitem(last=False)
                    self._last_seq.pop(old, None)
                    self._dropped.discard(old)
            else:
                self._lines.move_to_end(key)
            if len(dq) == self._max_lines:
                self._dropped.add(key)  # this append evicts the oldest line
            dq.append(line)
            self._last_seq[key] = line.seq
        return line

    def read(
        self,
        user_id: str,
        module_id: str,
        *,
        levels: Collection[str] | None = None,
        limit: int = 500,
        after_seq: int = 0,
        log_id: str | None = None,
    ) -> ModuleLogPage:
        """Newest-last page of lines, optionally narrowed by level / log / cursor.

        ``levels=None`` (or empty) means every level. ``after_seq`` returns only
        lines newer than that cursor - what a reconnecting client uses to fill
        the gap. When more than ``limit`` lines match, the *newest* ``limit``
        are kept.
        """
        key = (user_id, module_id)
        with self._lock:
            dq = self._lines.get(key)
            snapshot = list(dq) if dq is not None else []
            last_seq = self._last_seq.get(key, 0)
            truncated = key in self._dropped

        counts: dict[LogLevel, int] = dict.fromkeys(LOG_LEVELS, 0)
        for line in snapshot:
            counts[line.level] += 1

        wanted = set(levels) if levels else None
        lines = [
            ln
            for ln in snapshot
            if ln.seq > after_seq
            and (wanted is None or ln.level in wanted)
            and (log_id is None or ln.log_id == log_id)
        ]
        if limit and len(lines) > limit:
            lines = lines[-limit:]
        return ModuleLogPage(
            lines=lines,
            counts=counts,
            truncated=truncated,
            capacity=self._max_lines,
            last_seq=last_seq,
        )


_store: ModuleLogStore | None = None


def get_module_log_store() -> ModuleLogStore:
    global _store
    if _store is None:
        _store = ModuleLogStore()
    return _store


# ---------------------------------------------------------------------------
# Recording: the one write path every log line takes
# ---------------------------------------------------------------------------


def _running_loop() -> asyncio.AbstractEventLoop | None:
    try:
        return asyncio.get_running_loop()
    except RuntimeError:
        return None


def _publish(
    bus: EventBus | None,
    loop: asyncio.AbstractEventLoop | None,
    line: ModuleLogLine,
    payload: dict[str, Any],
) -> None:
    """Best-effort fan-out of a stored line onto ``module.log.<level>``.

    From the event loop it is spawned directly. From a worker thread (a sync
    handler, ``asyncio.to_thread`` compute) there is no running loop, so
    ``asyncio.create_task`` would raise - which the old code swallowed, silently
    dropping every line a sync module logged. Hop onto the owning loop instead.
    The stored line is already safe in the ring either way; this only feeds the
    live tail.
    """
    if bus is None:
        with contextlib.suppress(RuntimeError):
            bus = get_event_bus()
    if bus is None:
        return
    topic = f"module.log.{line.level}"
    live_bus = bus
    # The live tail only exists while someone has the Logs panel open. A chatty
    # module would otherwise pay a task per line for an audience of nobody - the
    # line is already stored, and the panel backfills over REST when it opens.
    if not live_bus.has_subscribers(topic):
        return

    def _fire() -> None:
        spawn(live_bus.publish(topic, payload))

    if _running_loop() is not None:
        _fire()
    elif loop is not None and not loop.is_closed():
        loop.call_soon_threadsafe(_fire)


def record_module_log(
    user_id: str,
    module_id: str,
    level: LogLevel,
    event: str,
    fields: dict[str, Any],
    *,
    source: LogSource = "module",
    log_id: str | None = None,
    job_id: str | None = None,
    bus: EventBus | None = None,
    loop: asyncio.AbstractEventLoop | None = None,
) -> None:
    """Store one line and push it to the live tail. Never raises into the caller.

    Callers sit inside a handler's ``except`` block or on a hot path; a logging
    failure there must not replace the handler's own exception or break its work.
    """
    with contextlib.suppress(Exception):
        line = get_module_log_store().append(
            user_id, module_id, level, event, fields, source=source, log_id=log_id, job_id=job_id
        )
        # `user_id` scopes the live line to its owner: the Logs panel subscribes to
        # `module.log.*` over the per-user stream, and the fan-out filters on it.
        _publish(
            bus,
            loop,
            line,
            {
                "module_id": module_id,
                "user_id": user_id,
                "log_id": line.log_id,
                "job_id": line.job_id,
                "seq": line.seq,
                "ts": line.ts,
                "source": line.source,
                "event": event,
                "fields": line.fields,
            },
        )


# The platform's own packages. Their frames (the loader's `_invoke_handler`, the
# SDK's thread wrappers) say nothing about the module's bug and carry the server's
# install path, which the module's user has no business reading.
_PLATFORM_DIRS = (
    os.path.dirname(os.path.dirname(os.path.abspath(__file__))) + os.sep,
    os.path.dirname(os.path.abspath(mate.sdk.__file__)) + os.sep,
)


def _drop_platform_frames(exc: traceback.TracebackException, seen: set[int]) -> None:
    if id(exc) in seen:
        return
    seen.add(id(exc))
    exc.stack = traceback.StackSummary.from_list(
        [f for f in exc.stack if not os.path.abspath(f.filename).startswith(_PLATFORM_DIRS)]
    )
    for chained in (exc.__cause__, exc.__context__):
        if chained is not None:
            _drop_platform_frames(chained, seen)


def format_exc_info(exc_info: Any) -> str:
    """Render ``exc_info=...`` (True / exception / tuple) as a bounded traceback string.

    Frames inside the platform itself are dropped; the module's own frames and the
    exception line stay.
    """
    if exc_info is True:
        exc = sys.exc_info()[1]
    elif isinstance(exc_info, BaseException):
        exc = exc_info
    elif isinstance(exc_info, tuple) and len(exc_info) == 3:  # pyright: ignore[reportUnknownArgumentType]
        exc = exc_info[1]  # pyright: ignore[reportUnknownVariableType]
    else:
        return ""
    if not isinstance(exc, BaseException):  # `exc_info=True` outside an except block
        return ""
    rendered = traceback.TracebackException.from_exception(exc)
    _drop_platform_frames(rendered, set())
    text = "".join(rendered.format())
    # Keep the *tail*: the raising frame and the exception line are what matter.
    return text if len(text) <= FIELD_STR_MAX else "…\n" + text[-(FIELD_STR_MAX - 2) :]


class BusForwardingLogger:
    """The ``ctx.logger`` every module handler receives.

    Wraps a structlog ``BoundLogger`` so server-side log aggregation (stdout) is
    untouched, and additionally records every call into the per-(user, module)
    store, the per-job ring (admin Jobs tab) and the ``module.log.<level>`` bus
    topic (live tail).
    """

    def __init__(
        self,
        base: Any,
        bus: EventBus,
        module_id: str,
        user_id: str,
        job_id: str | None = None,
        *,
        log_id: str | None = None,
        bound: dict[str, Any] | None = None,
        loop: asyncio.AbstractEventLoop | None = None,
    ) -> None:
        self._base = base
        self._bus = bus
        self._module_id = module_id
        self._user_id = user_id
        # Set when this logger belongs to a job invocation (precompute / @job /
        # route-job); None for direct route handlers. Drives the per-job ring.
        self._job_id = job_id
        self._log_id = log_id
        # Values the author attached with `.bind(...)`: structlog prints them on
        # stdout, so the stored line has to carry them too.
        self._bound = dict(bound or {})
        # The loop to hop onto when a line is logged from a worker thread. The
        # loader builds contexts on the loop, so capture it here.
        self._loop = loop if loop is not None else _running_loop()

    def bind(self, **kwargs: Any) -> BusForwardingLogger:
        return BusForwardingLogger(
            self._base.bind(**kwargs),
            self._bus,
            self._module_id,
            self._user_id,
            self._job_id,
            log_id=self._log_id,
            bound={**self._bound, **kwargs},
            loop=self._loop,
        )

    def _emit(self, level: LogLevel, event: str, **kwargs: Any) -> None:
        getattr(self._base, level)(event, **kwargs)
        # Best-effort from here on: never let a logging side-effect break the handler.
        with contextlib.suppress(Exception):
            fields = {**self._bound, **{k: v for k, v in kwargs.items() if k != "exc_info"}}
            tb = format_exc_info(kwargs["exc_info"]) if kwargs.get("exc_info") else ""
            if tb:
                fields["traceback"] = tb
            # Mirror into the per-job ring (admin Jobs tab). Synchronous and
            # lock-guarded, so unlike a bus publish it also captures lines logged
            # from inside `asyncio.to_thread` module compute (no running loop).
            if self._job_id:
                with contextlib.suppress(Exception):
                    get_job_log_buffer().append(self._job_id, level, event, fields)
            record_module_log(
                self._user_id,
                self._module_id,
                level,
                event,
                fields,
                log_id=self._log_id,
                job_id=self._job_id,
                bus=self._bus,
                loop=self._loop,
            )

    def debug(self, event: str, **kw: Any) -> None:
        self._emit("debug", event, **kw)

    def info(self, event: str, **kw: Any) -> None:
        self._emit("info", event, **kw)

    def warning(self, event: str, **kw: Any) -> None:
        self._emit("warning", event, **kw)

    warn = warning

    def error(self, event: str, **kw: Any) -> None:
        self._emit("error", event, **kw)

    def critical(self, event: str, **kw: Any) -> None:
        self._emit("error", event, **kw)

    def exception(self, event: str, **kw: Any) -> None:
        self._emit("error", event, exc_info=True, **kw)
