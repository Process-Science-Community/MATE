"""module outputs - rename stored viz-card refs ``dataset_ref`` -> ``output_ref``

Revision ID: 0017_rename_module_outputs
Revises: 0016_lazy_module_processing
Create Date: 2026-11-19

A module's named, typed data output used to be called a *dataset* (manifest
``datasets:``). "Dataset" now means only a published event log, so the module
concept is renamed to *output* (manifest ``outputs:``). Generic-viz cards
persist a reference to one, so this data-only migration rewrites it wherever a
card list is stored:

* ``dashboards.layout_json`` - ``items[*]``
* ``module_layouts.layout_json`` - ``items[*]`` (per-user module page layouts)

At each card, ``dataset_ref`` becomes ``output_ref`` and its inner
``dataset_id`` becomes ``output_id``. Nothing else in the blob is touched - a
``dataset_*`` key anywhere else (e.g. inside a card's opaque ``config``) is not
a card ref and stays as written.

Rows are loaded, rewritten in Python and written back, so the migration is
portable across Postgres and SQLite (no dialect-specific JSON SQL). It is
idempotent: a card that already has ``output_ref`` keeps it, and a blob with no
legacy key is not written. ``downgrade`` applies the exact inverse.
"""

from __future__ import annotations

import json
from typing import Any

import sqlalchemy as sa
from alembic import op

revision: str = "0017_rename_module_outputs"
down_revision: str | None = "0016_lazy_module_processing"
branch_labels = None
depends_on = None

# Kept local rather than imported from the application: a migration describes
# the data as of its own revision.
NEW_REF, NEW_ID = "output_ref", "output_id"
OLD_REF, OLD_ID = "dataset_ref", "dataset_id"

# table -> primary-key columns. Both hold a ``{"items": [card, ...]}`` blob.
TABLES: dict[str, tuple[str, ...]] = {
    "dashboards": ("id",),
    "module_layouts": ("user_id", "log_id", "module_id"),
}


def _table(name: str, pk: tuple[str, ...]) -> sa.Table:
    cols = [sa.column(c, sa.String()) for c in pk]
    return sa.table(name, *cols, sa.column("layout_json", sa.JSON()))


def _as_obj(value: object) -> object:
    """Defensive JSON decode - the JSON column type usually deserializes, but a
    raw string slips through on some drivers."""
    if isinstance(value, str):
        try:
            return json.loads(value)
        except ValueError:
            return None
    return value


def _rename_card(card: dict[str, Any], src: tuple[str, str], dst: tuple[str, str]) -> bool:
    """Move ``card[src_ref]`` to ``card[dst_ref]`` (renaming its inner id key).
    Returns whether the card changed. An existing ``dst_ref`` wins."""
    src_ref, src_id = src
    dst_ref, dst_id = dst
    if src_ref not in card:
        return False
    ref = card.pop(src_ref)
    if dst_ref in card:
        return True
    if isinstance(ref, dict):
        inner: dict[str, Any] = dict(ref)  # pyright: ignore[reportUnknownArgumentType]
        if src_id in inner and dst_id not in inner:
            inner[dst_id] = inner.pop(src_id)
        ref = inner
    card[dst_ref] = ref
    return True


def rewrite_blob(blob: object, src: tuple[str, str], dst: tuple[str, str]) -> object | None:
    """The rewritten ``layout_json``, or ``None`` when nothing needs to change."""
    if not isinstance(blob, dict):
        return None
    items = blob.get("items")  # pyright: ignore[reportUnknownMemberType, reportUnknownVariableType]
    if not isinstance(items, list):
        return None
    changed = False
    new_items: list[Any] = []
    for raw in items:  # pyright: ignore[reportUnknownVariableType]
        if isinstance(raw, dict):
            card: dict[str, Any] = dict(raw)  # pyright: ignore[reportUnknownArgumentType]
            changed = _rename_card(card, src, dst) or changed
            new_items.append(card)
        else:
            new_items.append(raw)
    if not changed:
        return None
    return {**blob, "items": new_items}  # pyright: ignore[reportUnknownVariableType]


def _migrate(src: tuple[str, str], dst: tuple[str, str]) -> None:
    bind = op.get_bind()
    existing = set(sa.inspect(bind).get_table_names())
    for name, pk in TABLES.items():
        if name not in existing:
            continue
        table = _table(name, pk)
        rows = list(bind.execute(sa.select(table)).mappings())
        for row in rows:
            new_blob = rewrite_blob(_as_obj(row["layout_json"]), src, dst)
            if new_blob is None:
                continue
            where = sa.and_(*(table.c[c] == row[c] for c in pk))
            bind.execute(table.update().where(where).values(layout_json=new_blob))


def upgrade() -> None:
    _migrate((OLD_REF, OLD_ID), (NEW_REF, NEW_ID))


def downgrade() -> None:
    _migrate((NEW_REF, NEW_ID), (OLD_REF, OLD_ID))
