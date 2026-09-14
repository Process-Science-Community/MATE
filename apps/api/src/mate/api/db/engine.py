"""SQLAlchemy async engine + sessionmaker.

PostgreSQL in production; SQLite is still accepted so `make dev` and the
test suite can run without a database server. The dialect is derived from
`DATABASE_URL` - the SQLite PRAGMAs below are applied only when it is one,
since `PRAGMA` is a syntax error on PostgreSQL.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from sqlalchemy import event
from sqlalchemy.engine import make_url
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from mate.api.config import get_settings

if TYPE_CHECKING:
    from sqlalchemy.ext.asyncio import AsyncEngine

_engine: AsyncEngine | None = None
_sessionmaker: async_sessionmaker[AsyncSession] | None = None


def _enable_wal_and_fk(dbapi_conn, _record) -> None:  # pragma: no cover - SQLAlchemy event hook
    cur = dbapi_conn.cursor()
    cur.execute("PRAGMA journal_mode=WAL")
    cur.execute("PRAGMA synchronous=NORMAL")
    cur.execute("PRAGMA foreign_keys=ON")
    cur.execute("PRAGMA busy_timeout=5000")
    cur.close()


def get_engine() -> AsyncEngine:
    global _engine
    if _engine is None:
        settings = get_settings()
        url = make_url(settings.database_url)
        is_sqlite = url.get_backend_name() == "sqlite"
        # SQLite is a single file with one writer - a pool is meaningless. On
        # PostgreSQL each replica keeps its own pool, so size it modestly:
        # api + worker pods multiply, and the server's max_connections is shared.
        pool_kwargs: dict[str, object] = (
            {}
            if is_sqlite
            else {
                "pool_size": settings.db_pool_size,
                "max_overflow": settings.db_max_overflow,
                # Recycle below any proxy/server idle timeout so a pooled
                # connection is never handed out already dead.
                "pool_recycle": 1800,
            }
        )
        _engine = create_async_engine(
            settings.database_url,
            future=True,
            echo=False,
            pool_pre_ping=True,
            **pool_kwargs,
        )
        if is_sqlite:
            # SQLAlchemy fires `connect` against the underlying DBAPI connection
            # (aiosqlite exposes `sync_connection`), so PRAGMAs apply on every
            # new connection. PostgreSQL needs none of this: WAL, durability and
            # FK enforcement are server-side defaults.
            event.listen(_engine.sync_engine, "connect", _enable_wal_and_fk)
    return _engine


def get_sessionmaker() -> async_sessionmaker[AsyncSession]:
    global _sessionmaker
    if _sessionmaker is None:
        _sessionmaker = async_sessionmaker(
            get_engine(),
            class_=AsyncSession,
            expire_on_commit=False,
        )
    return _sessionmaker


async def dispose_engine() -> None:
    global _engine, _sessionmaker
    if _engine is not None:
        await _engine.dispose()
    _engine = None
    _sessionmaker = None
