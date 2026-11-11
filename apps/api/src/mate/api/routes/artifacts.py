"""/api/v1/artifacts - the artifact workspace (``mate.api.modules.artifacts``).

Lists, reads, renames, pins, downloads and deletes a user's artifacts - the typed
outputs its installed modules produced (default runs and run variants, indexed when
they settle or found by a list) and the JSON artifacts it uploaded - and serves the
content of any artifact it owns by ref, indexed or not (``GET /artifacts/content?ref=``:
how the web renders a cache-only dataset; a shared dashboard's viewer reads the owner's
default results there too). Someone else's artifact is a 404, like a missing one; a
hidden one (``state``) can still be renamed, (un)pinned and deleted.
"""

from __future__ import annotations

import asyncio
import json
from typing import Any, Literal

from fastapi import APIRouter, HTTPException, Request, Response
from fastapi.responses import FileResponse
from pydantic import BaseModel, ConfigDict, Field, ValidationError
from sqlalchemy import delete, or_, select
from sqlalchemy.ext.asyncio import AsyncSession
from starlette.datastructures import UploadFile

from mate.api.auth import CurrentUserDep, get_owned_event_log
from mate.api.config import get_settings
from mate.api.db.models import Artifact, EventLog
from mate.api.db.session import SessionDep
from mate.api.modules import get_module_loader
from mate.api.modules.artifacts import (
    ArtifactState,
    ArtifactTooLargeError,
    NotPreviewableError,
    artifact_file,
    delete_upload,
    discover,
    forget_variant,
    listed,
    read_content,
    save_upload,
    states,
    workspace_ref,
)
from mate.api.modules.cache import ResultCache, is_run_variant
from mate.api.modules.installs import user_module_ids, user_owns_module
from mate.api.modules.processing import get_coordinator
from mate.api.schemas.common import UtcDateTime
from mate.api.sharing import user_can_read_log
from mate.api.uuid7 import uuid7_str
from mate.sdk.artifacts import (
    WORKSPACE_MODULE_ID,
    ArtifactRef,
    get_artifact_type,
    validate_artifact,
)

router = APIRouter(prefix="/artifacts", tags=["artifacts"])

_NOT_FOUND = "Artifact not found."
# Multipart framing around the file: boundaries, part headers, the form fields.
_MULTIPART_OVERHEAD = 64 * 1024
_MEDIA_TYPES = {".json": "application/json", ".parquet": "application/vnd.apache.parquet"}


class ArtifactOut(BaseModel):
    """One artifact in the user's workspace."""

    model_config = ConfigDict(from_attributes=True)

    id: str
    # `{log_id}/{module_id}[@{variant}]/{key}`, or `{log_id or "_"}/_workspace/{id}` for
    # an upload - what a run variant's `inputs` binds and `…/content?ref=` reads.
    ref: str
    # Artifact type id (`petri_net`) and its version (`1`).
    artifact_type: str
    type_version: int
    source: Literal["run", "upload"]
    log_id: str | None = None
    # The producer; None for an upload.
    module_id: str | None = None
    module_name: str | None = None
    dataset_id: str | None = None
    # The run variant that produced it; None for a default-run result or an upload.
    variant: str | None = None
    key: str
    name: str
    pinned: bool
    producer_version: str | None = None
    # The producer's loaded version now; differs from `producer_version` when the
    # result predates an upgrade or rollback. None for an upload or an unloaded module.
    current_version: str | None = None
    # The producing run's fingerprint when it was submitted.
    producer_hash: str | None = None
    # Lineage: the input refs the producing run had bound.
    inputs: list[str] = Field(default_factory=lambda: [])
    # A run variant's `{params, inputs, filter}`.
    spec: dict[str, Any] | None = None
    size_bytes: int | None = None
    created_at: UtcDateTime
    # When the content was last produced (not renamed or pinned).
    updated_at: UtcDateTime
    # `ready`: the file is there. A pinned run variant's is listed `running` while its
    # rerun is under way and `failed` once it failed (no content: rerun or delete it);
    # `missing` only answers a PATCH of a hidden row.
    state: ArtifactState = "ready"


