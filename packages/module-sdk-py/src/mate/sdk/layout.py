"""A module's declarative page layout: ``modules/<id>/layout.json``.

A module can ship its page as data instead of a hand-written TSX panel. The
layout is a grid of cards on the platform's 12-column board grid. Each card is
either a generic visualization bound to one of the module's manifest
``datasets`` (``kind: "viz"``), or one of a module's ``frontend.widgets``
(``kind: "widget"``). The item shape is the same one a dashboard stores
(``DashboardItem`` in ``apps/web/lib/dashboard-queries.ts``), so the dashboard
canvas renders and edits it unchanged.

The file is optional and found by convention (no manifest key). The lite dev
mode's Studio writes it; it ships with the module folder like any other file.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator

LAYOUT_FILENAME = "layout.json"
LAYOUT_VERSION = 1
GRID_COLS = 12


class LayoutDatasetRef(BaseModel):
    module_id: str
    dataset_id: str


class LayoutItem(BaseModel):
    """One card. Mirrors the web ``DashboardItem``; unknown keys are kept."""

    model_config = ConfigDict(extra="allow")

    i: str = Field(min_length=1)
    kind: Literal["widget", "viz"] = "viz"
    module_id: str | None = None
    widget_id: str | None = None
    dataset_ref: LayoutDatasetRef | None = None
    viz_id: str | None = None
    mapping: dict[str, Any] = Field(default_factory=dict)
    title: str | None = None
    x: int = Field(ge=0, lt=GRID_COLS)
    y: int = Field(ge=0)
    w: int = Field(ge=1, le=GRID_COLS)
    h: int = Field(ge=1)
    config: dict[str, Any] = Field(default_factory=dict)

    @model_validator(mode="after")
    def _check_kind(self) -> LayoutItem:
        if self.kind == "viz" and (self.dataset_ref is None or not self.viz_id):
            raise ValueError(f"viz card {self.i!r} needs dataset_ref and viz_id")
        if self.kind == "widget" and not self.widget_id:
            raise ValueError(f"widget card {self.i!r} needs widget_id")
        if self.x + self.w > GRID_COLS:
            raise ValueError(f"card {self.i!r} overflows the {GRID_COLS}-column grid")
        return self


class ModuleLayout(BaseModel):
    version: Literal[1] = LAYOUT_VERSION
    items: list[LayoutItem] = Field(default_factory=list)

    @model_validator(mode="after")
    def _unique_ids(self) -> ModuleLayout:
        ids = [it.i for it in self.items]
        if len(ids) != len(set(ids)):
            raise ValueError("card ids (`i`) must be unique")
        return self

    @classmethod
    def load(cls, folder: Path) -> ModuleLayout | None:
        """Read ``<folder>/layout.json``; ``None`` when the module has none."""
        path = Path(folder) / LAYOUT_FILENAME
        if not path.is_file():
            return None
        return cls.model_validate(json.loads(path.read_text(encoding="utf-8")))

    def dump(self, folder: Path) -> Path:
        """Write ``<folder>/layout.json`` (stable, diff-friendly formatting)."""
        path = Path(folder) / LAYOUT_FILENAME
        data = self.model_dump(mode="json", exclude_none=True)
        path.write_text(json.dumps(data, indent=2) + "\n", encoding="utf-8")
        return path


__all__ = ["LAYOUT_FILENAME", "LayoutDatasetRef", "LayoutItem", "ModuleLayout"]
