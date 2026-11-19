"""0017_rename_module_outputs - stored viz-card refs move to ``output_ref``.

Drives the real ``upgrade``/``downgrade`` against an in-memory SQLite by
monkeypatching the migration's ``op`` proxy onto an Operations bound to our own
connection - the same harness as ``test_dashboard_grid_migration.py``.
"""

from __future__ import annotations

import importlib.util
import json
from pathlib import Path
from typing import Any

import pytest
import sqlalchemy as sa
from alembic.migration import MigrationContext
from alembic.operations import Operations

from mate.api.schemas.dashboards import DashboardItem


def _load_migration():
    path = (
        Path(__file__).resolve().parents[1]
        / "alembic"
        / "versions"
        / "0017_rename_module_outputs.py"
    )
    spec = importlib.util.spec_from_file_location("mig_0017", path)
    assert spec is not None and spec.loader is not None
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def _run(fn: str, mig, monkeypatch: pytest.MonkeyPatch, conn) -> None:
    ops = Operations(MigrationContext.configure(conn))
    monkeypatch.setattr(mig, "op", ops)
    getattr(mig, fn)()


def _legacy_viz(i: str) -> dict[str, Any]:
    return {
        "i": i,
        "kind": "viz",
        "module_id": "conformance",
        "dataset_ref": {"module_id": "conformance", "dataset_id": "per_activity"},
        "viz_id": "bar",
        "mapping": {"x": "activity"},
        # Opaque card config: a `dataset_id` here is not a card ref and must survive.
        "config": {"dataset_id": "untouched"},
        "x": 0,
        "y": 0,
        "w": 6,
        "h": 8,
    }


_WIDGET = {"i": "w", "kind": "widget", "module_id": "discovery", "widget_id": "process-map"}


def _seed(conn) -> None:
    conn.exec_driver_sql("CREATE TABLE dashboards (id TEXT PRIMARY KEY, layout_json JSON)")
    conn.exec_driver_sql(
        "CREATE TABLE module_layouts (user_id TEXT, log_id TEXT, module_id TEXT, "
        "layout_json JSON, PRIMARY KEY (user_id, log_id, module_id))"
    )
    board = {"items": [_legacy_viz("a"), _WIDGET], "settings": {"grid_version": 2}}
    ins = sa.text("INSERT INTO dashboards (id, layout_json) VALUES (:i, :b)")
    conn.execute(ins, {"i": "legacy", "b": json.dumps(board)})
    conn.execute(ins, {"i": "plain", "b": json.dumps({"items": [_WIDGET]})})
    conn.execute(ins, {"i": "junk", "b": json.dumps(["not", "a", "board"])})
    conn.execute(
        sa.text(
            "INSERT INTO module_layouts (user_id, log_id, module_id, layout_json) "
            "VALUES ('u1', 'l1', 'conformance', :b)"
        ),
        {"b": json.dumps({"items": [_legacy_viz("m")]})},
    )


def _read(conn, sql: str) -> Any:
    raw = conn.execute(sa.text(sql)).scalar_one()
    return json.loads(raw) if isinstance(raw, str) else raw


def test_upgrade_renames_card_refs_and_downgrade_restores(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    mig = _load_migration()
    engine = sa.create_engine("sqlite://")
    with engine.begin() as conn:
        _seed(conn)
        before = {
            "legacy": _read(conn, "SELECT layout_json FROM dashboards WHERE id='legacy'"),
            "module": _read(conn, "SELECT layout_json FROM module_layouts"),
        }

        _run("upgrade", mig, monkeypatch, conn)
        board = _read(conn, "SELECT layout_json FROM dashboards WHERE id='legacy'")
        viz, widget = board["items"]
        assert "dataset_ref" not in viz
        assert viz["output_ref"] == {"module_id": "conformance", "output_id": "per_activity"}
        assert viz["config"] == {"dataset_id": "untouched"}
        assert widget == _WIDGET
        assert board["settings"] == {"grid_version": 2}
        # The rewritten card is what the current schema expects (no legacy shim needed).
        assert DashboardItem.model_validate(viz).output_ref is not None
        page = _read(conn, "SELECT layout_json FROM module_layouts")
        assert page["items"][0]["output_ref"]["output_id"] == "per_activity"
        assert _read(conn, "SELECT layout_json FROM dashboards WHERE id='junk'") == [
            "not",
            "a",
            "board",
        ]

        # Idempotent: a second run changes nothing.
        _run("upgrade", mig, monkeypatch, conn)
        assert _read(conn, "SELECT layout_json FROM dashboards WHERE id='legacy'") == board

        _run("downgrade", mig, monkeypatch, conn)
        assert (
            _read(conn, "SELECT layout_json FROM dashboards WHERE id='legacy'")
            == (before["legacy"])
        )
        assert _read(conn, "SELECT layout_json FROM module_layouts") == before["module"]


def test_upgrade_skips_missing_tables(monkeypatch: pytest.MonkeyPatch) -> None:
    """A fresh database (tables created later by the baseline) is a no-op."""
    mig = _load_migration()
    engine = sa.create_engine("sqlite://")
    with engine.begin() as conn:
        _run("upgrade", mig, monkeypatch, conn)
