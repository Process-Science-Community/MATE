from __future__ import annotations

from typing import Any

from mate.sdk import Module, ModuleContext, route


class BoardModule(Module):
    """Fixture with no panel and a manifest `frontend.board`."""

    id = "board_mod"

    @route.get("/counts")
    async def counts(self, ctx: ModuleContext) -> dict[str, Any]:
        return {"columns": ["activity", "count"], "rows": [["a", 1]]}
