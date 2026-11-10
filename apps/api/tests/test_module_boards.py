"""Module boards - a panel-less module's page as a dashboard seeded from `frontend.board`.

Covers the manifest contract, the lazy per-(user, module, log) board, reset, the
binding a module board keeps through `PATCH /dashboards/{id}`, its exclusion from
the dashboard list / sharing / export, and Alembic 0019.
"""

from __future__ import annotations

import importlib.util
from pathlib import Path
from typing import Any

import pytest
import sqlalchemy as sa
from alembic.migration import MigrationContext
from alembic.operations import Operations
from pydantic import ValidationError
from sqlalchemy.exc import IntegrityError

from mate.sdk.errors import ModuleManifestError
from mate.sdk.manifest import Manifest

from .test_module_processing import import_sample, mods_client

MOD = "board_mod"

# --------------------------------------------------------------------------- #
# Manifest
# --------------------------------------------------------------------------- #


def _item(**overrides: Any) -> dict[str, Any]:
    item: dict[str, Any] = {
        "id": "t",
        "viz": "table",
        "dataset": "counts",
        "x": 0,
        "y": 0,
        "w": 6,
        "h": 8,
    }
    item.update(overrides)
    return item


def _manifest(*items: dict[str, Any]) -> Manifest:
    return Manifest.model_validate(
        {
            "id": "bm",
            "name": "BM",
            "version": "1.0.0",
            "category": "other",
            "datasets": [{"id": "counts", "shape": "table", "route": "/counts"}],
            "frontend": {"board": {"items": list(items)}},
        }
    )


def test_board_parses_and_seeds_viz_cards() -> None:
    from mate.api.routes.module_boards import seed_items

    m = _manifest(
        _item(),
        _item(id="b", viz="bar", mapping={"x": "a"}, options={"top_n": 5}, x=6, w=6, title="B"),
    )
    assert m.frontend.board is not None and len(m.frontend.board.items) == 2
    items = seed_items(m)
    assert [i.kind for i in items] == ["viz", "viz"]
    bar = items[1]
    assert bar.i == "b" and bar.viz_id == "bar" and bar.title == "B"
    assert bar.dataset_ref is not None
    assert (bar.dataset_ref.module_id, bar.dataset_ref.dataset_id) == ("bm", "counts")
    assert bar.mapping == {"x": "a"} and bar.config == {"top_n": 5}
    assert (bar.x, bar.y, bar.w, bar.h) == (6, 0, 6, 8)


def test_board_is_optional() -> None:
    m = Manifest.model_validate({"id": "bm", "name": "BM", "version": "1.0.0", "category": "other"})
    assert m.frontend.board is None


@pytest.mark.parametrize(
    ("item", "match"),
    [
        (_item(x=8, w=6), "overflows"),
        (_item(x=-1), "greater than or equal"),
        (_item(w=0), "greater than or equal"),
        (_item(h=0), "greater than or equal"),
        (_item(w=13), "less than or equal"),
        (_item(dataset="nope"), "not one of this module's"),
    ],
)
def test_board_item_rejected(item: dict[str, Any], match: str) -> None:
    with pytest.raises((ModuleManifestError, ValidationError), match=match):
        _manifest(item)


def test_board_item_may_use_a_cache_only_dataset() -> None:
    # The web reads a cache-only dataset by artifact ref (`GET /artifacts/content?ref=`).
    m = Manifest.model_validate(
        {
            "id": "bm",
            "name": "BM",
            "version": "1.0.0",
            "category": "other",
            "datasets": [{"id": "counts", "shape": "table", "cache_key": "counts"}],
            "frontend": {"board": {"items": [_item()]}},
        }
    )
    assert m.frontend.board is not None


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


def test_board_item_ids_unique() -> None:
    with pytest.raises(ModuleManifestError, match="declared twice"):
        _manifest(_item(), _item(x=6))


def test_bundled_fixture_manifest_validates() -> None:
    path = Path(__file__).parent / "fixtures" / "modules" / MOD / "manifest.yaml"
    assert Manifest.load_yaml(path).frontend.board is not None


# --------------------------------------------------------------------------- #
# API
# --------------------------------------------------------------------------- #


def _url(log_id: str, module_id: str = MOD) -> str:
    return f"/api/v1/event-logs/{log_id}/modules/{module_id}/board"