class ArtifactConsumer(BaseModel):
    """An installed module with an input port of the artifact's type ("Use in…")."""

    module_id: str
    module_name: str
    port_id: str
    required: bool


class ArtifactLineageEntry(BaseModel):
    ref: str
    # None when the input is not (or no longer) in the workspace.
    id: str | None = None
    name: str | None = None
    artifact_type: str | None = None


class ArtifactDetail(ArtifactOut):
    consumers: list[ArtifactConsumer]
    # `inputs`, resolved against the workspace (same order).
    lineage: list[ArtifactLineageEntry]


class ArtifactPatch(BaseModel):
    model_config = ConfigDict(extra="forbid")

    name: str | None = Field(default=None, min_length=1, max_length=255)
    pinned: bool | None = None


class ArtifactUpload(BaseModel):
    artifact_type: str
    name: str | None = Field(default=None, min_length=1, max_length=255)
    log_id: str | None = None
    payload: Any


def _loaded() -> dict[str, Any]:
    try:
        return get_module_loader().loaded
    except HTTPException:
        return {}


def _out(row: Artifact, loaded: dict[str, Any], state: ArtifactState = "ready") -> ArtifactOut:
    producer = loaded.get(row.module_id or "")
    out = ArtifactOut.model_validate(row)
    out.module_name = producer.manifest.name if producer is not None else None
    out.current_version = producer.manifest.version if producer is not None else None
    out.state = state
    return out


async def _owned(
    session: AsyncSession, user_id: str, artifact_id: str, *, hidden: bool = False
) -> tuple[Artifact, ArtifactState]:
    """The user's artifact and its state - listed (or, with *hidden*, still there at
    all), its log not deleted, its producer still installed - or 404."""
    row = await session.get(Artifact, artifact_id)
    if row is None or row.user_id != user_id:
        raise HTTPException(status_code=404, detail=_NOT_FOUND)
    if row.module_id is not None and not await user_owns_module(session, user_id, row.module_id):
        raise HTTPException(status_code=404, detail=_NOT_FOUND)
    if row.log_id is not None:
        log_row = await session.get(EventLog, row.log_id)
        if log_row is None or log_row.deleted_at is not None:
            raise HTTPException(status_code=404, detail=_NOT_FOUND)
    state = (await states(session, [row])).get(row.id)
    if state is None or not (hidden or listed(row, state)):
        raise HTTPException(status_code=404, detail=_NOT_FOUND)
    return row, state


async def _content(user_id: str, ref: ArtifactRef) -> Response:
    def _read() -> bytes | None:
        path = artifact_file(user_id, ref)
        return None if path is None else read_content(path)

    try:
        body = await asyncio.to_thread(_read)
    except ArtifactTooLargeError as exc:
        raise HTTPException(status_code=413, detail="The artifact is too large to serve.") from exc
    except NotPreviewableError as exc:
        raise HTTPException(status_code=415, detail="A binary artifact has no preview.") from exc
    if body is None:
        raise HTTPException(status_code=404, detail="The artifact has not been produced.")
    return Response(content=body, media_type="application/json")


# -- reads --------------------------------------------------------------------


