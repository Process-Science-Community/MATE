"""Test helpers for module authors: a fake ``ModuleContext`` over a small log (§10).

    from mate.sdk.testing import build_test_context, load_module, sample_log

    def test_summary() -> None:
        async def check() -> None:
            ctx = await build_test_context(sample_log(rows=1000), module_id="my_module")
            out = await MyModule().summary(ctx)
            assert out["cases"] > 0

        asyncio.run(check())

Every collaborator is an in-memory stand-in that satisfies the Protocols in
``mate.sdk.context``: ``ctx.event_log`` holds the log the way the importer stores it
(string ids, tz-naive UTC timestamps), ``ctx.cache`` round-trips values like the
platform's file cache (Parquet / JSON), ``ctx.progress`` / ``ctx.bus`` record their calls
(``RecordingProgress.calls``, ``RecordingBus.events``), ``ctx.registry`` knows no
capabilities, ``ctx.run_in_process`` runs the function in a thread and ``ctx.inputs``
serves the values passed as ``inputs={port: value}``. Assert on log lines with
``structlog.testing.capture_logs()``.

pandas, pyarrow and duckdb come from the platform's ``inherit`` set, not from mate-sdk,
so they are imported lazily; polars and pm4py only when a handler asks for them.
"""

from __future__ import annotations

import asyncio
import importlib
import importlib.util
import io
import json
import sys
import tempfile
import uuid
from collections.abc import AsyncIterator
from dataclasses import dataclass, field
from importlib import resources
from pathlib import Path
from typing import Any

import structlog

from mate.sdk.artifacts import ArtifactRef
from mate.sdk.context import EventLogAccessProtocol, ModuleContext
from mate.sdk.errors import ModuleError, ReadOnlyContextError
from mate.sdk.manifest import Manifest
from mate.sdk.module import Module

__all__ = [
    "EmptyRegistry",
    "FakeEventLog",
    "MemoryCache",
    "MemoryConfig",
    "MemoryInputs",
    "RecordingBus",
    "RecordingProgress",
    "SampleLog",
    "build_test_context",
    "load_log",
    "load_module",
    "sample_log",
]

REQUIRED_COLUMNS = frozenset({"case_id", "activity", "timestamp"})

# Logs created by `sample_log` / `load_log`, so `build_test_context(log_id=...)`
# and `ctx.open_event_log(...)` can find them by id like the platform does.
_LOGS: dict[str, SampleLog] = {}


def _need(name: str) -> Any:
    try:
        return importlib.import_module(name)
    except ModuleNotFoundError as exc:
        raise RuntimeError(
            f"mate.sdk.testing needs `{name}`, which the platform provides but mate-sdk "
            "does not install - run inside the platform workspace (`uv run ...`) or "
            f"install `{name}` next to mate-sdk."
        ) from exc


@dataclass(frozen=True)
class SampleLog:
    """A case-centric log for tests: an id plus a pandas frame in canonical columns."""

    id: str
    frame: Any


def _to_datetime(pd: Any, values: Any) -> Any:
    """UTC timestamps, surviving mixed formats/precisions - a copy of the platform's
    ``mate.api.ingest.parquet_coerce.to_datetime_robust`` (the SDK can't import it)."""
    parsed = pd.to_datetime(values, errors="coerce", utc=True)
    if (parsed.isna() & values.notna()).any():
        try:
            retry = pd.to_datetime(values, format="mixed", errors="coerce", utc=True)
        except (ValueError, TypeError):
            return parsed
        if retry.notna().sum() > parsed.notna().sum():
            return retry
    return parsed


def _register(frame: Any) -> SampleLog:
    # Same normalisation as the platform importer (`mate.api.ingest.dispatch`): string
    # ids, tz-naive UTC timestamps, rows ordered by case then time, columns kept as-is.
    pd = _need("pandas")
    frame["case_id"] = frame["case_id"].astype(str)
    frame["activity"] = frame["activity"].astype(str)
    for column in ("timestamp", "end_timestamp"):
        if column in frame.columns:
            frame[column] = _to_datetime(pd, frame[column]).dt.tz_localize(None)
    frame = frame.dropna(subset=["timestamp"])  # the importer drops unparseable rows too
    frame = frame.sort_values(["case_id", "timestamp"], kind="mergesort").reset_index(drop=True)
    log = SampleLog(id=str(uuid.uuid4()), frame=frame)
    _LOGS[log.id] = log
    return log


