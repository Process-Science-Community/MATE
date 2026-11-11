"""Test fixture: the write shapes a shared dashboard's viewer must not reach.

`/result` and `/result-job` store a normal cache key the way compute runs do
(conformance `/run`, agentsimulator `/simulate`, ...); `/derived` and
`/reserved-job` are viewer-safe; `/reserved-read` is a dataset route that touches
a reserved entry.
"""

from __future__ import annotations

from typing import Any

from mate.sdk import Module, ModuleContext, job, route


class SharedProbe(Module):
    id = "shared_probe"

    @route.get("/state")
    async def state(self, ctx: ModuleContext) -> dict[str, Any]:
        return {"result": await ctx.cache.get("result"), "read_only": ctx.read_only}

    @route.post("/result")
    async def write_result(self, ctx: ModuleContext) -> dict[str, Any]:
        await ctx.cache.set("result", {"by": ctx.user_id})
        return {"ok": True}

    @route.post("/result-job")
    @job(title="probe result")
    async def write_result_job(self, ctx: ModuleContext) -> dict[str, Any]:
        await ctx.cache.set("result", {"by": ctx.user_id, "via": "job"})
        return {"ok": True}

    @route.post("/derived", viewer_safe=True)
    async def derived(self, ctx: ModuleContext) -> dict[str, Any]:
        await ctx.cache.set("derived", {"n": 1})
        return {"read_only": ctx.read_only}

    @route.post("/reserved-job", viewer_safe=True)
    @job(title="probe reserved")
    async def write_reserved_job(self, ctx: ModuleContext) -> dict[str, Any]:
        await ctx.cache.set("__probe_state", {"by": ctx.user_id})
        return {"ok": True}

    @route.get("/reserved-read")
    async def reserved_read(self, ctx: ModuleContext) -> list[dict[str, Any]]:
        await ctx.cache.set("__probe_state", {"by": ctx.user_id})
        return [{"n": 1}]