@router.get("", response_model=list[ArtifactOut])
async def list_artifacts(
    session: SessionDep,
    user: CurrentUserDep,
    log_id: str | None = None,
    artifact_type: str | None = None,
    pinned: bool | None = None,
) -> list[ArtifactOut]:
    """Your artifacts, newest first: uploads, and the outputs of the modules you have
    installed. A result is listed while its cache entry exists - one no run indexed yet
    is added on the way, and a re-imported one refreshed: *log_id*'s, else the newest
    logs' and those whose results changed last - and a pinned run variant's also while
    its rerun is under way or after it failed (``state``). An upload whose file has
    gone, and a run variant's whose dir has, is dropped."""
    owned = await user_module_ids(session, user.id)
    loaded = _loaded()
    await discover(
        session, user.id, log_id, {m: lm.manifest for m, lm in loaded.items() if m in owned}
    )
    query = (
        select(Artifact)
        .outerjoin(EventLog, EventLog.id == Artifact.log_id)
        .where(
            Artifact.user_id == user.id,
            or_(Artifact.log_id.is_(None), EventLog.deleted_at.is_(None)),
            or_(Artifact.module_id.is_(None), Artifact.module_id.in_(owned)),
        )
    )
    if log_id is not None:
        query = query.where(Artifact.log_id == log_id)
    if artifact_type is not None:
        known = get_artifact_type(artifact_type)
        query = query.where(Artifact.artifact_type == (known.id if known else artifact_type))
    if pinned is not None:
        query = query.where(Artifact.pinned.is_(pinned))
    rows = (
        (await session.execute(query.order_by(Artifact.created_at.desc(), Artifact.id.desc())))
        .scalars()
        .all()
    )
    found = await states(session, rows)
    return [_out(r, loaded, s) for r in rows if (s := found.get(r.id)) is not None and listed(r, s)]


@router.get("/content")
async def get_artifact_content_by_ref(
    ref: str, session: SessionDep, user: CurrentUserDep
) -> Response:
    """An artifact's content by ref - a default result, a run variant's or an upload,
    on your own logs and installed modules, indexed or not; a default result also on a
    log a dashboard shared with you shows (read from its owner's data). JSON artifacts
    come back as stored; a Parquet table as its first 200 rows, ``{columns, rows,
    total_rows, truncated}``. 413 over ``ARTIFACT_UPLOAD_MAX_BYTES``; reading a run
    variant's counts as a use for the variant GC."""
    try:
        parsed = ArtifactRef.parse(ref)
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    if parsed.module_id == WORKSPACE_MODULE_ID:
        row, _state = await _owned(session, user.id, parsed.key)
        if row.ref != str(parsed):
            raise HTTPException(status_code=404, detail=_NOT_FOUND)
        return await _content(user.id, parsed)
    if parsed.variant is not None and not is_run_variant(parsed.variant):
        raise HTTPException(status_code=404, detail=_NOT_FOUND)
    log_row = await session.get(EventLog, parsed.log_id)
    if log_row is None or log_row.deleted_at is not None:
        raise HTTPException(status_code=404, detail=_NOT_FOUND)
    # A shared dashboard's viewer reads the owner's default results, never a variant's
    # (as `loader._make_context` widens storage for route datasets).
    if log_row.user_id != user.id and (
        parsed.variant is not None or not await user_can_read_log(session, parsed.log_id, user.id)
    ):
        raise HTTPException(status_code=404, detail=_NOT_FOUND)
    producer = _loaded().get(parsed.module_id)
    declared = producer is not None and any(
        d.cache_key == parsed.key for d in producer.manifest.datasets
    )
    # Only a declared output: never another cache entry (`__ai_guidance`, …).
    if not declared or not await user_owns_module(session, user.id, parsed.module_id):
        raise HTTPException(status_code=404, detail=_NOT_FOUND)
    return await _content(log_row.user_id, parsed)


@router.get("/{artifact_id}", response_model=ArtifactDetail)
async def get_artifact(
    artifact_id: str, session: SessionDep, user: CurrentUserDep
) -> ArtifactDetail:
    """One artifact, with the installed modules that can take it as an input
    (``consumers``) and its inputs resolved against the workspace (``lineage``)."""
    row, state = await _owned(session, user.id, artifact_id)
    loaded = _loaded()
    owned = await user_module_ids(session, user.id)
    wanted = get_artifact_type(row.artifact_type)
    consumers = [
        ArtifactConsumer(
            module_id=mid, module_name=lm.manifest.name, port_id=port.id, required=port.required
        )
        for mid, lm in sorted(loaded.items())
        if mid in owned and wanted is not None
        for port in lm.manifest.inputs
        if get_artifact_type(port.artifact_type) is wanted
    ]
    known = {
        r.ref: r
        for r in (
            await session.execute(
                select(Artifact).where(Artifact.user_id == user.id, Artifact.ref.in_(row.inputs))
            )
        ).scalars()
    }
    lineage = [
        ArtifactLineageEntry(ref=ref, id=r.id, name=r.name, artifact_type=r.artifact_type)
        if (r := known.get(ref)) is not None
        else ArtifactLineageEntry(ref=ref)
        for ref in row.inputs
    ]
    return ArtifactDetail(
        **_out(row, loaded, state).model_dump(), consumers=consumers, lineage=lineage
    )


