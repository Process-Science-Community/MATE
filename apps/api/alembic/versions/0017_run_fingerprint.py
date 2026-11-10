"""run fingerprint - log_module_runs.fingerprint

Revision ID: 0017_run_fingerprint
Revises: 0016_lazy_module_processing
Create Date: 2026-10-28

A module's stored result also goes stale when the code that computes it changes
(its ``.py`` / ``.jar`` files or manifest) - not only when the log's data or the
module's version string moves on. The run records that code hash as of submission:

  - ``log_module_runs.fingerprint`` - nullable. ``NULL`` means unknown (every
    existing row) and is treated as matching, like ``module_version`` on a
    backfilled row, so upgrading does not turn every existing result stale. The
    coordinator stamps each such ``ready`` row with the current fingerprint at
    boot, so a later change is detected.

Idempotent in the style of the squashed baseline (guarded on the live schema).
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision: str = "0017_run_fingerprint"
down_revision: str | None = "0016_lazy_module_processing"
branch_labels = None
depends_on = None


def _run_columns() -> set[str] | None:
    """Column names of ``log_module_runs``, or ``None`` when the table is absent."""
    inspector = sa.inspect(op.get_bind())
    if "log_module_runs" not in set(inspector.get_table_names()):
        return None
    return {col["name"] for col in inspector.get_columns("log_module_runs")}


def upgrade() -> None:
    cols = _run_columns()
    if cols is not None and "fingerprint" not in cols:
        op.add_column("log_module_runs", sa.Column("fingerprint", sa.String(64)))


def downgrade() -> None:
    cols = _run_columns()
    if cols is not None and "fingerprint" in cols:
        with op.batch_alter_table("log_module_runs") as batch:
            batch.drop_column("fingerprint")
