"""Model Interop - converts, imports and exports process models.

The precompute converts whatever models are bound to the manifest ``inputs:``
(process tree -> Petri net, Petri net -> BPMN, BPMN -> Petri net) and caches each
result as a typed artifact. It chains off ``discovery.completed``, so the platform
runs Discovery first and re-runs this module whenever Discovery recomputes. Routes
export one of this module's own results as PNML or BPMN XML, import an uploaded
PNML / BPMN file as the ``imported_petri_net`` / ``imported_bpmn`` artifact. The
results have no routes of their own: the module page (layout.json) and dashboards render them as
cache-only datasets through the platform's artifact content endpoint. The
precompute reads only ``ctx.inputs`` and ``ctx.cache``, so run variants with other
input refs work unchanged.
"""

from __future__ import annotations

import asyncio
from collections.abc import Callable
from typing import Any

from fastapi import HTTPException, Response, UploadFile

from mate.sdk import Module, ModuleContext, job, on_event, route
from mate.sdk.artifacts import ArtifactRef, validate_artifact

from .convert import (
    bpmn_from_xml,
    bpmn_to_petri,
    petri_to_bpmn,
    petri_to_pnml,
    pnml_to_petri,
    tree_to_petri,
)

# (input port, output cache key = dataset id, converter). Each port id is its
# artifact type.
_CONVERTERS: tuple[tuple[str, str, Callable[[dict[str, Any]], dict[str, Any]]], ...] = (
    ("process_tree", "petri_net_from_process_tree", tree_to_petri),
    ("petri_net", "bpmn_from_petri_net", petri_to_bpmn),
    ("bpmn", "petri_net_from_bpmn", bpmn_to_petri),
)

# File format -> (artifact type, import cache key, download media type, importer).
_FORMATS: dict[str, tuple[str, str, str, Callable[[bytes], dict[str, Any]]]] = {
    "pnml": ("petri_net", "imported_petri_net", "application/xml", pnml_to_petri),
    "bpmn": ("bpmn", "imported_bpmn", "application/bpmn+xml", bpmn_from_xml),
}

# This module's own results per artifact type, in the order export falls back
# through when no `source` is given: converted models first, uploads last.
_SOURCES: dict[str, tuple[str, ...]] = {
    "petri_net": ("petri_net_from_process_tree", "petri_net_from_bpmn", "imported_petri_net"),
    "bpmn": ("bpmn_from_petri_net", "imported_bpmn"),
}

_MAX_UPLOAD_BYTES = 10 * 1024 * 1024


def _format(fmt: str) -> tuple[str, str, str, Callable[[bytes], dict[str, Any]]]:
    if fmt not in _FORMATS:
        raise HTTPException(status_code=404, detail=f"Unknown format {fmt!r}: pnml or bpmn.")
    return _FORMATS[fmt]


def _upload_key(key: str) -> str:
    # A durable `__<module id>_` entry: the platform keeps it when it empties the cache
    # before a re-run and persists it with the module's results (`ResultCache.clear`).
    return f"__interop_{key}"


class InteropModule(Module):
    id = "interop"

    @on_event("discovery.completed")
    @job(progress=True, title="Model Interop - convert models")
    async def precompute(self, ctx: ModuleContext, payload: dict[str, Any]) -> None:
        total = len(_CONVERTERS) + 1
        # Read the uploads before writing anything: in S3 mode the first read of a cold
        # cache downloads it whole, a write first would make it look warm and skip them.
        uploads = {
            key: await ctx.cache.get(_upload_key(key)) for _t, key, _m, _i in _FORMATS.values()
        }
        for i, (port, key, convert) in enumerate(_CONVERTERS):
            await ctx.progress.update(i, total=total, stage=key, message=key)
            model = await ctx.inputs.get(port)
            if model is None:
                await ctx.cache.delete(key)
            else:
                await ctx.cache.set(key, await asyncio.to_thread(convert, model))
        await ctx.progress.update(len(_CONVERTERS), total=total, stage="imports")
        for key, upload in uploads.items():
            if upload is not None:
                await ctx.cache.set(key, upload)
        await ctx.progress.update(total, total=total, stage="done", message="done")

    @route.get("/export/{fmt}")
    async def export(self, ctx: ModuleContext, fmt: str, source: str | None = None) -> Response:
        artifact_type, _key, media, _importer = _format(fmt)
        sources = _SOURCES[artifact_type]
        if source is not None and source not in sources:
            raise HTTPException(
                status_code=404,
                detail=f"Model Interop has no {artifact_type} named {source!r}: "
                f"one of {', '.join(sources)}.",
            )
        for name in (source,) if source else sources:
            model = await ctx.cache.get(name)
            if model is not None:
                break
        else:
            raise HTTPException(
                status_code=404,
                detail=f"Nothing to export: {' / '.join((source,) if source else sources)} "
                f"is not there for log {ctx.log_id}. Run Model Interop for the log or "
                "import a model first.",
            )
        problems = validate_artifact(artifact_type, model)
        if problems:
            raise HTTPException(
                status_code=422, detail=f"{name} is not a valid {artifact_type}: {problems[0]}"
            )
        content = await asyncio.to_thread(petri_to_pnml, model) if fmt == "pnml" else model["xml"]
        return Response(
            content=content,
            media_type=media,
            headers={"Content-Disposition": f'attachment; filename="{name}.{fmt}"'},
        )

    @route.post("/import/{fmt}")
    async def import_model(self, ctx: ModuleContext, fmt: str, file: UploadFile) -> dict[str, Any]:
        if ctx.read_only:  # a shared dashboard's viewer: the owner's imports stay theirs
            raise HTTPException(
                status_code=403,
                detail="This log is shared with you read-only: only its owner can import models.",
            )
        artifact_type, key, _media, importer = _format(fmt)
        data = await file.read(_MAX_UPLOAD_BYTES + 1)
        if len(data) > _MAX_UPLOAD_BYTES:
            raise HTTPException(
                status_code=413,
                detail=f"The file is larger than {_MAX_UPLOAD_BYTES // (1024 * 1024)} MB.",
            )
        try:
            model = await asyncio.to_thread(importer, data)
        except Exception as exc:  # pm4py raises a variety of parse errors
            ctx.logger.warning("interop.import_invalid", fmt=fmt, error=str(exc))
            raise HTTPException(
                status_code=422, detail=f"Not a readable {fmt.upper()} file: {exc}"
            ) from exc
        await ctx.cache.set(_upload_key(key), model)
        await ctx.cache.set(key, model)
        ctx.logger.info("interop.imported", fmt=fmt, dataset=key)
        return {
            "dataset": key,
            "artifact_type": artifact_type,
            "ref": str(ArtifactRef(ctx.log_id, ctx.module_id, key)),
        }
