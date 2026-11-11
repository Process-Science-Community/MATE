"""Alembic 0019 (``dashboards.module_id``, drops ``module_layouts``) and dataset
route rules. Module boards themselves were removed in favour of ``layout.json``;
the column stays, reserved and always NULL, so the migration and models agree.
"""

from __future__ import annotations

import importlib.util
from pathlib import Path
from typing import Any

import pytest
import sqlalchemy as sa
from alembic.migration import MigrationContext
from alembic.operations import Operations
from sqlalchemy.exc import IntegrityError

from mate.sdk.errors import ModuleManifestError
from mate.sdk.manifest import Manifest


def test_dataset_may_be_cache_only() -> None:
    # The web reads a cache-only dataset by artifact ref (`GET /artifacts/content?ref=`).
    m = Manifest.model_validate(
        {
            "id": "bm",
            "name": "BM",
            "version": "1.0.0",
            "category": "other",
            "datasets": [{"id": "counts", "shape": "table", "cache_key": "counts"}],
        }
    )
    assert m.datasets[0].route is None


@pytest.mark.parametrize("route", ["", "counts"])
def test_dataset_route_must_start_with_a_slash(route: str) -> None:
    with pytest.raises(ModuleManifestError, match="must start with '/'"):
        Manifest.model_validate(
            {
                "id": "bm",
                "name": "BM",
                "version": "1.0.0",
                "category": "other",
                "datasets": [{"id": "counts", "shape": "table", "route": route}],
            }
        )


# --------------------------------------------------------------------------- #
# Migration 0019
# --------------------------------------------------------------------------- #


def _load_migration() -> Any:
    path = Path(__file__).resolve().parents[1] / "alembic" / "versions" / "0019_module_boards.py"
    spec = importlib.util.spec_from_file_location("mig_0019", path)
    assert spec is not None and spec.loader is not None
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def _run(fn: str, mig: Any, monkeypatch: pytest.MonkeyPatch, conn: sa.Connection) -> None:
    monkeypatch.setattr(mig, "op", Operations(MigrationContext.configure(conn)))
    getattr(mig, fn)()


def test_migration_up_down_up(monkeypatch: pytest.MonkeyPatch) -> None:
    mig = _load_migration()
    eng = sa.create_engine("sqlite://")
    with eng.connect() as conn:
        conn.exec_driver_sql(
            "CREATE TABLE dashboards (id VARCHAR(36) PRIMARY KEY, user_id VARCHAR(36) NOT NULL,"
            " name VARCHAR(255) NOT NULL, event_log_id VARCHAR(36), layout_json JSON NOT NULL,"
            " created_at DATETIME NOT NULL)"
        )
        conn.exec_driver_sql(
            "CREATE INDEX ix_dashboards_user_created_at ON dashboards (user_id, created_at)"
        )
        conn.exec_driver_sql(
            "CREATE TABLE module_layouts (user_id VARCHAR(36), log_id VARCHAR(36),"
            " module_id VARCHAR(64), layout_json JSON NOT NULL, updated_at DATETIME NOT NULL,"
            " PRIMARY KEY (user_id, log_id, module_id))"
        )
        conn.exec_driver_sql(
            "INSERT INTO dashboards VALUES ('plain', 'u', 'Plain', 'l', '{}', datetime('now'))"
        )

        def state() -> tuple[set[str], set[str], set[str]]:
            insp = sa.inspect(conn)
            tables = set(insp.get_table_names())
            cols = {c["name"] for c in insp.get_columns("dashboards")}
            idx = {str(i["name"]) for i in insp.get_indexes("dashboards")}
            return tables, cols, idx

        _run("upgrade", mig, monkeypatch, conn)
        _run("upgrade", mig, monkeypatch, conn)  # idempotent
        tables, cols, idx = state()
        assert "module_layouts" not in tables
        assert "module_id" in cols
        assert {"ix_dashboards_user_module_log", "ix_dashboards_user_created_at"} <= idx

        board = (
            "INSERT INTO dashboards (id, user_id, name, event_log_id, layout_json, created_at,"
            " module_id) VALUES (:id, 'u', 'Board', 'l', '{}', datetime('now'), 'm')"
        )
        conn.execute(sa.text(board), {"id": "board"})
        with pytest.raises(IntegrityError):
            conn.execute(sa.text(board), {"id": "board-2"})

        _run("downgrade", mig, monkeypatch, conn)
        _run("downgrade", mig, monkeypatch, conn)  # idempotent
        tables, cols, idx = state()
        assert "module_layouts" in tables
        assert "module_id" not in cols
        assert "ix_dashboards_user_created_at" in idx
        ids = {r[0] for r in conn.exec_driver_sql("SELECT id FROM dashboards")}
        assert ids == {"plain"}  # the module board went with its column

        _run("upgrade", mig, monkeypatch, conn)
        tables, cols, _ = state()
        assert "module_layouts" not in tables and "module_id" in cols