def sample_log(rows: int | None = None) -> SampleLog:
    """The bundled sample log: 3 cases, 9 events, ``case_id/activity/timestamp/resource``.

    ``rows=N`` tiles it to exactly N events, each copy with fresh case ids and
    shifted one day later.
    """
    pd = _need("pandas")
    text = resources.files("mate.sdk").joinpath("data/sample.csv").read_text(encoding="utf-8")
    frame = pd.read_csv(io.StringIO(text))
    if rows is not None:
        if rows < 1:
            raise ValueError("sample_log(rows=...) needs at least one row.")
        copies = [
            frame.assign(
                case_id=frame["case_id"] + f"-{i}",
                timestamp=pd.to_datetime(frame["timestamp"], utc=True) + pd.Timedelta(days=i),
            )
            for i in range(-(-rows // len(frame)))
        ]
        frame = pd.concat(copies, ignore_index=True).head(rows)
    return _register(frame)


def load_log(path: Path | str) -> SampleLog:
    """Load a ``.csv`` or ``.parquet`` log that already uses the canonical column names."""
    path = Path(path)
    pd = _need("pandas")
    if path.suffix == ".csv":
        frame = pd.read_csv(path)
    elif path.suffix == ".parquet":
        frame = pd.read_parquet(path)
    else:
        raise ValueError(
            f"{path.name}: load_log reads .csv or .parquet only - import XES through the "
            "platform and export the events as CSV."
        )
    missing = sorted(REQUIRED_COLUMNS - set(frame.columns))
    if missing:
        raise ValueError(f"{path.name} is missing the column(s) {', '.join(missing)}.")
    return _register(frame)


class FakeEventLog:
    """``EventLogAccessProtocol`` over a frame, written to ``events_path`` and read back
    the way the platform writes and reads a log's ``events.parquet``."""

    def __init__(self, frame: Any, events_path: Path) -> None:
        self.events_path = events_path
        self.active_filter: list[dict[str, Any]] | None = None
        events_path.parent.mkdir(parents=True, exist_ok=True)
        frame.to_parquet(events_path, index=False, engine="pyarrow", compression="zstd")

    async def __aenter__(self) -> FakeEventLog:
        return self

    async def __aexit__(self, *exc: object) -> None:
        return None

    def _table(self) -> Any:
        return _need("pyarrow.parquet").read_table(self.events_path)

    async def pandas(self) -> Any:
        return self._table().to_pandas()

    async def polars(self) -> Any:
        return _need("polars").from_arrow(self._table())

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
        with _need("duckdb").connect() as con:
            con.execute(
                f"CREATE VIEW events AS SELECT * FROM read_parquet('{_sql_path(self.events_path)}')"
            )
            return con.execute(sql, params or []).fetchall()

    async def materialize_parquet(self) -> tuple[str, bool]:
        return str(self.events_path), False


def _sql_path(path: Path) -> str:
    return str(path).replace("'", "''")


def _check_key(key: str) -> None:
    # The platform's ResultCache checks the file name `<key>.<ext>`, so it rejects a
    # trailing dot as well as `/` and `..`.
    if "/" in key or ".." in f"{key}.":
        raise ValueError(f"Invalid cache key {key!r} - must be a flat name.")


@dataclass
class MemoryCache:
    """``ResultCacheProtocol`` in a dict (``data``), with the platform cache's key rules
    and storage round trips: DataFrames through Parquet, bytes as-is, the rest JSON.
    ``read_only`` refuses writes to reserved ``__*`` keys like a shared viewer's cache."""

    data: dict[str, Any] = field(default_factory=dict)
    read_only: bool = False

    def _check_writable(self, key: str) -> None:
        _check_key(key)
        if self.read_only and key.startswith("__"):
            raise ReadOnlyContextError(f"Cache entry {key!r} belongs to the log's owner.")

    async def get(self, key: str) -> Any:
        _check_key(key)
        return self.data.get(key)

    async def set(self, key: str, value: Any) -> None:
        self._check_writable(key)
        pd = sys.modules.get("pandas")
        if pd is not None and isinstance(value, pd.DataFrame):
            buf = io.BytesIO()
            value.to_parquet(buf, engine="pyarrow", compression="zstd", index=False)
            value = pd.read_parquet(io.BytesIO(buf.getvalue()))
        elif isinstance(value, bytes | bytearray):
            value = bytes(value)
        else:
            # Read back what a route would (tuples become lists, numpy scalars strings).
            value = json.loads(json.dumps(value, default=str))
        self.data[key] = value

    async def exists(self, key: str) -> bool:
        _check_key(key)
        return key in self.data

    async def delete(self, key: str) -> None:
        self._check_writable(key)
        self.data.pop(key, None)


@dataclass
class MemoryConfig:
    """``ModuleConfigProtocol`` over a plain dict."""

    value: dict[str, Any] = field(default_factory=dict)

    def get(self, key: str, default: Any = None) -> Any:
        return self.value.get(key, default)


@dataclass
class MemoryInputs:
    """``InputsProtocol`` over ``values`` (``{port: value}``): every given port is bound
    to a ``test_input`` producer and returns its value as-is."""

    values: dict[str, Any] = field(default_factory=dict)
    log_id: str = ""

    @property
    def ports(self) -> list[str]:
        return list(self.values)

    def ref(self, port: str) -> ArtifactRef | None:
        return ArtifactRef(self.log_id, "test_input", port) if port in self.values else None

    async def get(self, port: str) -> Any | None:
        return self.values.get(port)


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
    # shortcut: a thread, not the platform's process pool - nothing checks that
    # args pickle; run the real platform when that matters.
    return await asyncio.to_thread(fn, *args, **kwargs)


async def build_test_context(
    log: SampleLog | None = None,
    *,
    log_id: str | None = None,
    module_id: str = "test_module",
    user_id: str = "test-user",
    config: dict[str, Any] | None = None,
    workdir: Path | None = None,
    inputs: dict[str, Any] | None = None,
    read_only: bool = False,
) -> ModuleContext:
    """A ``ModuleContext`` over *log* (or the log created with id *log_id*; default
    ``sample_log()``). Object-centric logs are not faked: ``ctx.object_log`` is None.
    *inputs* feeds ``ctx.inputs``: ``{port: value}``, the value a producer would cache.
    *config* is the effective config as ``ctx.config`` carries it: module-wide values
    with any per-log (``ui.scope: dataset``) overrides already merged in.
    *read_only* builds a shared-dashboard viewer's context: ``ctx.read_only`` is True
    and ``ctx.cache`` refuses writes to reserved ``__*`` keys.
    """
    if log is None and log_id is not None:
        log = _LOGS.get(log_id)
        if log is None:
            raise LookupError(
                f"No test log {log_id!r} - create it with sample_log() or load_log()."
            )
    log = log or sample_log()
    root = workdir or Path(tempfile.mkdtemp(prefix="mate-test-"))
    root.mkdir(parents=True, exist_ok=True)
    # Per context, so contexts sharing a workdir never overwrite each other's logs.
    logs = Path(tempfile.mkdtemp(prefix="logs-", dir=root))

    # Parameter names match OpenEventLogProtocol (callers may pass them by keyword).
    async def open_event_log(
        log_id: str, filters: list[dict[str, Any]] | None = None
    ) -> EventLogAccessProtocol:
        if filters:
            raise NotImplementedError("mate.sdk.testing does not apply event-log filters.")
        other = _LOGS.get(log_id)
        if other is None:
            raise LookupError(f"No test log {log_id!r}.")
        return FakeEventLog(other.frame, logs / log_id / "events.parquet")

    return ModuleContext(
        log_id=log.id,
        module_id=module_id,
        user_id=user_id,
        event_log=FakeEventLog(log.frame, logs / log.id / "events.parquet"),
        bus=RecordingBus(),
        registry=EmptyRegistry(),
        cache=MemoryCache(read_only=read_only),
        config=MemoryConfig(dict(config or {})),
        progress=RecordingProgress(),
        logger=structlog.get_logger(module_id).bind(module_id=module_id, log_id=log.id),
        workdir=root,
        run_in_process=_run_in_thread,
        open_event_log=open_event_log,
        inputs=MemoryInputs(dict(inputs or {}), log.id),
        read_only=read_only,
    )


def load_module(folder: Path | str) -> Module:
    """Import ``<folder>/module.py`` the way the platform loader does and instantiate it.

    The folder is treated as a package, so ``module.py`` may use relative imports.
    """
    folder = Path(folder)
    manifest = Manifest.load_yaml(folder / "manifest.yaml")
    # Same namespace as the platform's `mate.api.modules.finder.module_namespace`.
    ns = f"mate_mod_{manifest.id.replace('-', '_')}"
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
