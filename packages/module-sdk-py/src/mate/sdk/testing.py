"""Test helpers for module authors: a fake ``ModuleContext`` over a small log (README §10).

    import asyncio
    from mate.sdk.testing import build_test_context, load_module, sample_log

    def test_summary() -> None:
        module = load_module("modules/my_module")

        async def check() -> None:
            log = sample_log(rows=1000)
            ctx = await build_test_context(log_id=log.id, module_id="my_module")
            out = await module.summary(ctx)
            assert out["rows"]

        asyncio.run(check())

Every collaborator is an in-memory stand-in for the Protocols in ``mate.sdk.context``:
``ctx.event_log`` serves the log the way the importer stores it (string ids, tz-naive UTC
timestamps) through pandas / duckdb / polars / pm4py, ``ctx.cache`` is a dict
(``MemoryCache.data``), ``ctx.progress`` and ``ctx.bus`` record their calls
(``RecordingProgress.calls``, ``RecordingBus.events``), ``ctx.registry`` knows no other
module and ``ctx.run_in_process`` runs the function in a thread.

pandas, pyarrow and duckdb come from the platform's ``inherit`` set, not from mate-sdk,
so they are imported lazily; polars and pm4py only when a handler asks for them.
"""

from __future__ import annotations

import asyncio
import importlib
import importlib.util
import io
import sys
import tempfile
import uuid
from collections.abc import AsyncIterator
from dataclasses import dataclass, field
from importlib import resources
from pathlib import Path
from typing import Any

import structlog

from mate.sdk.context import EventLogAccessProtocol, ModuleContext
from mate.sdk.errors import ModuleError
from mate.sdk.manifest import Manifest
from mate.sdk.module import Module

__all__ = [
    "EmptyRegistry",
    "FakeEventLog",
    "MemoryCache",
    "MemoryConfig",
    "RecordingBus",
    "RecordingProgress",
    "SampleLog",
    "build_test_context",
    "load_module",
    "sample_log",
]

# Logs created by `sample_log`, so `build_test_context(log_id=...)` and
# `ctx.open_event_log(...)` find them by id like the platform does.
_LOGS: dict[str, SampleLog] = {}


def _need(name: str) -> Any:
    try:
        return importlib.import_module(name)
    except ModuleNotFoundError as exc:
        raise RuntimeError(
            f"mate.sdk.testing needs `{name}`, which the platform provides but mate-sdk "
            "does not install - run inside the platform workspace (`uv run ...`)."
        ) from exc


@dataclass(frozen=True)
class SampleLog:
    """A case-centric log for tests: an id plus a pandas frame in canonical columns."""

    id: str
    frame: Any


