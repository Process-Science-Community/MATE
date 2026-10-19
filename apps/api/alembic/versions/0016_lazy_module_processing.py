"""lazy module processing - process_logs.data_revision + log_module_runs

Revision ID: 0016_lazy_module_processing
Revises: 0015_analytics_ui_objects
Create Date: 2026-10-19

Module processing is lazy now: a log is ``ready`` as soon as it is parsed, and a
module computes for it the first time something needs it (or at import, for the
few modules that declare ``precompute: eager``). Two schema changes back that:

  - ``process_logs.data_revision`` - a counter bumped whenever the log's data
    changes (import, reimport, remap, committed filter, cell edit). A module's
    stored result is stale when its recorded revision no longer matches.
  - ``log_module_runs`` - one row per (log, module) that has been asked to
    compute: status, the latest precompute job, the data revision and module
    version it ran against.

Data steps:

  - Logs still held in the old ``processing`` gate are flipped to ``ready`` (the
    gate no longer exists, so nothing would ever un-gate them) and their two
    gate columns cleared. The columns themselves stay - nothing writes them any
    more, but dropping them would break a downgrade.
  - ``log_module_runs`` is backfilled with a ``ready`` row for every (log,
    module) whose import-time precompute job already completed, so existing logs
    do not recompute on their first open. ``log_id`` lives inside the job's
    ``payload_json``, so the latest job per pair is picked in Python rather than
    with dialect-specific JSON SQL.

Idempotent in the style of the squashed baseline (guarded on the live schema); the
backfill only runs when this revision creates the table, so a half-applied boot
cannot insert duplicate primary keys.
"""

from __future__ import annotations

import json
from datetime import UTC, datetime
from typing import Any

import sqlalchemy as sa
from alembic import op

revision: str = "0016_lazy_module_processing"
down_revision: str | None = "0015_analytics_ui_objects"
branch_labels = None
depends_on = None

# `module.<id>.event.<topic_with_underscores>` - what `ModuleLoader._bind_event`
# names a job-backed import subscription. Kept local: a migration describes the
# schema and naming as of its own revision.
_PRECOMPUTE_TYPE_SUFFIXES = (".event.log_imported", ".event.ocel_imported")


def _as_obj(value: object) -> object:
    """Defensive JSON decode - the JSON column type usually deserializes, but a
    raw string slips through on some drivers."""
    if isinstance(value, str):
        try:
            return json.loads(value)
        except ValueError:
            return None
    return value


def _backfill_runs(bind: sa.Connection) -> None:
    jobs = sa.table(
        "jobs",
        sa.column("id", sa.String()),
        sa.column("user_id", sa.String()),
        sa.column("type", sa.String()),
        sa.column("module_id", sa.String()),
        sa.column("payload_json", sa.JSON()),
        sa.column("status", sa.String()),
        sa.column("finished_at", sa.DateTime()),
    )
    logs = sa.table("process_logs", sa.column("id", sa.String()))
    runs = sa.table(
        "log_module_runs",
        sa.column("log_id", sa.String()),
        sa.column("module_id", sa.String()),
        sa.column("user_id", sa.String()),
        sa.column("status", sa.String()),
        sa.column("job_id", sa.String()),
        sa.column("data_revision", sa.Integer()),
        sa.column("module_version", sa.String()),
        sa.column("error", sa.Text()),
        sa.column("requested_at", sa.DateTime()),
        sa.column("finished_at", sa.DateTime()),
    )

    log_ids = {row[0] for row in bind.execute(sa.select(logs.c.id))}
    rows = bind.execute(
        sa.select(
            jobs.c.id,
            jobs.c.user_id,
            jobs.c.type,
            jobs.c.module_id,
            jobs.c.payload_json,
            jobs.c.finished_at,
        ).where(jobs.c.status == "completed", jobs.c.module_id.is_not(None))
    ).mappings()

    # Latest completed precompute job per (log, module).
    latest: dict[tuple[str, str], dict[str, Any]] = {}
    for row in rows:
        if not str(row["type"]).endswith(_PRECOMPUTE_TYPE_SUFFIXES):
            continue
        payload = _as_obj(row["payload_json"])
        log_id = payload.get("log_id") if isinstance(payload, dict) else None
        if not isinstance(log_id, str) or log_id not in log_ids:
            continue
        key = (log_id, str(row["module_id"]))
        finished: datetime | None = row["finished_at"]
        prev = latest.get(key)
        if prev is None or (finished or datetime.min) >= (prev["finished_at"] or datetime.min):
            latest[key] = {
                "job_id": row["id"],
                "user_id": row["user_id"],
                "finished_at": finished,
            }

    now = datetime.now(UTC).replace(tzinfo=None)  # the schema stores naive UTC
    if latest:
        op.bulk_insert(
            runs,
            [
                {
                    "log_id": log_id,
                    "module_id": module_id,
                    "user_id": info["user_id"],
                    "status": "ready",
                    "job_id": info["job_id"],
                    # Every existing log starts at revision 0; a ready row at 0
                    # therefore reads as fresh until the data next changes.
                    "data_revision": 0,
                    "module_version": None,
                    "error": None,
                    "requested_at": info["finished_at"] or now,
                    "finished_at": info["finished_at"],
                }
                for (log_id, module_id), info in latest.items()
            ],
        )


