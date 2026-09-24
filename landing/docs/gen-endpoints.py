#!/usr/bin/env python3
"""Regenerate the REST chapter's endpoint table from the API's own OpenAPI schema.

`content/40-interfaces.md` marks the region between

    <!-- generated: endpoint-table -->
    <!-- /generated -->

and this script rewrites everything in between: one row per route, with an
abridged request and response example. The schema is the API's, not a hand copy,
so the table cannot drift from the code — run it after changing route signatures,
Pydantic models, or response status codes.

    uv run python landing/docs/gen-endpoints.py          # from the repo root
    make docs                                            # then rebuild the site

The cells are deliberately abridged: `…` marks elided object fields or further
array items. `/openapi.json` stays authoritative — this table is the map, the
schema is the territory.
"""

from __future__ import annotations

import json
import re
import sys
from pathlib import Path
from typing import Any

REPO = Path(__file__).resolve().parents[2]
CHAPTER = REPO / "landing/docs/content/40-interfaces.md"
START = "<!-- generated: endpoint-table -->"
END = "<!-- /generated -->"

CELL_BUDGET = 90  # chars of JSON per cell: a few fields, then `…`
METHOD_RANK = {"GET": 0, "POST": 1, "PUT": 2, "PATCH": 3, "DELETE": 4}

# --- grouping -------------------------------------------------------------
# Order of the groups in the table, then the rules that assign a route to one.
GROUP_ORDER = [
    "Import",
    "Logs",
    "Log data",
    "OCEL",
    "Organisation",
    "Jobs",
    "Events",
    "Modules",
    "Datasets",
    "Dashboards",
    "Sharing",
    "AI",
    "Account",
    "System",
    "Admin",
    "Meta",
]

IMPORT_ROUTES = {
    ("POST", "/event-logs/stage"),
    ("POST", "/event-logs"),
    ("POST", "/event-logs/from-url"),
    ("POST", "/event-logs/probe-xml"),
    ("POST", "/event-logs/probe-json"),
}

# A few groups read better in workflow order than in alphabetical order.
ORDER_OVERRIDES: dict[str, list[tuple[str, str]]] = {
    "Import": [
        ("POST", "/event-logs/stage"),
        ("POST", "/event-logs"),
        ("POST", "/event-logs/from-url"),
        ("POST", "/event-logs/probe-xml"),
        ("POST", "/event-logs/probe-json"),
    ],
    "Modules": [("POST", "/modules/install"), ("POST", "/modules/restore-defaults")],
    "Dashboards": [("POST", "/dashboards/import")],
}

# Query parameters worth showing, in this order; the rest are only shown when required.
PARAMS_WANTED = ("limit", "offset", "cursor", "q", "status", "folder_id", "topic", "format")


def group_of(method: str, path: str) -> str:
    if path == "/health":
        return "Meta"
    if path.startswith("/event-logs"):
        if (method, path) in IMPORT_ROUTES:
            return "Import"
        if "/ocel/" in path:
            return "OCEL"
        return "Log data" if path.count("/") > 2 else "Logs"
    if path.startswith(("/folders", "/watched-folders")):
        return "Organisation"
    if path.startswith("/jobs"):
        return "Jobs"
    if path.startswith("/events"):
        return "Events"
    if path.startswith("/modules"):
        return "Modules"
    if path.startswith("/datasets"):
        return "Datasets"
    if path.startswith("/dashboards"):
        return "Dashboards"
    if path.startswith("/sharing"):
        return "Sharing"
    if path.startswith("/ai"):
        return "AI"
    if path.startswith(("/api-tokens", "/preferences", "/onboarding", "/usage")):
        return "Account"
    if path.startswith("/system"):
        return "System"
    if path.startswith("/admin"):
        return "Admin"
    raise SystemExit(f"unmapped route: {method} {path}")


# --- schema → example -----------------------------------------------------


def deref(node: Any, schemas: dict[str, Any], depth: int = 0) -> dict[str, Any]:
    """Resolve $ref / anyOf / oneOf / allOf down to a usable schema object."""
    if not isinstance(node, dict) or depth > 12:
        return node if isinstance(node, dict) else {}
    if "$ref" in node:
        return deref(schemas.get(node["$ref"].split("/")[-1], {}), schemas, depth + 1)
    for key in ("anyOf", "oneOf", "allOf"):
        if key in node:
            options = [
                o
                for o in node[key]
                if o.get("type") != "null" and not str(o.get("$ref", "")).endswith("NoneType")
            ]
            if not options:
                return {"type": "null"}
            first = deref(options[0], schemas, depth + 1)
            if key == "allOf":
                props: dict[str, Any] = {}
                for opt in node[key]:
                    props.update(deref(opt, schemas, depth + 1).get("properties", {}))
                if props:
                    first = {**first, "properties": props}
            return first
    return node


