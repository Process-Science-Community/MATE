"""DUCKDB_THREADS / DUCKDB_MEMORY_LIMIT reach every DuckDB connection the API opens.

Each ``duckdb.connect()`` is its own instance with DuckDB's defaults (a thread
per core, ~80% of host RAM). The per-request ``EventLogAccess`` connection
behind the Events/Variants tabs used to skip the configured limits, so a few
concurrent queries on a large log could exhaust the API container.
"""

from __future__ import annotations

import uuid
from collections.abc import Iterator

import duckdb
import pytest

from .conftest import TEST_USER_ID


@pytest.fixture
def _tight_limits(monkeypatch: pytest.MonkeyPatch) -> Iterator[None]:
    from mate.api import config as cfg

    monkeypatch.setenv("DUCKDB_MEMORY_LIMIT", "512MB")
    monkeypatch.setenv("DUCKDB_THREADS", "1")
    cfg.get_settings.cache_clear()
    yield
    monkeypatch.undo()
    cfg.get_settings.cache_clear()


def _settings(conn: duckdb.DuckDBPyConnection) -> tuple[str, int, str]:
    row = conn.execute(
        "SELECT current_setting('memory_limit'), current_setting('threads'),"
        " current_setting('temp_directory')"
    ).fetchone()
    assert row is not None
    return str(row[0]), int(row[1]), str(row[2])


def test_apply_resource_limits(_tight_limits: None) -> None:
    from mate.api.config import get_settings
    from mate.api.duckdb.pool import apply_resource_limits

    conn = duckdb.connect(":memory:")
    apply_resource_limits(conn)
    mem, threads, tmp = _settings(conn)
    assert threads == 1
    assert mem.endswith("MiB") and float(mem.split()[0]) < 512, mem  # 512MB = 488.2 MiB
    assert tmp == str(get_settings().data_dir / "duckdb-tmp")


@pytest.mark.asyncio
async def test_event_log_access_connection_is_limited(_tight_limits: None) -> None:
    from mate.api.ingest.storage import log_paths
    from mate.api.modules.event_log_access import EventLogAccess

    log_id = str(uuid.uuid4())
    paths = log_paths(log_id, TEST_USER_ID)
    paths.root.mkdir(parents=True, exist_ok=True)
    events = str(paths.events).replace("'", "''")
    duckdb.connect(":memory:").execute(
        "COPY (SELECT 'c1' AS case_id, 'a' AS activity, TIMESTAMP '2026-01-01' AS timestamp)"
        f" TO '{events}' (FORMAT parquet)"
    )

    async with EventLogAccess(log_id, TEST_USER_ID) as access:
        (mem, threads, _tmp), *_ = await access.duckdb_fetch(
            "SELECT current_setting('memory_limit'), current_setting('threads'),"
            " current_setting('temp_directory')"
        )
        assert int(threads) == 1
        assert str(mem).endswith("MiB") and float(str(mem).split()[0]) < 512, mem
        # The view over the log still works under the limits.
        assert await access.duckdb_fetch("SELECT count(*) FROM events") == [(1,)]


@pytest.mark.asyncio
async def test_cancelled_query_does_not_strand_its_worker_thread() -> None:
    """A job cancelled mid-query (user cancel, shutdown) leaves `EventLogAccess`
    while its DuckDB query still runs on a worker thread. Closing the connection
    from the event loop under that thread used to leave it blocked forever: the
    loop's executor could not join it (300 s at shutdown) and prod leaked a
    thread per cancel. The exit must stop the query and let the thread go."""
    import asyncio

    from mate.api.ingest.storage import log_paths
    from mate.api.modules.event_log_access import EventLogAccess

    log_id = str(uuid.uuid4())
    paths = log_paths(log_id, TEST_USER_ID)
    paths.root.mkdir(parents=True, exist_ok=True)
    events = str(paths.events).replace("'", "''")
    duckdb.connect(":memory:").execute(
        "COPY (SELECT 'c1' AS case_id, 'a' AS activity, TIMESTAMP '2026-01-01' AS timestamp)"
        f" TO '{events}' (FORMAT parquet)"
    )

    access = EventLogAccess(log_id, TEST_USER_ID)
    await access.__aenter__()
    # A cross join of 10^12 rows: runs for minutes unless interrupted.
    query = asyncio.create_task(
        access.duckdb_fetch("SELECT sum(a.range * b.range) FROM range(1000000) a, range(1000000) b")
    )
    loop = asyncio.get_running_loop()
    deadline = loop.time() + 10
    while access._conn is None and loop.time() < deadline:  # pyright: ignore[reportPrivateUsage]
        await asyncio.sleep(0.01)
    assert access._conn is not None, "query never started"  # pyright: ignore[reportPrivateUsage]

    query.cancel()
    await asyncio.wait_for(access.__aexit__(None, None, None), timeout=10)
    # Every worker thread must be free again: this is the join that hung.
    await asyncio.wait_for(loop.shutdown_default_executor(), timeout=10)
