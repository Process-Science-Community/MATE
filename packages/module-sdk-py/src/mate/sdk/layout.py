"""A module's declarative page layout: ``modules/<id>/layout.json``.

A module can ship its page as data instead of a hand-written TSX panel. The
layout is a grid of cards on the platform's 12-column board grid. A card is one of:

- ``viz``: a generic visualization bound to one of the module's manifest ``datasets``
  (unbound = a Studio placeholder that ``mate-module validate`` rejects).
- ``widget``: one of the module's ``frontend.widgets`` (TSX).
- ``text``: markdown (``config.markdown``), e.g. a heading and an intro.
- ``controls``: a form over page params (``config.source: "params"``, keys from the
  datasets' ``params_schema``) or over the module config (``source: "config"``, keys
  from ``config_schema``). ``config.keys: []`` means all of them.
- ``action``: a button that POSTs ``config.route`` (a module route, often a ``@job``)
  with the log id and the current page params.

``text``/``controls``/``action`` are layout-only: dashboards store ``widget``/``viz``.
Optional ``tabs`` split the page; an item's ``tab`` names one (unset = the first tab).
The item shape is the one a dashboard stores (``DashboardItem`` in
``apps/web/lib/dashboard-queries.ts``), so the dashboard canvas renders and edits it.

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

ElementKind = Literal["text", "controls", "action"]


class LayoutDatasetRef(BaseModel):
    module_id: str
    dataset_id: str


class LayoutTab(BaseModel):
    model_config = ConfigDict(extra="forbid")

    id: str = Field(pattern=r"^[a-z0-9][a-z0-9_-]*$", max_length=40)
    title: str = Field(min_length=1, max_length=60)


class LayoutItem(BaseModel):
    """One card. Mirrors the web ``DashboardItem``; unknown keys are kept."""

    model_config = ConfigDict(extra="allow")

    i: str = Field(min_length=1)
    kind: Literal["widget", "viz", "text", "controls", "action"] = "viz"
    tab: str | None = None
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
        c, name = self.config, f"{self.kind} card {self.i!r}"
        # No dataset_ref = a Studio placeholder ("Needs data"); `validate` flags it.
        if self.kind == "viz" and not self.viz_id:
            raise ValueError(f"{name} needs viz_id")
        if self.kind == "widget" and not self.widget_id:
            raise ValueError(f"{name} needs widget_id")
        if self.kind == "text" and not isinstance(c.get("markdown"), str):
            raise ValueError(f"{name} needs config.markdown (a string)")
        if self.kind in ("controls", "action") and not self.module_id:
            raise ValueError(f"{name} needs module_id")
        if self.kind == "controls":
            if c.get("source", "params") not in ("params", "config"):
                raise ValueError(f"{name}: config.source must be 'params' or 'config'")
            keys = c.get("keys", [])
            if not isinstance(keys, list) or not all(isinstance(k, str) for k in keys):
                raise ValueError(f"{name}: config.keys must be a list of strings")
        if self.kind == "action":
            route = c.get("route")
            if not isinstance(route, str) or not route.startswith("/") or "?" in route:
                raise ValueError(f"{name}: config.route must be a module path like '/run'")
            if not isinstance(c.get("label"), str | None):
                raise ValueError(f"{name}: config.label must be a string")
        if self.x + self.w > GRID_COLS:
            raise ValueError(f"card {self.i!r} overflows the {GRID_COLS}-column grid")
        return self


class ModuleLayout(BaseModel):
    # forbid: an unknown top-level key is an error, never silently dropped on save.
    model_config = ConfigDict(extra="forbid")

    version: Literal[1] = LAYOUT_VERSION
    # None = one page; dump(exclude_none) keeps tab-less files byte-identical.
    tabs: list[LayoutTab] | None = None
    items: list[LayoutItem] = Field(default_factory=list)

    @model_validator(mode="after")
    def _check_refs(self) -> ModuleLayout:
        ids = [it.i for it in self.items]
        if len(ids) != len(set(ids)):
            raise ValueError("card ids (`i`) must be unique")
        tab_ids = [t.id for t in self.tabs or []]
        if len(tab_ids) != len(set(tab_ids)):
            raise ValueError("tab ids must be unique")
        if "panel" in tab_ids:
            raise ValueError("tab id 'panel' is reserved for the module's TSX panel")
        for it in self.items:
            if it.tab is None:
                continue
            if self.tabs is None:
                raise ValueError(f"card {it.i!r} sets `tab` but the layout declares no `tabs`")
            if it.tab not in tab_ids:
                raise ValueError(
                    f"card {it.i!r} uses tab {it.tab!r}, which `tabs` does not declare "
                    f"(declared: {', '.join(tab_ids) or 'none'})"
                )
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


__all__ = [
    "LAYOUT_FILENAME",
    "ElementKind",
    "LayoutDatasetRef",
    "LayoutItem",
    "LayoutTab",
    "ModuleLayout",
]
