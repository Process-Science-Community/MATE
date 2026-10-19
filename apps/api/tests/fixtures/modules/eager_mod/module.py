from __future__ import annotations

from mate.sdk import Module, ModuleContext, job, on_event


class EagerModule(Module):
    id = "eager_mod"

    @on_event("log.imported")
    @job(progress=True, title="Eager precompute on import")
    async def precompute(self, ctx: ModuleContext, payload: dict) -> None:
        # The body is irrelevant to the lazy-processing tests - they assert on the
        # resulting `Job` / run rows. Touch the log so the job does real (cheap) work.
        async with ctx.event_log as log:
            await log.duckdb_fetch("SELECT COUNT(*) FROM events")
        await ctx.cache.set("done", {"ok": True})
