"""Generate ``src/artifacts.generated.ts`` from ``mate.sdk.artifacts``.

The artifact type registry becomes a TS constant, and the JSON Schema of each json
artifact type's payload model (pydantic ``model_json_schema``) becomes TS interfaces.
Run from the repo root after changing ``mate.sdk.artifacts`` and commit the result::

    uv run python packages/module-sdk-ts/scripts/gen_artifact_types.py          # write
    uv run python packages/module-sdk-ts/scripts/gen_artifact_types.py --check  # fail on drift
"""

from __future__ import annotations

import json
import sys
from pathlib import Path
from typing import Any

from pydantic.json_schema import models_json_schema

from mate.sdk.artifacts import ARTIFACT_TYPES

OUT = Path(__file__).resolve().parents[1] / "src" / "artifacts.generated.ts"
_SCALARS = {"string": "string", "integer": "number", "number": "number", "boolean": "boolean"}


def _ts_type(schema: dict[str, Any]) -> str:
    if "$ref" in schema:
        return schema["$ref"]
    if "anyOf" in schema:
        return " | ".join(_ts_type(s) for s in schema["anyOf"])
    kind = schema.get("type")
    if kind == "array":
        item = _ts_type(schema["items"])
        return f"({item})[]" if "|" in item else f"{item}[]"
    if kind == "null":
        return "null"
    if kind in _SCALARS:
        return _SCALARS[kind]
    raise SystemExit(f"gen_artifact_types: unsupported JSON Schema {schema!r}")


def _doc(text: str, indent: str = "") -> list[str]:
    return [f"{indent}/** {text.replace('``', '`')} */"] if text else []


def _interface(name: str, schema: dict[str, Any]) -> list[str]:
    required = set(schema.get("required", []))
    lines = [*_doc(schema.get("description", "")), f"export interface {name} {{"]
    for prop, sub in schema.get("properties", {}).items():
        optional = "" if prop in required else "?"
        lines.append(f"  {prop}{optional}: {_ts_type(sub)};")
    if schema.get("additionalProperties"):
        # The payload models are lenient (extra="allow"): producers add their own keys.
        lines.append("  [key: string]: unknown;")
    return [*lines, "}", ""]


def render() -> str:
    typed = {t.id: t.model for t in ARTIFACT_TYPES.values() if t.model is not None}
    _, schema = models_json_schema(
        [(model, "validation") for model in typed.values()], ref_template="{model}"
    )
    lines = [
        "// Generated from mate.sdk.artifacts by packages/module-sdk-ts/scripts/"
        "gen_artifact_types.py - do not edit.",
        "// Regenerate: uv run python packages/module-sdk-ts/scripts/gen_artifact_types.py",
        "",
        "/** Every registered artifact type: `${id}@${version}`, storage, default viz shape. */",
        "export const ARTIFACT_TYPES = {",
    ]
    for t in ARTIFACT_TYPES.values():
        fields = {
            "version": t.version,
            "storage": t.storage,
            "shape": t.shape,
            "description": t.description,
        }
        body = ", ".join(f"{k}: {json.dumps(v)}" for k, v in fields.items())
        lines.append(f"  {t.id}: {{ {body} }},")
    lines += [
        "} as const;",
        "",
        "export type ArtifactTypeId = keyof typeof ARTIFACT_TYPES;",
        "",
    ]
    for name, sub in schema["$defs"].items():
        lines += _interface(name, sub)
    lines.append("/** The payload of each json artifact type, by artifact type id. */")
    lines.append("export interface ArtifactPayloads {")
    lines += [f"  {type_id}: {model.__name__};" for type_id, model in typed.items()]
    lines.append("}")
    return "\n".join(lines) + "\n"


def main() -> None:
    text = render()
    if "--check" in sys.argv[1:]:
        if not OUT.exists() or OUT.read_text() != text:
            raise SystemExit(f"{OUT} is stale - rerun without --check.")
        return
    OUT.write_text(text)


if __name__ == "__main__":
    main()