@router.get("/{artifact_id}/content")
async def get_artifact_content(
    artifact_id: str, session: SessionDep, user: CurrentUserDep
) -> Response:
    """The artifact's content, as ``GET /artifacts/content?ref=`` serves it."""
    row, _state = await _owned(session, user.id, artifact_id)
    return await _content(user.id, ArtifactRef.parse(row.ref))


@router.get("/{artifact_id}/download", response_class=FileResponse)
async def download_artifact(
    artifact_id: str, session: SessionDep, user: CurrentUserDep
) -> FileResponse:
    """The artifact as stored, whole and uncapped - a JSON artifact as ``.json``, a
    table as its ``.parquet`` file - named after the artifact."""
    row, _state = await _owned(session, user.id, artifact_id)
    path = await asyncio.to_thread(artifact_file, user.id, ArtifactRef.parse(row.ref))
    if path is None:
        raise HTTPException(status_code=404, detail="The artifact has not been produced.")
    name = row.name if row.name.lower().endswith(path.suffix) else f"{row.name}{path.suffix}"
    return FileResponse(
        path, media_type=_MEDIA_TYPES.get(path.suffix, "application/octet-stream"), filename=name
    )


# -- writes -------------------------------------------------------------------


async def _capped_body(request: Request, cap: int) -> bytes:
    chunks: list[bytes] = []
    size = 0
    async for chunk in request.stream():
        size += len(chunk)
        if size > cap:
            raise HTTPException(status_code=413, detail="The artifact is too large.")
        chunks.append(chunk)
    return b"".join(chunks)


async def _upload_body(request: Request) -> tuple[ArtifactUpload, str | None]:
    """The upload and, for a multipart one, its file name."""
    cap = get_settings().artifact_upload_max_bytes
    declared = request.headers.get("content-length", "")
    multipart = request.headers.get("content-type", "").startswith("multipart/form-data")
    # `request.form()` reads the whole body before we see the file: bound it up front.
    if multipart and not declared.isdigit():
        raise HTTPException(status_code=411, detail="A multipart upload needs a Content-Length.")
    if declared.isdigit() and int(declared) > cap + _MULTIPART_OVERHEAD:
        raise HTTPException(status_code=413, detail="The artifact is too large.")
    try:
        if not multipart:
            return ArtifactUpload.model_validate_json(await _capped_body(request, cap)), None
        form = await request.form(max_files=1, max_fields=8)
        file = form.get("file")
        if not isinstance(file, UploadFile):
            raise HTTPException(status_code=422, detail="Attach the artifact as `file`.")
        raw = await file.read(cap + 1)
        if len(raw) > cap:
            raise HTTPException(status_code=413, detail="The artifact is too large.")
        fields = {
            k: v
            for k in ("artifact_type", "name", "log_id")
            if isinstance(v := form.get(k), str) and v
        }
        return ArtifactUpload.model_validate({**fields, "payload": json.loads(raw)}), file.filename
    except (ValidationError, ValueError) as exc:  # bad JSON or fields; UnicodeError too
        raise HTTPException(status_code=422, detail=str(exc)) from exc


