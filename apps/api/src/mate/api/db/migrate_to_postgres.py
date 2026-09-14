"""One-shot copy of the metadata database from SQLite into PostgreSQL.

Phase 1 of the Kubernetes migration: SQLite is a single-writer file, which
pins the API to one replica and forces `Recreate` rollouts (downtime on every
deploy). Everything above this module is already portable - the ORM issues no
raw SQL and none of the Alembic revisions use `batch_alter_table` or any
SQLite-only construct - so moving the data is the whole job.

Usage (the target schema must exist first)::

    DATABASE_URL=postgresql+asyncpg://mate:...@app-db:5432/mate \\
        alembic -c apps/api/alembic.ini upgrade head
    python -m mate.api.db.migrate_to_postgres --source data/metadata.db

Both URLs are normalised to sync drivers: this runs once, offline, and async
buys nothing here.

Two things this handles that a naive `INSERT … SELECT` would get wrong:

1. **Insertion order.** Rows are copied in `Base.metadata.sorted_tables`
   order, which is topologically sorted by foreign key, so a child row never
   precedes its parent.
2. **Sequences.** `event_edits.id` is the one `autoincrement=True` integer
   primary key in the schema. SQLite tracks the high-water mark implicitly;
   PostgreSQL uses a sequence that a bulk copy leaves sitting at 1, so the
   next insert collides with an existing id. Every integer identity column is
   fast-forwarded with `setval` after its table is copied.
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

from sqlalchemy import Integer, create_engine, func, insert, select
from sqlalchemy.engine import Engine, make_url

from mate.api.config import get_settings
from mate.api.db.models import Base

# Rows per executemany batch. Large enough to keep the round trips down, small
# enough that a wide table (event_edits carries JSON payloads) does not build a
# multi-hundred-megabyte parameter list.
BATCH = 1_000


def _sync_url(url: str) -> str:
    """Swap async driver tokens for their sync counterparts."""
    return url.replace("+aiosqlite", "").replace("+asyncpg", "+psycopg")


def _row_count(engine: Engine, table) -> int:  # type: ignore[no-untyped-def]
    with engine.connect() as conn:
        return int(conn.execute(select(func.count()).select_from(table)).scalar_one())


def _copy_table(src: Engine, dst: Engine, table) -> int:  # type: ignore[no-untyped-def]
    copied = 0
    with src.connect() as s_conn, dst.begin() as d_conn:
        result = s_conn.execution_options(stream_results=True).execute(select(table))
        while rows := result.fetchmany(BATCH):
            d_conn.execute(insert(table), [dict(r._mapping) for r in rows])
            copied += len(rows)
    return copied


def _reset_sequences(dst: Engine, table) -> None:  # type: ignore[no-untyped-def]
    """Fast-forward PostgreSQL identity sequences past the copied rows."""
    if dst.dialect.name != "postgresql":
        return
    for col in table.primary_key.columns:
        if not isinstance(col.type, Integer) or not col.autoincrement:
            continue
        with dst.begin() as conn:
            seq = conn.exec_driver_sql(
                "SELECT pg_get_serial_sequence(%s, %s)", (table.name, col.name)
            ).scalar()
            if not seq:
                continue
            conn.exec_driver_sql(
                f"SELECT setval('{seq}', COALESCE((SELECT MAX({col.name}) FROM {table.name}), 1))"
            )
            print(f"    sequence {seq} fast-forwarded")


def migrate(source_url: str, target_url: str, *, force: bool = False) -> int:
    src = create_engine(_sync_url(source_url), future=True)
    dst = create_engine(_sync_url(target_url), future=True)

    tables = list(Base.metadata.sorted_tables)

    # Refuse to double-import. Re-running after a partial failure needs a
    # deliberate --force plus a truncated target, not a silent second copy that
    # would violate primary keys halfway through.
    if not force:
        occupied = [t.name for t in tables if _row_count(dst, t) > 0]
        if occupied:
            print(
                "✗ target is not empty: " + ", ".join(occupied[:5])
                + ("…" if len(occupied) > 5 else "")
                + "\n  Re-run with --force once you have truncated it, or point at a fresh database.",
                file=sys.stderr,
            )
            return 2

    total = 0
    for table in tables:
        expected = _row_count(src, table)
        if expected == 0:
            print(f"  {table.name}: empty")
            continue
        copied = _copy_table(src, dst, table)
        actual = _row_count(dst, table)
        status = "ok" if actual == expected else f"MISMATCH (source {expected}, target {actual})"
        print(f"  {table.name}: {copied} rows -> {status}")
        if actual != expected:
            print("✗ row counts differ; aborting before the next table.", file=sys.stderr)
            return 1
        _reset_sequences(dst, table)
        total += copied

    print(f"\n✓ {total} rows across {len(tables)} tables")
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawTextHelpFormatter)
    parser.add_argument(
        "--source",
        default="data/metadata.db",
        help="SQLite file, or a full SQLAlchemy URL (default: data/metadata.db)",
    )
    parser.add_argument(
        "--target",
        default=None,
        help="Target URL (default: DATABASE_URL from the environment)",
    )
    parser.add_argument(
        "--force",
        action="store_true",
        help="Copy even though the target already holds rows",
    )
    args = parser.parse_args(argv if argv is not None else sys.argv[1:])

    source = args.source
    if "://" not in source:
        path = Path(source).resolve()
        if not path.exists():
            print(f"✗ no such file: {path}", file=sys.stderr)
            return 2
        source = f"sqlite:///{path}"

    target = args.target or get_settings().database_url
    if make_url(_sync_url(target)).get_backend_name() == "sqlite":
        print("✗ target is SQLite - set DATABASE_URL to the PostgreSQL URL.", file=sys.stderr)
        return 2

    print(f"source: {source}\ntarget: {make_url(_sync_url(target)).render_as_string(hide_password=True)}\n")
    return migrate(source, target, force=args.force)


if __name__ == "__main__":  # pragma: no cover - CLI
    raise SystemExit(main())
