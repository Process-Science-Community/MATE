"""Per-thread DuckDB connection pool.

DuckDB is single-threaded per connection (§9). We hand out one connection per
worker thread via `contextvars`.

For phase 3, the pool is initialised but only consumed by ad-hoc DuckDB
queries inside the ingest path. Module-author access via `EventLogAccess`
arrives in phase 5.
"""

from __future__ import annotations

import asyncio
import contextlib
import contextvars
import threading
from collections.abc import Callable
from typing import TypeVar

import duckdb

from mate.api.config import get_settings

T = TypeVar("T")

_thread_conn: contextvars.ContextVar[duckdb.DuckDBPyConnection | None] = contextvars.ContextVar(
    "_duckdb_conn", default=None
)


class DuckDBPool:
    """Lazy thread-local in-memory DuckDB connections."""

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._conns: list[duckdb.DuckDBPyConnection] = []

    def _new_connection(self) -> duckdb.DuckDBPyConnection:
        conn = duckdb.connect(database=":memory:")
        settings = get_settings()

        # Tuning (§9). Each statement is best-effort - older/newer DuckDB builds
        # differ on which PRAGMAs exist, and a missing one must never break the
        # connection. Object cache keeps Parquet footers between queries (big win
        # when many widgets scan the same file); unordered scans cut memory/time
        # for aggregations; the optional thread cap curbs core oversubscription
        # when many widget queries run at once.
        def _try(sql: str) -> None:
            with contextlib.suppress(duckdb.Error):
                conn.execute(sql)

        if settings.duckdb_threads > 0:
            _try(f"PRAGMA threads={int(settings.duckdb_threads)}")
        if settings.duckdb_memory_limit:
            _try(f"PRAGMA memory_limit='{settings.duckdb_memory_limit}'")
        _try("PRAGMA enable_object_cache=true")
        _try("SET preserve_insertion_order=false")

        with self._lock:
            self._conns.append(conn)
        return conn

    def _conn(self) -> duckdb.DuckDBPyConnection:
        conn = _thread_conn.get()
        if conn is None:
            conn = self._new_connection()
            _thread_conn.set(conn)
        return conn

    def execute(self, sql: str, params: list | tuple | None = None) -> list[tuple]:
        cur = self._conn().execute(sql, params or [])
        return cur.fetchall()

    def run_in_thread(self, fn: Callable[[duckdb.DuckDBPyConnection], T]) -> asyncio.Future[T]:
        async def _await() -> T:
            return await asyncio.to_thread(lambda: fn(self._conn()))

        return asyncio.ensure_future(_await())

    def close_all(self) -> None:
        with self._lock:
            for conn in self._conns:
                with contextlib.suppress(Exception):
                    conn.close()
            self._conns.clear()


_pool: DuckDBPool | None = None


def get_duckdb_pool() -> DuckDBPool:
    global _pool
    if _pool is None:
        _pool = DuckDBPool()
    return _pool