@router.post("", response_model=ArtifactOut, status_code=201)
async def upload_artifact(
    request: Request, session: SessionDep, user: CurrentUserDep
) -> ArtifactOut:
    """Add a JSON artifact to your workspace: a JSON body ``{artifact_type, name?,
    log_id?, payload}``, or multipart with the payload as ``file`` and the rest as
    form fields. Only JSON-stored artifact types (``petri_net``, ``bpmn``, …), checked
    against the type's schema (422 listing the problems); at most
    ``ARTIFACT_UPLOAD_MAX_BYTES`` (413; a multipart one needs a Content-Length, 411).
    The name is cut to 255 characters. PNML/BPMN files go through the Model Interop
    module instead. Its ref, ``{log_id or "_"}/_workspace/{id}``, binds any input port
    of its type in a run variant."""
    body, filename = await _upload_body(request)
    artifact_type = get_artifact_type(body.artifact_type)
    if artifact_type is None or artifact_type.storage != "json":
        raise HTTPException(
            status_code=422,
            detail=f"{body.artifact_type!r} is not an artifact type that can be uploaded - "
            "only JSON ones (PNML or BPMN files: import them in Model Interop).",
        )
    problems = validate_artifact(str(artifact_type), body.payload)
    if problems:
        raise HTTPException(
            status_code=422, detail=f"Not a valid {artifact_type}: " + "; ".join(problems[:20])
        )
    if body.log_id is not None:
        await get_owned_event_log(session, body.log_id, user.id)
    # `body.name` is validated; a multipart file name is not.
    name = (body.name or filename or artifact_type.id.replace("_", " ").capitalize())[:255]
    artifact_id = uuid7_str()
    try:
        size = await asyncio.to_thread(save_upload, user.id, artifact_id, json.dumps(body.payload))
        row = Artifact(
            id=artifact_id,
            user_id=user.id,
            log_id=body.log_id,
            ref=workspace_ref(body.log_id, artifact_id),
            artifact_type=artifact_type.id,
            type_version=artifact_type.version,
            source="upload",
            key=artifact_id,
            name=name,
            pinned=False,
            inputs=[],
            size_bytes=size,
        )
        session.add(row)
        await session.commit()
    except BaseException:
        # No row, no file: never leave an orphan behind (locally or in the S3 mirror).
        await asyncio.to_thread(delete_upload, user.id, artifact_id)
        raise
    return _out(row, {})


@router.patch("/{artifact_id}", response_model=ArtifactOut)
async def update_artifact(
    artifact_id: str, body: ArtifactPatch, session: SessionDep, user: CurrentUserDep
) -> ArtifactOut:
    """Rename or (un)pin, a hidden artifact too. A pinned run variant's artifact is
    never evicted by the variant GC - unless its rerun failed."""
    row, state = await _owned(session, user.id, artifact_id, hidden=True)
    if body.name is not None:
        row.name = body.name
    if body.pinned is not None:
        row.pinned = body.pinned
    await session.commit()
    return _out(row, _loaded(), state)


@router.delete("/{artifact_id}", status_code=204)
async def delete_artifact(artifact_id: str, session: SessionDep, user: CurrentUserDep) -> None:
    """Delete an upload, or the run variant that produced an artifact (with all its
    outputs), a hidden one too. A default-run result is the module's to manage: 409."""
    row, _state = await _owned(session, user.id, artifact_id, hidden=True)
    if row.source == "upload":
        await asyncio.to_thread(delete_upload, user.id, row.id)
    elif row.variant is None:
        raise HTTPException(
            status_code=409,
            detail="Default results are managed by the module - they update when it reruns.",
        )
    else:
        coordinator = get_coordinator()
        if coordinator is None:
            raise HTTPException(status_code=503, detail="Module processing is not running.")
        log_id, module_id = str(row.log_id), str(row.module_id)
        try:
            await coordinator.variants.delete(user.id, log_id, module_id, row.variant)
        except LookupError:
            # Installed (`_owned`) but not loaded - a failed load, a reload window: remove
            # the dir as `VariantRunner.delete` does (S3 mirror included), or the next
            # list indexes the variant again. A job still writing leaves a meta-less dir:
            # never indexed, and evicted by the GC as an orphan.
            cache = ResultCache(log_id, module_id, user.id, run_variant=row.variant)
            await asyncio.to_thread(cache.remove)
            await forget_variant(user.id, log_id, module_id, row.variant)
    # A statement, not `session.delete`: the variant's own delete may have dropped it.
    await session.execute(delete(Artifact).where(Artifact.id == row.id))
    await session.commit()