def placeholder(name: str, schema: dict[str, Any]) -> Any:
    if schema.get("enum"):
        return schema["enum"][0]
    fmt = schema.get("format", "")
    key = name.lower()
    if fmt == "date-time" or key.endswith("_at") or "timestamp" in key:
        return "2026-01-01T00:00:00Z"
    if fmt == "date":
        return "2026-01-01"
    if fmt == "binary":
        return "<binary>"
    if key.endswith("base_url"):
        return "https://api.openai.com/v1"
    if key.endswith(("_id", "_ids")) or key in {"id", "sub", "token"}:
        return "018f2c9a…"
    if "url" in key or key.endswith("_uri"):
        return "https://example.com/log.xes"
    if "email" in key:
        return "ada@example.com"
    if key in {"name", "title", "label"}:
        return "nightly import"
    if key in {"status", "state"}:
        return "ready"
    if key == "provider":
        return "openai"
    if key == "model":
        return "gpt-4o-mini"
    if key == "topic":
        return "job.progress"
    if key == "path":
        return "/data/inbox"
    return "…"


def sample(node: Any, schemas: dict[str, Any], name: str = "", depth: int = 0) -> Any:
    """One representative value for a schema — the shape, not the data."""
    schema = deref(node, schemas, depth)
    kind = schema.get("type")
    if kind is None:
        if "properties" in schema:
            kind = "object"
        elif "items" in schema:
            kind = "array"
        else:
            return None
    if kind == "null":
        return None
    if kind == "object":
        props = schema.get("properties", {})
        return {k: sample(v, schemas, k, depth + 1) for k, v in props.items()} or None
    if kind == "array":
        item = sample(schema.get("items", {}), schemas, name, depth + 1)
        return ["…"] if item is None else [item]
    if kind == "string":
        return placeholder(name, schema)
    if kind in {"integer", "number"}:
        # a numeric `*_at` is a unix timestamp, not a count
        if name.lower().endswith("_at") or "timestamp" in name.lower():
            return 1767225600
        default = schema.get("default")
        if isinstance(default, (int, float)):
            return default
        low = schema.get("minimum")
        if kind == "integer":
            return low if isinstance(low, int) and low > 0 else 25
        return 0.5
    if kind == "boolean":
        return bool(schema.get("default", False))
    return None


def sketch(value: Any, budget: int = CELL_BUDGET) -> str:
    """Compact JSON for one table cell; a trailing `…` marks elided members."""
    if isinstance(value, dict):
        out = "{"
        for key, sub in value.items():
            piece = f"{json.dumps(key)}:{sketch(sub, max(budget - len(out) - 26, 12))}"
            if len(out) + len(piece) + 2 > budget:
                return out + (",…}" if len(out) > 1 else "…}")
            out += ("," if len(out) > 1 else "") + piece
        return out + "}"
    if isinstance(value, list):
        if not value:
            return "[]"
        inner = sketch(value[0], budget - 6)
        return f"[{inner}]" if len(inner) + 2 <= budget else f"[{inner},…]"
    return json.dumps(value, ensure_ascii=False)


def query_string(query: dict[str, Any]) -> str:
    """`?limit=25&status=ready` — a query as it would actually be sent."""
    def value(v: Any) -> str:
        if v is None:
            return "null"
        if isinstance(v, bool):
            return "true" if v else "false"
        if isinstance(v, (int, float)):
            return str(v)
        if isinstance(v, (list, dict)):
            return json.dumps(v, ensure_ascii=False, separators=(",", ":"))
        return str(v)

    return "?" + "&".join(f"{k}={value(v)}" for k, v in query.items())


def wrap(text: str) -> str:
    """One code span. Each cell part is its own span so the builder can render a
    JSON part as a formatted block while the query string stays inline."""
    return f"`{text}`"