def sample_log(rows: int | None = None) -> SampleLog:
    """The bundled sample log: 10 cases, 43 events, ``case_id/activity/timestamp/resource``.

    ``rows=N`` tiles it to exactly N events; each copy gets fresh case ids and is
    shifted one week later.
    """
    pd = _need("pandas")
    text = resources.files("mate.sdk").joinpath("data/sample_log.csv").read_text(encoding="utf-8")
    frame = pd.read_csv(io.StringIO(text))
    frame["timestamp"] = pd.to_datetime(frame["timestamp"], utc=True)
    if rows is not None:
        if rows < 1:
            raise ValueError("sample_log(rows=...) needs at least one row.")
        copies = [
            frame.assign(
                case_id=frame["case_id"] + f"-{i}",
                timestamp=frame["timestamp"] + pd.Timedelta(weeks=i),
            )
            for i in range(-(-rows // len(frame)))
        ]
        frame = pd.concat(copies, ignore_index=True).head(rows)
    # Same normalisation as the platform importer: string ids, tz-naive UTC timestamps.
    frame["case_id"] = frame["case_id"].astype(str)
    frame["activity"] = frame["activity"].astype(str)
    frame["timestamp"] = frame["timestamp"].dt.tz_localize(None)
    frame = frame.sort_values(["case_id", "timestamp"], kind="mergesort").reset_index(drop=True)
    log = SampleLog(id=str(uuid.uuid4()), frame=frame)
    _LOGS[log.id] = log
    return log


class FakeEventLog:
    """``EventLogAccessProtocol`` over a frame, written to ``events_path`` as Parquet and
    read back the way the platform reads a log's ``events.parquet``."""

    def __init__(self, frame: Any, events_path: Path) -> None:
        self.events_path = events_path
        self.active_filter: list[dict[str, Any]] | None = None
        events_path.parent.mkdir(parents=True, exist_ok=True)
        frame.to_parquet(events_path, index=False)

    async def __aenter__(self) -> FakeEventLog:
        return self

    async def __aexit__(self, *exc: object) -> None:
        return None

    async def pandas(self) -> Any:
        return _need("pandas").read_parquet(self.events_path)

    async def polars(self) -> Any:
        return _need("polars").read_parquet(self.events_path)

    async def pm4py(self) -> Any:
        renamed = (await self.pandas()).rename(
            columns={
                "case_id": "case:concept:name",
                "activity": "concept:name",
                "timestamp": "time:timestamp",
            }
        )
        return _need("pm4py.utils").format_dataframe(renamed)

    async def duckdb_fetch(
        self, sql: str, params: list[Any] | tuple[Any, ...] | None = None
    ) -> list[tuple[Any, ...]]:
        path = str(self.events_path).replace("'", "''")
        with _need("duckdb").connect() as con:
            con.execute(f"CREATE VIEW events AS SELECT * FROM read_parquet('{path}')")
            return con.execute(sql, params or []).fetchall()

    async def materialize_parquet(self) -> tuple[str, bool]:
        return str(self.events_path), False


@dataclass
class MemoryCache:
    """``ResultCacheProtocol`` in a dict (``data``)."""

    data: dict[str, Any] = field(default_factory=dict)

    async def get(self, key: str) -> Any:
        return self.data.get(key)

    async def set(self, key: str, value: Any) -> None:
        self.data[key] = value

    async def exists(self, key: str) -> bool:
        return key in self.data

    async def delete(self, key: str) -> None:
        self.data.pop(key, None)


@dataclass
class MemoryConfig:
    """``ModuleConfigProtocol`` over a plain dict."""

    value: dict[str, Any] = field(default_factory=dict)

    def get(self, key: str, default: Any = None) -> Any:
        return self.value.get(key, default)


@dataclass
class RecordingProgress:
    """``ProgressReporterProtocol`` that keeps every update in ``calls``."""

    calls: list[dict[str, Any]] = field(default_factory=list)

    async def update(
        self,
        current: float,
        message: str | None = None,
        *,
        total: float | None = None,
        stage: str | None = None,
    ) -> None:
        self.calls.append({"current": current, "message": message, "total": total, "stage": stage})


async def _no_events() -> AsyncIterator[Any]:
    for event in ():
        yield event


@dataclass
class RecordingBus:
    """``EventBusProtocol`` that keeps every emitted ``(topic, payload)`` in ``events``."""

    events: list[tuple[str, Any]] = field(default_factory=list)

    async def emit(self, topic: str, payload: Any) -> None:
        self.events.append((topic, payload))

    async def subscribe(self, *patterns: str) -> AsyncIterator[Any]:
        return _no_events()


class EmptyRegistry:
    """``ModuleRegistryProtocol`` with no other module installed."""

    def has(self, capability_or_module_id: str) -> bool:
        return False

    async def call(self, capability: str, **kwargs: Any) -> Any:
        raise ModuleError(f"Capability {capability!r} is not available in a test context.")

    def installed_modules(self) -> list[str]:
        return []


async def _run_in_thread(fn: Any, /, *args: Any, **kwargs: Any) -> Any:
    # shortcut: a thread, not the platform's process pool - nothing checks that args
    # pickle; run the module on the real platform when that matters.
    return await asyncio.to_thread(fn, *args, **kwargs)


async def build_test_context(
    log: SampleLog | None = None,
    *,
    log_id: str | None = None,
    module_id: str = "test_module",
    user_id: str = "test-user",
    config: dict[str, Any] | None = None,
    workdir: Path | None = None,
) -> ModuleContext:
    """A ``ModuleContext`` over *log* (or the ``sample_log`` with id *log_id*; default: a
    fresh ``sample_log()``). Object-centric logs are not faked: ``ctx.object_log`` is None.
    *config* is the effective module config ``ctx.config`` carries."""
    if log is None and log_id is not None:
        log = _LOGS.get(log_id)
        if log is None:
            raise LookupError(f"No test log {log_id!r} - create it with sample_log().")
    log = log or sample_log()
    root = workdir or Path(tempfile.mkdtemp(prefix="mate-test-"))
    root.mkdir(parents=True, exist_ok=True)
    logs = Path(tempfile.mkdtemp(prefix="logs-", dir=root))

    async def open_event_log(
        log_id: str, filters: list[dict[str, Any]] | None = None
    ) -> EventLogAccessProtocol:
        if filters:
            raise NotImplementedError("mate.sdk.testing does not apply event-log filters.")
        other = _LOGS.get(log_id)
        if other is None:
            raise PermissionError(f"No test log {log_id!r}.")
        return FakeEventLog(other.frame, logs / log_id / "events.parquet")

    return ModuleContext(
        log_id=log.id,
        module_id=module_id,
        user_id=user_id,
        event_log=FakeEventLog(log.frame, logs / log.id / "events.parquet"),
        bus=RecordingBus(),
        registry=EmptyRegistry(),
        cache=MemoryCache(),
        config=MemoryConfig(dict(config or {})),
        progress=RecordingProgress(),
        logger=structlog.get_logger(module_id).bind(module_id=module_id, log_id=log.id),
        workdir=root,
        run_in_process=_run_in_thread,
        open_event_log=open_event_log,
    )


def load_module(folder: Path | str) -> Module:
    """Import ``<folder>/module.py`` like the platform loader does and instantiate it.

    The folder is treated as a package (namespace ``mate_mod_<id>``), so ``module.py``
    may use relative imports.
    """
    folder = Path(folder)
    manifest = Manifest.load_yaml(folder / "manifest.yaml")
    ns = f"mate_mod_{manifest.id}"
    spec = importlib.util.spec_from_file_location(
        ns, folder / "module.py", submodule_search_locations=[str(folder)]
    )
    if spec is None or spec.loader is None:
        raise ImportError(f"Cannot import {folder / 'module.py'}.")
    mod = importlib.util.module_from_spec(spec)
    sys.modules[ns] = mod
    spec.loader.exec_module(mod)
    for value in vars(mod).values():
        if (
            isinstance(value, type)
            and issubclass(value, Module)
            and value is not Module
            and value.__module__ == ns
        ):
            if value.id != manifest.id:
                raise ModuleError(
                    f"Module class id {value.id!r} does not match manifest id {manifest.id!r}."
                )
            return value()
    raise ModuleError(f"No Module subclass found in {folder / 'module.py'}.")
