"""flows - the flow builder (module combiner)

Revision ID: 0021_flows
Revises: 0020_artifacts
Create Date: 2026-10-29

A *flow* chains module runs: a node graph (source -> module -> output) stored as
``graph_json``, with ``pins_json`` recording each module node's ``version+hash`` at
save time (``mate.api.flows``). Each run binds a log and records per-node state in
``flow_runs``. Unrelated to the removed Builder's ``flows`` table (0009, dropped by
0011): a new table under the old name.

Idempotent in the style of the squashed baseline (guarded on the live schema).
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision: str = "0021_flows"
down_revision: str | None = "0020_artifacts"
branch_labels = None
depends_on = None

_INDEXES = (
    ("flows", "ix_flows_user_created_at", ["user_id", "created_at"]),
    ("flow_runs", "ix_flow_runs_flow_created_at", ["flow_id", "created_at"]),
)


def _state() -> tuple[set[str], set[str]]:
    """(table names, index names on ``flows`` + ``flow_runs``)."""
    inspector = sa.inspect(op.get_bind())
    tables = set(inspector.get_table_names())
    indexes = {
        str(ix["name"])
        for table in ("flows", "flow_runs")
        if table in tables
        for ix in inspector.get_indexes(table)
    }
    return tables, indexes


def upgrade() -> None:
    tables, indexes = _state()
    if "flows" not in tables:
        op.create_table(
            "flows",
            sa.Column("id", sa.String(length=36), primary_key=True),
            sa.Column(
                "user_id",
                sa.String(length=36),
                sa.ForeignKey("users.id", ondelete="CASCADE"),
                nullable=False,
            ),
            sa.Column("name", sa.String(length=255), nullable=False),
            sa.Column("description", sa.Text(), nullable=True),
            sa.Column("log_model", sa.String(length=16), nullable=False),
            sa.Column("graph_json", sa.JSON(), nullable=False),
            sa.Column("pins_json", sa.JSON(), nullable=False),
            sa.Column("created_at", sa.DateTime(), nullable=False),
            sa.Column("updated_at", sa.DateTime(), nullable=False),
        )
    if "flow_runs" not in tables:
        op.create_table(
            "flow_runs",
            sa.Column("id", sa.String(length=36), primary_key=True),
            sa.Column(
                "flow_id",
                sa.String(length=36),
                sa.ForeignKey("flows.id", ondelete="CASCADE"),
                nullable=False,
            ),
            sa.Column(
                "user_id",
                sa.String(length=36),
                sa.ForeignKey("users.id", ondelete="CASCADE"),
                nullable=False,
            ),
            sa.Column(
                "log_id",
                sa.String(length=36),
                sa.ForeignKey("process_logs.id", ondelete="CASCADE"),
                nullable=False,
            ),
            sa.Column("job_id", sa.String(length=36), nullable=True),
            sa.Column("status", sa.String(length=16), nullable=False),
            sa.Column("nodes_json", sa.JSON(), nullable=False),
            sa.Column("error", sa.Text(), nullable=True),
            sa.Column("created_at", sa.DateTime(), nullable=False),
            sa.Column("finished_at", sa.DateTime(), nullable=True),
        )
    for table, name, cols in _INDEXES:
        if name not in indexes:
            op.create_index(name, table, cols)


def downgrade() -> None:
    tables, _indexes = _state()
    if "flow_runs" in tables:
        op.drop_table("flow_runs")
    if "flows" in tables:
        op.drop_table("flows")
