"""Reads every ctx.inputs port inside a subprocess worker (see manifest.yaml)."""

from __future__ import annotations

from typing import Any

from mate.sdk import Module, ModuleContext, route


class InputsWorkerModule(Module):
    id = "inputs_worker"

    @route.get("/inputs")
    async def read_inputs(self, ctx: ModuleContext) -> dict[str, Any]:
        out: dict[str, Any] = {"ports": ctx.inputs.ports}
        for port in ctx.inputs.ports:
            value = await ctx.inputs.get(port)
            ref = ctx.inputs.ref(port)
            if hasattr(value, "to_dict"):  # a DataFrame
                value = value.to_dict(orient="list")
            out[port] = {"value": value, "ref": str(ref) if ref is not None else None}
        return out
