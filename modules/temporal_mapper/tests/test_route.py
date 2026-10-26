"""The ``/graph`` route: what it tells the Logs panel (compute, drops, refusals)."""

from __future__ import annotations

import asyncio
from typing import Any

import pandas as pd
import pytest
from fastapi import HTTPException
from modules.temporal_mapper.module import TemporalMapperModule


class _FakeLogger:
    """Records ``(level, event)`` so tests can assert what reaches the Logs panel."""

    def __init__(self) -> None:
        self.records: list[tuple[str, str]] = []

    def debug(self, event: str, **fields: Any) -> None:
        self.records.append(("debug", event))

    def info(self, event: str, **fields: Any) -> None:
        self.records.append(("info", event))

    def warning(self, event: str, **fields: Any) -> None:
        self.records.append(("warning", event))

    def exception(self, event: str, **fields: Any) -> None:
        self.records.append(("exception", event))


class _FakeEventLog:
    def __init__(self, df: pd.DataFrame) -> None:
        self._df = df

    async def __aenter__(self) -> _FakeEventLog:
        return self

    async def __aexit__(self, *exc: object) -> None:
        return None

    async def pandas(self) -> pd.DataFrame:
        return self._df


class _Ctx:
    def __init__(self, df: pd.DataFrame) -> None:
        self.event_log = _FakeEventLog(df)
        self.logger = _FakeLogger()


def _graph(ctx: _Ctx, **overrides: Any) -> dict[str, Any]:
    # The route's defaults are FastAPI `Query(...)` markers, so pass real values.
    params: dict[str, Any] = {
        "time_unit": "week",
        "similarity_threshold": 0.85,
        "method": "hclust",
        "temporal_edges": "consecutive",
        "loop_size": 2,
        "pca_enabled": False,
        "pca_variance": 95.0,
    }
    params.update(overrides)
    return asyncio.run(TemporalMapperModule().graph(ctx, **params))  # type: ignore[arg-type]


def _events(rows: list[list[Any]]) -> pd.DataFrame:
    return pd.DataFrame(rows, columns=["case_id", "activity", "timestamp"])


_TWO_WEEKS = [
    ["a", "A", "2024-01-01"],
    ["a", "B", "2024-01-02"],
    ["b", "A", "2024-01-08"],
    ["b", "B", "2024-01-09"],
]


def test_logs_the_computation() -> None:
    ctx = _Ctx(_events(_TWO_WEEKS))

    result = _graph(ctx)

    assert len(result["periods"]) == 2
    assert ctx.logger.records == [("info", "temporal_mapper.graph_computed")]


def test_warns_about_dropped_events_and_a_single_period() -> None:
    ctx = _Ctx(_events([*_TWO_WEEKS[:2], ["a", "C", None]]))

    result = _graph(ctx)

    assert result["dropped_events"] == 1
    assert ("warning", "temporal_mapper.events_dropped") in ctx.logger.records
    assert ("warning", "temporal_mapper.too_few_periods") in ctx.logger.records


def test_a_refused_log_warns_and_still_answers_422() -> None:
    ctx = _Ctx(pd.DataFrame({"case_id": ["a"], "activity": ["A"]}))  # no timestamp column

    with pytest.raises(HTTPException) as exc:
        _graph(ctx)

    assert exc.value.status_code == 422
    assert ctx.logger.records == [("warning", "temporal_mapper.rejected")]
