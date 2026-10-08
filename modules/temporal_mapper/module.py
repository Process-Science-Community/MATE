"""MATE adapter for the standalone Temporal Mapper core."""

import asyncio
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
        try:
            return await asyncio.to_thread(
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
            raise HTTPException(status_code=422, detail=str(exc)) from exc
