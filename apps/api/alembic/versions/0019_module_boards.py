"""module boards - dashboards.module_id, drop module_layouts

Revision ID: 0019_module_boards
Revises: 0018_log_module_configs
Create Date: 2026-10-28

A panel-less module renders a *module board*: a dashboard row seeded from the
manifest's ``frontend.board`` per (user, module, log).

  - ``dashboards.module_id`` - nullable; set only on a module board. A unique
    index over (user_id, module_id, event_log_id) keeps one board per triple
    (NULLs are distinct, so user-built dashboards are unconstrained).
  - ``module_layouts`` is dropped. Nothing has read or written it since the
    module-layout hooks were removed; module boards replace it. A downgrade
    recreates it empty.

Idempotent in the style of the squashed baseline (guarded on the live schema).
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision: str = "0019_module_boards"
down_revision: str | None = "0018_log_module_configs"
branch_labels = None
depends_on = None

_INDEX = "ix_dashboards_user_module_log"


def _state() -> tuple[set[str], set[str], set[str]]:
    """(table names, ``dashboards`` columns, ``dashboards`` index names)."""
    inspector = sa.inspect(op.get_bind())
    tables = set(inspector.get_table_names())
    if "dashboards" not in tables:
        return tables, set(), set()
    cols = {col["name"] for col in inspector.get_columns("dashboards")}
    indexes = {str(ix["name"]) for ix in inspector.get_indexes("dashboards")}
    return tables, cols, indexes


def upgrade() -> None:
    tables, cols, indexes = _state()
    if "dashboards" in tables:
        if "module_id" not in cols:
            op.add_column("dashboards", sa.Column("module_id", sa.String(64), nullable=True))
        if _INDEX not in indexes:
            op.create_index(
                _INDEX, "dashboards", ["user_id", "module_id", "event_log_id"], unique=True
            )
    if "module_layouts" in tables:
        op.drop_table("module_layouts")


def downgrade() -> None:
    tables, cols, indexes = _state()
    if "module_layouts" not in tables:
        op.create_table(
            "module_layouts",
            sa.Column(
                "user_id",
                sa.String(length=36),
                sa.ForeignKey("users.id", ondelete="CASCADE"),
                primary_key=True,
            ),
            sa.Column(
                "log_id",
                sa.String(length=36),
                sa.ForeignKey("process_logs.id", ondelete="CASCADE"),
                primary_key=True,
            ),
            sa.Column("module_id", sa.String(length=64), primary_key=True),
            sa.Column("layout_json", sa.JSON(), nullable=False),
            sa.Column("updated_at", sa.DateTime(), nullable=False),
        )
    if _INDEX in indexes:
        op.drop_index(_INDEX, table_name="dashboards")
    if "module_id" in cols:
        # Module boards are meaningless without the column - drop them rather than
        # let them surface as ordinary dashboards.
        op.execute("DELETE FROM dashboards WHERE module_id IS NOT NULL")
        with op.batch_alter_table("dashboards") as batch:
            batch.drop_column("module_id")
