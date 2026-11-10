from __future__ import annotations

from typing import Any

from mate.sdk import Module, ModuleContext, job, on_event, route


class LogConfigModule(Module):
    id = "log_config_mod"

    @on_event("log.imported")
    @job(progress=True, title="Precompute with per-log config")
    async def precompute(self, ctx: ModuleContext, payload: dict) -> None:
        await ctx.cache.set("config", ctx.config.value)

    @route.get("/echo-config")
    async def echo_config(self, ctx: ModuleContext) -> dict[str, Any]:
        """The effective config the loader assembled for the request's log."""
        return {"config": ctx.config.value}
