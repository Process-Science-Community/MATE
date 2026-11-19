"""0018_hub_datasets - the dataset-hub schema and its guarded downgrade.

Drives the real ``upgrade``/``downgrade`` against an in-memory SQLite seeded with
the 0017-era tables the migration touches (same harness as
``test_card_migration.py``).
"""

from __future__ import annotations

import importlib.util
from pathlib import Path

import pytest
import sqlalchemy as sa
from alembic.migration import MigrationContext
from alembic.operations import Operations


def _load_migration():
    path = Path(__file__).resolve().parents[1] / "alembic" / "versions" / "0018_hub_datasets.py"
    spec = importlib.util.spec_from_file_location("mig_0018", path)
    assert spec is not None and spec.loader is not None
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def _run(fn: str, mig, monkeypatch: pytest.MonkeyPatch, conn) -> None:
    ops = Operations(MigrationContext.configure(conn))
    monkeypatch.setattr(mig, "op", ops)
    getattr(mig, fn)()


def _seed_0017(conn) -> None:
    conn.exec_driver_sql("CREATE TABLE users (id VARCHAR(36) PRIMARY KEY)")
    conn.exec_driver_sql(
        "CREATE TABLE teams (id VARCHAR(36) PRIMARY KEY, name VARCHAR(255) NOT NULL,"
        " created_at DATETIME NOT NULL, deleted_at DATETIME)"
    )
    conn.exec_driver_sql(
        "CREATE TABLE process_logs (id VARCHAR(36) PRIMARY KEY,"
        " user_id VARCHAR(36) NOT NULL REFERENCES users(id) ON DELETE CASCADE,"
        " name VARCHAR(255) NOT NULL, status VARCHAR(16) NOT NULL,"
        " data_revision INTEGER NOT NULL DEFAULT 0, created_at DATETIME NOT NULL)"
    )
    conn.exec_driver_sql(
        "CREATE INDEX ix_process_logs_user_status ON process_logs (user_id, status)"
    )
    conn.exec_driver_sql("INSERT INTO users (id) VALUES ('u1')")
    conn.exec_driver_sql(
        "INSERT INTO process_logs (id, user_id, name, status, created_at)"
        " VALUES ('l1', 'u1', 'kept', 'ready', '2026-01-01')"
    )


def test_upgrade_creates_hub_tables_and_lineage_columns(monkeypatch: pytest.MonkeyPatch) -> None:
    mig = _load_migration()
    eng = sa.create_engine("sqlite://")
    with eng.connect() as conn:
        _seed_0017(conn)
        _run("upgrade", mig, monkeypatch, conn)
        # Idempotent: a half-applied boot re-runs it.
        _run("upgrade", mig, monkeypatch, conn)

        insp = sa.inspect(conn)
        assert {"hub_datasets", "hub_dataset_revisions"} <= set(insp.get_table_names())
        uniques = {u["name"] for u in insp.get_unique_constraints("hub_datasets")}
        assert "uq_hub_datasets_slug" in uniques
        log_cols = {c["name"] for c in insp.get_columns("process_logs")}
        assert {"dataset_revision_id", "license_snapshot"} <= log_cols
        idx = {i["name"] for i in insp.get_indexes("process_logs")}
        assert {"ix_process_logs_user_dataset_revision", "ix_process_logs_user_status"} <= idx
        # The batch recreate kept the existing row.
        assert conn.execute(sa.text("SELECT name FROM process_logs")).scalar_one() == "kept"
        # The slug is unique.
        ins = sa.text(
            "INSERT INTO hub_datasets (id, namespace, name, title, tags, created_at, updated_at)"
            " VALUES (:id, 'ns', 'n', 't', '[]', '2026-01-01', '2026-01-01')"
        )
        conn.execute(ins, {"id": "d1"})
        with pytest.raises(sa.exc.IntegrityError):
            conn.execute(ins, {"id": "d2"})


def test_downgrade_refuses_while_derived_logs_exist(monkeypatch: pytest.MonkeyPatch) -> None:
    mig = _load_migration()
    eng = sa.create_engine("sqlite://")
    with eng.connect() as conn:
        _seed_0017(conn)
        _run("upgrade", mig, monkeypatch, conn)
        conn.exec_driver_sql(
            "INSERT INTO hub_datasets (id, namespace, name, title, tags, created_at, updated_at)"
            " VALUES ('d1', 'ns', 'n', 't', '[]', '2026-01-01', '2026-01-01')"
        )
        conn.exec_driver_sql(
            "INSERT INTO hub_dataset_revisions (id, dataset_id, number, events_sha256, stats,"
            " preview, canonical_results, created_at)"
            " VALUES ('r1', 'd1', 1, 'x', '{}', '{}', '{}', '2026-01-01')"
        )
        conn.exec_driver_sql(
            "INSERT INTO process_logs (id, user_id, name, status, created_at, dataset_revision_id)"
            " VALUES ('l2', 'u1', 'copy', 'ready', '2026-01-01', 'r1')"
        )

        with pytest.raises(RuntimeError, match="derived logs exist"):
            _run("downgrade", mig, monkeypatch, conn)
        assert "hub_datasets" in sa.inspect(conn).get_table_names()

        conn.exec_driver_sql("UPDATE process_logs SET dataset_revision_id = NULL")
        _run("downgrade", mig, monkeypatch, conn)
        insp = sa.inspect(conn)
        assert not {"hub_datasets", "hub_dataset_revisions"} & set(insp.get_table_names())
        log_cols = {c["name"] for c in insp.get_columns("process_logs")}
        assert not {"dataset_revision_id", "license_snapshot"} & log_cols
        assert "ix_process_logs_user_dataset_revision" not in {
            i["name"] for i in insp.get_indexes("process_logs")
        }
        assert conn.execute(sa.text("SELECT COUNT(*) FROM process_logs")).scalar_one() == 2
