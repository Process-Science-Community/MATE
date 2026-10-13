"""Dialect-aware ``INSERT`` for upserts.

``ON CONFLICT`` is dialect-specific in SQLAlchemy: a statement built with
``sqlalchemy.dialects.sqlite.insert`` carries SQLite's conflict clause, which
the PostgreSQL compiler can't render (``'OnConflictDoNothing' object has no
attribute 'constraint_target'``). Both dialects expose the same
``on_conflict_do_nothing`` / ``on_conflict_do_update(index_elements, set_)`` /
``excluded`` API, so callers pick the constructor from the session's bind and
stay otherwise unchanged.
"""

from __future__ import annotations

from typing import Any

from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.dialects.sqlite import insert as sqlite_insert
from sqlalchemy.ext.asyncio import AsyncSession


def upsert_insert(session: AsyncSession, table: Any) -> Any:
    """Return an ``INSERT`` for *table* that supports ``ON CONFLICT`` on the
    session's database (PostgreSQL in prod, SQLite in dev/tests)."""
    if session.get_bind().dialect.name == "postgresql":
        return pg_insert(table)
    return sqlite_insert(table)
