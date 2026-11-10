from __future__ import annotations

from mate.sdk import Module, ModuleContext, job, on_event


class ArtifactConsumerModule(Module):
    id = "artifact_consumer"

    @on_event("log.imported")
    @job(title="Consume a petri net")
    async def precompute(self, ctx: ModuleContext, payload: dict) -> None:
        model = await ctx.inputs.get("reference_model")
        ref = ctx.inputs.ref("reference_model")
        await ctx.cache.set(
            "seen",
            {
                "ports": ctx.inputs.ports,
                "ref": str(ref) if ref is not None else None,
                "places": len(model["places"]) if model is not None else None,
            },
        )