def upgrade() -> None:
    bind = op.get_bind()
    inspector = sa.inspect(bind)

    log_cols = {col["name"] for col in inspector.get_columns("process_logs")}
    if "data_revision" not in log_cols:
        op.add_column(
            "process_logs",
            sa.Column("data_revision", sa.Integer(), nullable=False, server_default="0"),
        )

    created = False
    if "log_module_runs" not in set(inspector.get_table_names()):
        op.create_table(
            "log_module_runs",
            sa.Column(
                "log_id",
                sa.String(36),
                sa.ForeignKey("process_logs.id", ondelete="CASCADE"),
                primary_key=True,
            ),
            sa.Column("module_id", sa.String(64), primary_key=True),
            sa.Column(
                "user_id",
                sa.String(36),
                sa.ForeignKey("users.id", ondelete="CASCADE"),
                nullable=False,
            ),
            sa.Column("status", sa.String(16), nullable=False, server_default="queued"),
            sa.Column("job_id", sa.String(36)),
            sa.Column("data_revision", sa.Integer(), nullable=False, server_default="0"),
            sa.Column("module_version", sa.String(64)),
            sa.Column("error", sa.Text()),
            sa.Column("requested_at", sa.DateTime(), nullable=False),
            sa.Column("finished_at", sa.DateTime()),
        )
        op.create_index("ix_log_module_runs_job_id", "log_module_runs", ["job_id"])
        op.create_index(
            "ix_log_module_runs_user_module", "log_module_runs", ["user_id", "module_id"]
        )
        created = True

    # Un-gate logs stranded in the retired `processing` state.
    logs = sa.table(
        "process_logs",
        sa.column("status", sa.String()),
        sa.column("processing_import_job_id", sa.String()),
        sa.column("expected_modules", sa.JSON()),
    )
    bind.execute(
        logs.update()
        .where(logs.c.status == "processing")
        .values(status="ready", processing_import_job_id=None, expected_modules=sa.null())
    )

    if created:
        _backfill_runs(bind)


def downgrade() -> None:
    bind = op.get_bind()
    inspector = sa.inspect(bind)
    if "log_module_runs" in set(inspector.get_table_names()):
        op.drop_index("ix_log_module_runs_user_module", table_name="log_module_runs")
        op.drop_index("ix_log_module_runs_job_id", table_name="log_module_runs")
        op.drop_table("log_module_runs")
    if "data_revision" in {col["name"] for col in inspector.get_columns("process_logs")}:
        with op.batch_alter_table("process_logs") as batch:
            batch.drop_column("data_revision")
