from __future__ import annotations

from mate.sdk import Module, ModuleContext, job, on_event


class ArtifactProducerModule(Module):
    id = "artifact_producer"

    @on_event("log.imported")
    @job(title="Produce a petri net")
    async def precompute(self, ctx: ModuleContext, payload: dict) -> None:
        async with ctx.event_log as log:
            [(n_activities,)] = await log.duckdb_fetch(
                "SELECT COUNT(DISTINCT activity) FROM events"
            )
        await ctx.cache.set(
            "model",
            {
                "kind": "petri_net",
                "places": [{"id": f"p{i}"} for i in range(int(n_activities))],
                "transitions": [{"id": "t0", "label": "a", "is_invisible": False}],
                "arcs": [{"id": "p0__t0", "source": "p0", "target": "t0", "weight": 1}],
            },
        )
