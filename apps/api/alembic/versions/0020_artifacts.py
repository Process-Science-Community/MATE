"""artifacts - the artifact workspace index

Revision ID: 0020_artifacts
Revises: 0019_module_boards
Create Date: 2026-10-28

One row per artifact in a user's workspace (``mate.api.modules.artifacts``): an
index over module result caches (registered when a default run or a run variant
settles ready) plus the user's uploaded JSON artifacts. ``(user_id, ref)`` is
unique; ``name``/``pinned`` are the user's.

Idempotent in the style of the squashed baseline (guarded on the live schema).
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision: str = "0020_artifacts"
down_revision: str | None = "0019_module_boards"
branch_labels = None
depends_on = None

_INDEXES = (
    ("ux_artifacts_user_ref", ["user_id", "ref"], True),
    ("ix_artifacts_user_log", ["user_id", "log_id"], False),
    ("ix_artifacts_user_type", ["user_id", "artifact_type"], False),
)


def _state() -> tuple[set[str], set[str]]:
    """(table names, ``artifacts`` index names)."""
    inspector = sa.inspect(op.get_bind())
    tables = set(inspector.get_table_names())
    if "artifacts" not in tables:
        return tables, set()
    return tables, {str(ix["name"]) for ix in inspector.get_indexes("artifacts")}


def upgrade() -> None:
    tables, indexes = _state()
    if "artifacts" not in tables:
        op.create_table(
            "artifacts",
            sa.Column("id", sa.String(length=36), primary_key=True),
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
                nullable=True,
            ),
            sa.Column("ref", sa.String(length=255), nullable=False),
            sa.Column("artifact_type", sa.String(length=64), nullable=False),
            sa.Column("type_version", sa.Integer(), nullable=False),
            sa.Column("source", sa.String(length=16), nullable=False),
            sa.Column("module_id", sa.String(length=64), nullable=True),
            sa.Column("dataset_id", sa.String(length=64), nullable=True),
            sa.Column("variant", sa.String(length=16), nullable=True),
            sa.Column("key", sa.String(length=128), nullable=False),
            sa.Column("name", sa.String(length=255), nullable=False),
            sa.Column("pinned", sa.Boolean(), nullable=False, server_default=sa.false()),
            sa.Column("producer_version", sa.String(length=64), nullable=True),
            sa.Column("producer_hash", sa.String(length=64), nullable=True),
            sa.Column("inputs", sa.JSON(), nullable=False),
            sa.Column("spec", sa.JSON(), nullable=True),
            sa.Column("size_bytes", sa.BigInteger(), nullable=True),
            sa.Column("created_at", sa.DateTime(), nullable=False),
            sa.Column("updated_at", sa.DateTime(), nullable=False),
        )
    for name, cols, unique in _INDEXES:
        if name not in indexes:
            op.create_index(name, "artifacts", cols, unique=unique)


def downgrade() -> None:
    tables, _indexes = _state()
    if "artifacts" in tables:
        # Uploaded files under data/users/{uid}/artifacts/ stay on disk, unindexed.
        op.drop_table("artifacts")
