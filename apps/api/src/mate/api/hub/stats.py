"""Revision stats + preview, snapshotted at publish from the curator's own log.

DuckDB only through ``EventLogAccess`` (which calls ``apply_resource_limits``).
The source log has no committed filter (publish refuses one), so every number
here describes the full log.
"""

from __future__ import annotations

import json
from typing import Any

from mate.api.db.models import EventLog
from mate.api.modules.event_log_access import EventLogAccess
from mate.api.services import log_aggregates

TOP_N = 20
PREVIEW_ROWS = 50


def _json_safe(value: Any) -> Any:
    return json.loads(json.dumps(value, default=str))


async def build_stats_and_preview(
    row: EventLog, user_id: str
) -> tuple[dict[str, Any], dict[str, Any], int]:
    """``(stats, preview, activities_count)`` for ``row``."""
    activities = await log_aggregates.activity_counts(row, user_id)
    variants = await log_aggregates.variant_table(row.id, user_id, None)
    quality = await log_aggregates.data_quality_report(row, user_id)
    specs = await log_aggregates.column_specs_for(row, user_id)
    dtypes = {s.name: str(s.type) for s in specs}
    total = max(int(quality.total_events), 1)
    columns = [
        {
            "name": c.column,
            "dtype": dtypes.get(c.column, str(c.type)),
            "null_ratio": round(c.null_count / total, 6),
            "distinct": int(c.distinct_count),
        }
        for c in quality.columns
    ]
    async with EventLogAccess(row.id, user_id) as access:
        cols, data = await access.duckdb_fetch_with_columns(
            f"SELECT * FROM events LIMIT {PREVIEW_ROWS}"
        )
    span_days: int | None = None
    if row.date_min is not None and row.date_max is not None:
        span_days = (row.date_max - row.date_min).days
    stats: dict[str, Any] = {
        "columns": columns,
        "time_span_days": span_days,
        "file_format": row.source_format,
    }
    preview: dict[str, Any] = {
        "top_activities": [{"activity": a, "count": n} for a, n in activities[:TOP_N]],
        "top_variants": [
            {
                "activities": v.activities_str.split("→") if v.activities_str else [],
                "case_count": v.case_count,
            }
            for v in variants[:TOP_N]
        ],
        "rows": {"columns": list(cols), "data": _json_safe([list(r) for r in data])},
    }
    return stats, preview, len(activities)
