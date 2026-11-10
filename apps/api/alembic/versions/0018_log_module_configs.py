"""per-log module config - log_module_configs

Revision ID: 0018_log_module_configs
Revises: 0017_run_fingerprint
Create Date: 2026-10-28

A module's ``config_schema`` property tagged ``ui.scope: dataset`` may be set per
event log: the module-wide value (``module_configs``) is the default every log
inherits, and this table holds a user's per-log overrides - only the keys they
changed, one row per (user, log, module).

Idempotent in the style of the squashed baseline (guarded on the live schema).
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision: str = "0018_log_module_configs"
down_revision: str | None = "0017_run_fingerprint"
branch_labels = None
depends_on = None


def _has_table() -> bool:
    return "log_module_configs" in set(sa.inspect(op.get_bind()).get_table_names())


def upgrade() -> None:
    if _has_table():
        return
    op.create_table(
        "log_module_configs",
        sa.Column(
            "user_id",
            sa.String(36),
            sa.ForeignKey("users.id", ondelete="CASCADE"),
            primary_key=True,
        ),
        sa.Column(
            "log_id",
            sa.String(36),
            sa.ForeignKey("process_logs.id", ondelete="CASCADE"),
            primary_key=True,
        ),
        sa.Column("module_id", sa.String(64), primary_key=True),
        sa.Column("config_json", sa.JSON(), nullable=False, server_default="{}"),
        sa.Column("updated_at", sa.DateTime(), nullable=False),
    )


def downgrade() -> None:
    if _has_table():
        op.drop_table("log_module_configs")