def cell_request(op: dict[str, Any], schemas: dict[str, Any]) -> str:
    params = [p for p in op.get("parameters", []) if p.get("in") == "query"]
    query: dict[str, Any] = {}
    for want in PARAMS_WANTED:
        for p in params:
            if p["name"] == want:
                query[p["name"]] = sample(p.get("schema", {}), schemas, p["name"])
    for p in params:
        if p.get("required") and p["name"] not in query:
            query[p["name"]] = sample(p.get("schema", {}), schemas, p["name"])
    body = None
    content = op.get("requestBody", {}).get("content", {})
    if "multipart/form-data" in content:
        shape = deref(content["multipart/form-data"].get("schema", {}), schemas)
        body = {
            k: sample(v, schemas, k, 1) for k, v in shape.get("properties", {}).items()
        } or None
    elif "application/json" in content:
        body = sample(content["application/json"].get("schema", {}), schemas)
    parts = []
    if query:
        parts.append(wrap(query_string(query)))
    if body is not None:
        parts.append(wrap(sketch(body)))
    return " · ".join(parts) if parts else "—"


def cell_response(op: dict[str, Any], schemas: dict[str, Any]) -> str:
    responses = op.get("responses", {})
    for status in sorted(responses):
        if not status.startswith("2"):
            continue
        content = responses[status].get("content", {})
        if "text/event-stream" in content:
            return f"{wrap('text/event-stream')} {wrap('{\"event\":\"job.progress\",\"progress\":0.4}')}"
        if "application/json" in content:
            value = sample(content["application/json"].get("schema", {}), schemas)
            return f"{wrap(status)} {wrap(sketch(value))}"
        if "text/csv" in content:
            return f"{wrap(status)} {wrap('<csv>')}"
        if "application/xml" in content or "text/xml" in content:
            return f"{wrap(status)} {wrap('<xml>')}"
        if "application/zip" in content or "application/octet-stream" in content:
            return f"{wrap(status)} {wrap('<binary>')}"
        if content:
            return f"{wrap(status)} {wrap(next(iter(content)))}"
        return f"{wrap(status)} —"
    return "—"


def sort_key(group: str, method: str, path: str) -> tuple:
    """Workflow order for the few routes listed in ORDER_OVERRIDES, then path
    (which also sorts `/modules` before `/modules/{module_id}`), then verb."""
    override = ORDER_OVERRIDES.get(group, [])
    if (method, path) in override:
        return (0, override.index((method, path)), "", 0)
    return (1, 0, path, METHOD_RANK.get(method, 9))


def render(spec: dict[str, Any]) -> str:
    schemas = spec.get("components", {}).get("schemas", {})
    routes: list[tuple[str, str, str]] = []
    for path, item in spec["paths"].items():
        for method in item:
            if method.upper() not in METHOD_RANK:
                continue
            trimmed = path.removeprefix("/api/v1")
            routes.append((group_of(method.upper(), trimmed), method.upper(), trimmed))
    routes.sort(key=lambda r: (GROUP_ORDER.index(r[0]), sort_key(r[0], r[1], r[2])))

    lookup = {
        (m.upper(), p.removeprefix("/api/v1")): op
        for p, item in spec["paths"].items()
        for m, op in item.items()
        if m.upper() in METHOD_RANK
    }
    # `:::wide` pins the column widths, so a JSON-heavy row wraps instead of
    # forcing the reader to scroll the card sideways on every line.
    lines = [
        ":::wide 12 22 33 33",
        "",
        "| Group | Endpoint | Request | Response |",
        "| --- | --- | --- | --- |",
    ]
    for group, method, path in routes:
        op = lookup[(method, path)]
        lines.append(
            f"| {group} | `{method} {path}` | {cell_request(op, schemas)} | "
            f"{cell_response(op, schemas)} |"
        )
    lines.append("")
    lines.append(":::")
    return "\n".join(lines)


def main() -> int:
    from mate.api.main import app  # imported here so `--help` stays cheap

    spec = app.openapi()
    table = render(spec)
    source = CHAPTER.read_text()
    if START not in source or END not in source:
        print(f"missing generated-region markers in {CHAPTER}", file=sys.stderr)
        return 1
    head, rest = source.split(START, 1)
    _, tail = rest.split(END, 1)
    data_rows = sum(1 for line in table.splitlines() if line.startswith("| ") and "---" not in line)
    CHAPTER.write_text(f"{head}{START}\n\n{table}\n\n{END}{tail}")
    print(f"{data_rows - 1} endpoints written to {CHAPTER.name}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