@pytest.mark.asyncio
async def test_board_is_seeded_once_and_reset(tmp_path: Path) -> None:
    async with mods_client(tmp_path, MOD, "sample_cards") as c:
        summaries = {m["id"]: m for m in (await c.get("/api/v1/modules")).json()}
        assert summaries[MOD]["has_board"] is True
        assert summaries["sample_cards"]["has_board"] is False

        log_id = await import_sample(c, "board-seed")
        first = await c.get(_url(log_id))
        assert first.status_code == 200, first.text
        board = first.json()
        assert board["event_log_id"] == log_id
        assert board["name"] == "Board Module"
        assert [i["i"] for i in board["items"]] == ["counts-table", "counts-bar"]
        bar = board["items"][1]
        assert bar["kind"] == "viz" and bar["viz_id"] == "bar"
        assert bar["dataset_ref"] == {"module_id": MOD, "dataset_id": "counts"}
        assert bar["config"] == {"top_n": 5}

        # Idempotent: the same row on every read.
        again = (await c.get(_url(log_id))).json()
        assert again["id"] == board["id"]

        # A second log gets its own board.
        other_log = await import_sample(c, "board-seed-2")
        assert (await c.get(_url(other_log))).json()["id"] != board["id"]

        # Saving goes through the normal dashboard PATCH.
        kept = board["items"][:1]
        settings = {**board["settings"], "chrome": {"border": False}}
        resp = await c.patch(
            f"/api/v1/dashboards/{board['id']}", json={"items": kept, "settings": settings}
        )
        assert resp.status_code == 200, resp.text
        saved = (await c.get(_url(log_id))).json()
        assert saved["id"] == board["id"]
        assert [i["i"] for i in saved["items"]] == ["counts-table"]

        # Reset puts the manifest items back and keeps the board settings.
        reset = await c.post(f"{_url(log_id)}/reset")
        assert reset.status_code == 200, reset.text
        body = reset.json()
        assert body["id"] == board["id"]
        assert [i["i"] for i in body["items"]] == ["counts-table", "counts-bar"]
        assert body["settings"]["chrome"] == {"border": False}


@pytest.mark.asyncio
async def test_board_binding_and_exclusions(tmp_path: Path) -> None:
    async with mods_client(tmp_path, MOD) as c:
        log_id = await import_sample(c, "board-bind")
        other_log = await import_sample(c, "board-bind-2")
        board_id = (await c.get(_url(log_id))).json()["id"]
        url = f"/api/v1/dashboards/{board_id}"

        # Bound to its log for good; re-sending the same log is fine.
        resp = await c.patch(url, json={"event_log_id": other_log})
        assert resp.status_code == 400, resp.text
        resp = await c.patch(url, json={"event_log_id": None})
        assert resp.status_code == 400, resp.text
        resp = await c.patch(url, json={"event_log_id": log_id, "name": "Mine"})
        assert resp.status_code == 200, resp.text
        assert (await c.get(_url(log_id))).json()["id"] == board_id

        # Readable by id for its owner, but not listed, shared or exported.
        assert (await c.get(url)).status_code == 200
        assert board_id not in {d["id"] for d in (await c.get("/api/v1/dashboards")).json()}
        resp = await c.get(f"{url}/export")
        assert resp.status_code == 404
        resp = await c.post(f"{url}/shares", json={"target_user_id": "someone"})
        assert resp.status_code == 404
        assert resp.json()["detail"] == "Dashboard not found."

        # A plain dashboard is unaffected.
        plain = (await c.post("/api/v1/dashboards", json={"name": "Plain"})).json()
        assert plain["id"] in {d["id"] for d in (await c.get("/api/v1/dashboards")).json()}
        assert (await c.get(f"/api/v1/dashboards/{plain['id']}/export")).status_code == 200


@pytest.mark.asyncio
async def test_board_404s(tmp_path: Path) -> None:
    async with mods_client(tmp_path, MOD, "sample_cards") as c:
        log_id = await import_sample(c, "board-404")
        # A module without a board.
        assert (await c.get(_url(log_id, "sample_cards"))).status_code == 404
        # A module the user does not have.
        assert (await c.get(_url(log_id, "nope"))).status_code == 404
        # A log the user does not own.
        assert (await c.get(_url("00000000-0000-7000-8000-00000000dead"))).status_code == 404
        assert (await c.post(f"{_url(log_id, 'sample_cards')}/reset")).status_code == 404


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
