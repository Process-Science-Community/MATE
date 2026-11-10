"""Render `import_benchmark.py` JSON output as the Markdown tables in docs/benchmarks/.

Usage:
    uv run python scripts/benchmark/render_benchmark.py bench.json
"""

from __future__ import annotations

import json
import statistics
import sys
from pathlib import Path
from typing import Any


def _s(v: float | None) -> str:
    if v is None:
        return "–"
    if v >= 120:
        return f"{v / 60:.1f} min"
    return f"{v:.1f} s"


def _mib(b: int | None) -> str:
    return "–" if not b else f"{b / 2**20:,.0f} MiB"


def _n(v: int | None) -> str:
    return "–" if v is None else f"{v:,}"


def render(data: dict[str, Any]) -> str:
    logs = data["logs"]
    out: list[str] = []

    out.append("| Log | Events | Cases | Variants | Import | Peak api mem (import) "
               "| Precompute min | median | max | Modules ok |")
    out.append("|---|--:|--:|--:|--:|--:|--:|--:|--:|--:|")
    for log in logs:
        ok = [m["wall_s"] for m in log["modules"].values()
              if m.get("job_status") == "completed" and m.get("wall_s") is not None]
        total = len(log["modules"])
        stats = (min(ok), statistics.median(ok), max(ok)) if ok else (None, None, None)
        out.append(
            f"| {log['key']} | {_n(log['events'])} | {_n(log['cases'])} | {_n(log['variants'])} "
            f"| {_s(log.get('import_wall_s'))} | {_mib(log['peak_mem_bytes_import'])} "
            f"| {_s(stats[0])} | {_s(stats[1])} | {_s(stats[2])} | {len(ok)}/{total} |"
        )

    modules = sorted({m for log in logs for m in log["modules"]})
    out.append("")
    out.append("| Module | " + " | ".join(log["key"] for log in logs) + " |")
    out.append("|---|" + "--:|" * len(logs))
    for mod in modules:
        cells = []
        for log in logs:
            m = log["modules"].get(mod)
            if m is None:
                cells.append("–")
            elif m.get("job_status") == "completed":
                cells.append(_s(m.get("wall_s")))
            else:
                cells.append(m.get("job_status") or m.get("status") or "?")
        out.append(f"| `{mod}` | " + " | ".join(cells) + " |")
    return "\n".join(out) + "\n"


if __name__ == "__main__":
    print(render(json.loads(Path(sys.argv[1]).read_text())))
