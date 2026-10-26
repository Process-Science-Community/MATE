"""MATE adapter for the standalone Temporal Mapper core."""

import asyncio
import time
from typing import Literal

from fastapi import HTTPException, Query

from mate.sdk import Module, ModuleContext, route

from .core import compute


class TemporalMapperModule(Module):
    id = "temporal_mapper"

    @route.get("/graph")
    async def graph(
        self,
        ctx: ModuleContext,
        time_unit: Literal["day", "week", "month", "year"] = "day",
        similarity_threshold: float = Query(0.85, ge=0, le=1),
        method: Literal["hclust", "heuristic"] = "hclust",
        temporal_edges: Literal["consecutive", "adjacent"] = "consecutive",
        loop_size: int = Query(2, ge=1, le=20),
        pca_enabled: bool = False,
        pca_variance: float = Query(95, ge=0, le=100),
    ) -> dict:
        async with ctx.event_log as log:
            events = await log.pandas()
        t0 = time.perf_counter()
        try:
            result = await asyncio.to_thread(
                compute,
                events,
                time_unit,
                similarity_threshold,
                method,
                temporal_edges,
                loop_size,
                pca_enabled,
                pca_variance,
            )
        except ValueError as exc:
            # compute() refuses what the user can fix: too many events / periods /
            # features, a log without the needed columns, nothing valid left.
            ctx.logger.warning(
                "temporal_mapper.rejected", reason=str(exc), events=len(events), time_unit=time_unit
            )
            raise HTTPException(status_code=422, detail=str(exc)) from exc
        except Exception:
            ctx.logger.exception(
                "temporal_mapper.failed", events=len(events), time_unit=time_unit, method=method
            )
            raise
        ctx.logger.info(
            "temporal_mapper.graph_computed",
            events=len(events),
            periods=len(result["periods"]),
            features=result["n_features"],
            nodes=len(result["nodes"]),
            edges=len(result["edges"]),
            gaps=result["n_gaps"],
            time_unit=time_unit,
            method=method,
            similarity_threshold=similarity_threshold,
            temporal_edges=temporal_edges,
            loop_size=loop_size,
            pca=pca_enabled,
            duration_ms=round((time.perf_counter() - t0) * 1000),
        )
        if result["dropped_events"]:
            ctx.logger.warning(
                "temporal_mapper.events_dropped",
                dropped=result["dropped_events"],
                events=len(events),
            )
        if len(result["periods"]) < 2:
            ctx.logger.warning(
                "temporal_mapper.too_few_periods",
                periods=len(result["periods"]),
                time_unit=time_unit,
            )
        if result["pca"] and result["pca"]["note"]:
            ctx.logger.warning("temporal_mapper.pca_degenerate", note=result["pca"]["note"])
        return result
