"""Module e2e_viz: static sample data in every dataset shape and adapter rule.

Test fixture for apps/web/e2e/studio-catalog.spec.ts; scripts/e2e-studio.sh copies it to
modules/e2e_viz for the run and removes it after. Nothing here reads the event log.
"""

from __future__ import annotations

from typing import Any

from fastapi import HTTPException

from mate.sdk import Module, ModuleContext, route

_CATS = ["Alpha", "Beta", "Gamma", "Delta"]
_GROUPS = ["North", "South", "West"]
_PERIODS = [f"2026-01-0{d}" for d in range(1, 7)]

SAMPLES: dict[str, Any] = {
    # table: an array of row objects (category x group, so heatmaps have two axes).
    "rows": {
        "kind": "sample_rows",
        "rows": [
            {
                "category": c,
                "group": g,
                "value": 10 + 7 * i + 3 * j,
                "value_b": 8 + 5 * i + 4 * j,
                "duration_s": 3600 * (i + 1) + 600 * j,
                "share_pct": round(100 * (i + 1) / 10, 1),
                "date": _PERIODS[i + j],
                "tags": [c.lower(), g.lower()],
            }
            for i, c in enumerate(_CATS)
            for j, g in enumerate(_GROUPS)
        ],
    },
    # graph: a Petri net with a silent (invisible) transition.
    "petri": {
        "kind": "petri_net",
        "places": [
            {"id": "p0", "label": "", "is_initial": True, "tokens": 1},
            {"id": "p1", "label": ""},
            {"id": "p2", "label": ""},
            {"id": "p3", "label": "", "is_final": True},
        ],
        "transitions": [
            {"id": "t0", "label": "register"},
            {"id": "t1", "label": "check"},
            {"id": "t2", "label": "tau", "is_invisible": True},
            {"id": "t3", "label": "ship"},
        ],
        "arcs": [
            {"source": "p0", "target": "t0"},
            {"source": "t0", "target": "p1"},
            {"source": "p1", "target": "t1"},
            {"source": "p1", "target": "t2"},
            {"source": "t1", "target": "p2"},
            {"source": "t2", "target": "p2"},
            {"source": "p2", "target": "t3"},
            {"source": "t3", "target": "p3"},
        ],
    },
    # tree: a process tree (operators -> X + *).
    "ptree": {
        "kind": "process_tree",
        "root": {
            "operator": "->",
            "children": [
                {"label": "register"},
                {"operator": "X", "children": [{"label": "approve"}, {"label": "reject"}]},
                {"operator": "+", "children": [{"label": "pack"}, {"label": "invoice"}]},
                {"operator": "*", "children": [{"label": "ship"}, {"label": "recheck"}]},
            ],
        },
    },
    # kpi: numeric leaves; *_s = duration, *_pct = percent, value_a/value_b = baseline.
    "kpis": {
        "kind": "sample_kpis",
        "cases": 120,
        "avg_cycle_time_s": 86400,
        "fitness": 0.93,
        "conversion_pct": 42.5,
        "revenue": {"value_a": 100, "value_b": 120, "lower_is_better": False},
    },
    # table: parallel arrays (a string index plus same-length number arrays).
    "series": {
        "kind": "sample_series",
        "periods": _PERIODS,
        "arrivals": [4, 6, 5, 8, 7, 9],
        "completions": [2, 5, 6, 6, 8, 7],
    },
    # table: a dense matrix with labels -> long rows {row, col, value}.
    "matrix": {
        "kind": "sample_matrix",
        "labels": ["A", "B", "C"],
        "matrix": [[1.0, 0.5, 0.2], [0.5, 1.0, 0.7], [0.2, 0.7, 1.0]],
    },
    # table: {columns: string[], rows: any[][]} -> zipped rows.
    "grid": {"columns": ["name", "score"], "rows": [["north", 3], ["south", 5], ["west", 2]]},
    # table: pre-binned histogram buckets + a stats object.
    "bins": {
        "kind": "sample_bins",
        "bins": [
            {"bin_start": 0, "bin_end": 10, "count": 3},
            {"bin_start": 10, "bin_end": 20, "count": 7},
            {"bin_start": 20, "bin_end": 30, "count": 4},
            {"bin_start": 30, "bin_end": 40, "count": 1},
        ],
        "stats": {"p50_s": 14, "p90_s": 31},
    },
    # kpi: an item array of {key, value}.
    "kpi_items": {
        "items": [
            {"key": "open", "label": "Open cases", "value": 12},
            {"key": "lead_time_s", "label": "Lead time", "value": 7200},
        ]
    },
    # graph: a directly-follows graph.
    "dfg": {
        "kind": "dfg",
        "activities": [
            {"id": "register", "label": "register", "frequency": 10},
            {"id": "check", "label": "check", "frequency": 9},
            {"id": "ship", "label": "ship", "frequency": 8},
        ],
        "edges": [
            {"source": "register", "target": "check", "frequency": 9, "performance_seconds": 600},
            {"source": "check", "target": "ship", "frequency": 8, "performance_seconds": 3600},
            {"source": "check", "target": "check", "frequency": 2, "performance_seconds": 60},
        ],
        "start_activities": {"register": 10},
        "end_activities": {"ship": 8},
    },
    # graph: a route that already returns the envelope ({shape, data}) is used verbatim.
    "envelope_graph": {
        "shape": "graph",
        "data": {
            "directed": True,
            "nodes": [
                {"id": "a", "label": "Start here", "value": 5},
                {"id": "b", "label": "Then here", "value": 3},
            ],
            "edges": [{"id": "ab", "source": "a", "target": "b", "value": 3}],
        },
    },
    # table: the envelope form without a schema (columns are inferred).
    "envelope_table": {"shape": "table", "data": {"rows": [{"label": "one", "n": 1}]}},
    # tree: a prefix tree (flat nodes with parent ids).
    "prefix": {
        "kind": "prefix_tree",
        "nodes": [
            {"id": 0, "label": "start", "parent": None, "frequency": 10},
            {"id": 1, "label": "register", "parent": 0, "frequency": 10},
            {"id": 2, "label": "check", "parent": 1, "frequency": 9},
            {"id": 3, "label": "cancel", "parent": 1, "frequency": 1},
        ],
    },
    # blob: plain text.
    "note": {"kind": "note", "value": "All sample data in this module is static."},
}


class E2eVizModule(Module):
    id = "e2e_viz"

    @route.get("/data/{name}")
    async def data(self, ctx: ModuleContext, name: str) -> Any:
        """One sample response per manifest dataset (`route: /data/<name>`)."""
        if name not in SAMPLES:
            raise HTTPException(status_code=404, detail=f"No sample {name!r}")
        return SAMPLES[name]
