"""dataset hub - hub_datasets, hub_dataset_revisions, process_logs lineage

Revision ID: 0018_hub_datasets
Revises: 0017_rename_module_outputs
Create Date: 2026-11-19

Stage 1 of the dataset hub (``docs/strategy/dataset-hub-plan.md``): a curator
publishes one of their event logs as a *dataset*; "Use this dataset" copies an
immutable revision of it into an ordinary event log owned by the user.

  - ``hub_datasets`` - the card (title, license, citation, publisher, tags,
    visibility, takedown state).
  - ``hub_dataset_revisions`` - one immutable snapshot (stats, preview, the
    canonical module results it ships). The on-disk path is derived, never
    stored: ``data/datasets/{dataset_id}/r{number}/``.
  - ``process_logs.dataset_revision_id`` + ``license_snapshot`` - lineage of a
    derived log, plus an index on ``(user_id, dataset_revision_id)`` that serves
    both "use" idempotency and the takedown fan-out.

SQLite note: the ``process_logs`` change runs through ``batch_alter_table``, which
recreates the table on SQLite. Alembic's connection does not set
``PRAGMA foreign_keys=ON`` (only ``db/engine.py`` does, for app connections), so the
recreate does not cascade into ``log_module_runs``, ``module_layouts`` or
``dashboards``. On Postgres the batch is a plain ``ALTER TABLE``.

Idempotent (guarded on the live schema, as in 0016). The downgrade refuses while
any log - soft-deleted ones included - still points at a revision. It leaves
``data/datasets/`` on disk; remove it by hand after a downgrade if wanted.
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision: str = "0018_hub_datasets"
down_revision: str | None = "0017_rename_module_outputs"
branch_labels = None
depends_on = None

_LINEAGE_INDEX = "ix_process_logs_user_dataset_revision"
_LINEAGE_FK = "fk_process_logs_dataset_revision"


def upgrade() -> None:
    bind = op.get_bind()
    insp = sa.inspect(bind)
    tables = set(insp.get_table_names())

    if "hub_datasets" not in tables:
        op.create_table(
            "hub_datasets",
            sa.Column("id", sa.String(36), primary_key=True),
            sa.Column("namespace", sa.String(64), nullable=False),
            sa.Column("name", sa.String(64), nullable=False),
            sa.Column("title", sa.String(200), nullable=False),
            sa.Column("summary", sa.String(300), nullable=False, server_default=""),
            sa.Column("description_md", sa.Text()),
            sa.Column("license_id", sa.String(32), nullable=False, server_default="unknown"),
            sa.Column("license_name", sa.String(200)),
            sa.Column("license_url", sa.String(500)),
            sa.Column("citation", sa.Text()),
            sa.Column("source_url", sa.String(500)),
            sa.Column("source_doi", sa.String(200)),
            sa.Column("publisher_name", sa.String(200)),
            sa.Column("publisher_url", sa.String(500)),
            sa.Column("tags", sa.JSON(), nullable=False),
            sa.Column("search_text", sa.Text(), nullable=False, server_default=""),
            sa.Column("allow_external_ai", sa.Boolean(), nullable=False, server_default=sa.true()),
            sa.Column("retention_until", sa.Date()),
            sa.Column("visibility", sa.String(16), nullable=False, server_default="private"),
            sa.Column(
                "audience_team_id",
                sa.String(36),
                sa.ForeignKey("teams.id", ondelete="SET NULL"),
            ),
            sa.Column("status", sa.String(16), nullable=False, server_default="active"),
            sa.Column("disabled_at", sa.DateTime()),
            sa.Column("disabled_reason", sa.Text()),
            sa.Column("disabled_by", sa.String(36), sa.ForeignKey("users.id", ondelete="SET NULL")),
            sa.Column("current_revision_id", sa.String(36)),
            sa.Column("created_by", sa.String(36), sa.ForeignKey("users.id", ondelete="SET NULL")),
            sa.Column("created_at", sa.DateTime(), nullable=False),
            sa.Column("updated_at", sa.DateTime(), nullable=False),
            sa.Column("published_at", sa.DateTime()),
            sa.UniqueConstraint("namespace", "name", name="uq_hub_datasets_slug"),
        )
        op.create_index(
            "ix_hub_datasets_visibility_status", "hub_datasets", ["visibility", "status"]
        )
        op.create_index("ix_hub_datasets_updated_at", "hub_datasets", ["updated_at"])

    if "hub_dataset_revisions" not in tables:
        op.create_table(
            "hub_dataset_revisions",
            sa.Column("id", sa.String(36), primary_key=True),
            sa.Column(
                "dataset_id",
                sa.String(36),
                sa.ForeignKey("hub_datasets.id", ondelete="CASCADE"),
                nullable=False,
            ),
            sa.Column("number", sa.Integer(), nullable=False),
            sa.Column("source_log_id", sa.String(36)),
            sa.Column("source_format", sa.String(32)),
            sa.Column("log_model", sa.String(16)),
            sa.Column("detected_schema", sa.JSON()),
            sa.Column("column_roles", sa.JSON()),
            sa.Column("column_overrides", sa.JSON()),
            sa.Column("events_count", sa.Integer()),
            sa.Column("cases_count", sa.Integer()),
            sa.Column("variants_count", sa.Integer()),
            sa.Column("activities_count", sa.Integer()),
            sa.Column("date_min", sa.DateTime()),
            sa.Column("date_max", sa.DateTime()),
            sa.Column("bytes_parquet", sa.BigInteger(), nullable=False, server_default="0"),
            sa.Column("events_sha256", sa.String(64), nullable=False),
            sa.Column("stats", sa.JSON(), nullable=False),
            sa.Column("preview", sa.JSON(), nullable=False),
            sa.Column("canonical_results", sa.JSON(), nullable=False),
            sa.Column("created_by", sa.String(36), sa.ForeignKey("users.id", ondelete="SET NULL")),
            sa.Column("created_at", sa.DateTime(), nullable=False),
            sa.Column("purged_at", sa.DateTime()),
            sa.Column("purged_by", sa.String(36)),
            sa.UniqueConstraint("dataset_id", "number", name="uq_hub_dataset_revisions_number"),
        )

    log_cols = {c["name"] for c in insp.get_columns("process_logs")}
    missing = [c for c in ("dataset_revision_id", "license_snapshot") if c not in log_cols]
    if missing:
        with op.batch_alter_table("process_logs") as b:
            if "dataset_revision_id" in missing:
                b.add_column(
                    sa.Column(
                        "dataset_revision_id",
                        sa.String(36),
                        sa.ForeignKey(
                            "hub_dataset_revisions.id", name=_LINEAGE_FK, ondelete="SET NULL"
                        ),
                    )
                )
            if "license_snapshot" in missing:
                b.add_column(sa.Column("license_snapshot", sa.JSON()))

    indexes = {ix["name"] for ix in sa.inspect(bind).get_indexes("process_logs")}
    if _LINEAGE_INDEX not in indexes:
        op.create_index(_LINEAGE_INDEX, "process_logs", ["user_id", "dataset_revision_id"])


def downgrade() -> None:
    bind = op.get_bind()
    insp = sa.inspect(bind)
    log_cols = {c["name"] for c in insp.get_columns("process_logs")}
    if "dataset_revision_id" in log_cols:
        derived = bind.execute(
            sa.text("SELECT 1 FROM process_logs WHERE dataset_revision_id IS NOT NULL LIMIT 1")
        ).first()
        if derived is not None:
            raise RuntimeError(
                "0018 downgrade refused: derived logs exist (incl. soft-deleted). Purge the "
                "datasets, delete the derived logs, then NULL process_logs.dataset_revision_id."
            )

    indexes = {ix["name"] for ix in insp.get_indexes("process_logs")}
    if _LINEAGE_INDEX in indexes:
        op.drop_index(_LINEAGE_INDEX, table_name="process_logs")
    present = [c for c in ("dataset_revision_id", "license_snapshot") if c in log_cols]
    if present:
        fks = {fk.get("name") for fk in insp.get_foreign_keys("process_logs")}
        with op.batch_alter_table("process_logs") as b:
            if _LINEAGE_FK in fks and bind.dialect.name != "sqlite":
                b.drop_constraint(_LINEAGE_FK, type_="foreignkey")
            for col in present:
                b.drop_column(col)

    tables = set(insp.get_table_names())
    if "hub_dataset_revisions" in tables:
        op.drop_table("hub_dataset_revisions")
    if "hub_datasets" in tables:
        op.drop_index("ix_hub_datasets_updated_at", table_name="hub_datasets")
        op.drop_index("ix_hub_datasets_visibility_status", table_name="hub_datasets")
        op.drop_table("hub_datasets")
