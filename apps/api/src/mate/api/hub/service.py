"""Dataset hub service: publish, edit, use (copy-on-use), takedown, list.

Invariants (each pinned by a test):

* No hub path publishes a bus event; clients learn of a takedown from the 410 on
  their next request.
* Only ``hub.storage`` writes under ``data/datasets/``; :func:`use` writes the
  student's copy under ``data/users/{uid}/`` and never touches the revision.
* All SQL goes through SQLAlchemy core/ORM (``LIKE … ESCAPE``, ``count(distinct)``,
  joins) - portable to Postgres and SQLite.
* The curator's source log is never written to by :func:`publish`.
"""

from __future__ import annotations

import asyncio
import copy
import re
import weakref
from datetime import UTC, datetime
from typing import Any

import structlog
from fastapi import HTTPException
from sqlalchemy import Select, func, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from mate.api.auth.dependencies import CurrentUser, may_curate_datasets
from mate.api.auth.ownership import get_owned_event_log
from mate.api.config import get_settings
from mate.api.db.models import (
    EventLog,
    HubDataset,
    HubDatasetRevision,
    LogModuleRun,
    Team,
)
from mate.api.hub import storage
from mate.api.hub.access import live_team_ids, readable_clause, team_is_live
from mate.api.hub.gate import source_dataset_for
from mate.api.hub.licenses import get_license, license_dict
from mate.api.hub.stats import build_stats_and_preview
from mate.api.ingest.storage import log_paths
from mate.api.modules.cache import ResultCache
from mate.api.modules.installs import user_module_ids
from mate.api.modules.loader import ModuleLoader
from mate.api.path_segments import safe_segment
from mate.api.schemas.event_logs import EventLogDetail, PublishedDatasetRef, SourceDataset
from mate.api.schemas.hub_datasets import (
    DatasetCreate,
    DatasetDetail,
    DatasetLogRef,
    DatasetRevisionOut,
    DatasetSummary,
    DatasetUpdate,
    DatasetViewer,
    LicenseOut,
    TeamRef,
)
from mate.api.storage import sync as storage_sync
from mate.api.storage.quota import over_quota_sync
from mate.api.uuid7 import uuid7_str

log = structlog.get_logger(__name__)

SLUG_RE = re.compile(r"^[a-z0-9][a-z0-9-]{1,63}$")
TAG_RE = re.compile(r"^[a-z0-9][a-z0-9 _.-]{0,39}$")
DOI_RE = re.compile(r"^10\.\d{4,9}/\S+$")
# Rendered as ``href`` on the dataset page: absolute http(s) only, so neither a
# scheme-less value (resolves relative to /datasets/...) nor another scheme passes.
URL_RE = re.compile(r"^https?://[^\s/?#]+[^\s]*$", re.IGNORECASE)
URL_FIELDS = ("license_url", "source_url", "publisher_url")
MAX_TAGS = 20
SORTS = ("updated", "uses")

# The card fields (everything but the slug and the source).
CARD_FIELDS = (
    "title",
    "summary",
    "description_md",
    "license_id",
    "license_name",
    "license_url",
    "citation",
    "source_url",
    "source_doi",
    "publisher_name",
    "publisher_url",
    "tags",
    "allow_external_ai",
    "retention_until",
    "visibility",
    "audience_team_id",
)


def _utcnow() -> datetime:
    return datetime.now(UTC).replace(tzinfo=None)


def _422(detail: str) -> HTTPException:
    return HTTPException(status_code=422, detail=detail)


def _blank(value: object) -> bool:
    return value is None or (isinstance(value, str) and not value.strip())


def slug_ok(value: str) -> bool:
    return bool(SLUG_RE.fullmatch(value))


# ── card ───────────────────────────────────────────────────────────────────


async def validate_card(session: AsyncSession, fields: dict[str, Any], *, visibility: str) -> None:
    """422 on any card rule violation; normalises ``fields`` in place.

    ``fields`` holds the full (merged) card, plus ``namespace``/``name``.
    """
    for key in ("namespace", "name"):
        if not slug_ok(str(fields.get(key) or "")):
            raise _422(
                f"Invalid {key}: lowercase letters, digits and '-', 2-64 characters, "
                "starting with a letter or digit."
            )
    if _blank(fields.get("title")):
        raise _422("Title is required.")
    lic_id = str(fields.get("license_id") or "unknown")
    if get_license(lic_id) is None:
        raise _422(f"Unknown license {lic_id!r}.")
    if lic_id == "other" and (
        _blank(fields.get("license_name")) or _blank(fields.get("license_url"))
    ):
        raise _422("A license of type 'other' needs a license name and URL.")
    if visibility not in ("private", "restricted", "public"):
        raise _422(f"Unknown visibility {visibility!r}.")
    if visibility != "private":
        if lic_id == "unknown":
            raise _422("Choose a license before sharing the dataset beyond curators.")
        if _blank(fields.get("citation")):
            raise _422("A citation is required before sharing the dataset beyond curators.")
        if _blank(fields.get("publisher_name")):
            raise _422("A publisher is required before sharing the dataset beyond curators.")
    if visibility == "restricted":
        if not await team_is_live(session, fields.get("audience_team_id")):
            raise _422("Restricted visibility needs an existing audience team.")
    else:
        fields["audience_team_id"] = None
    tags = fields.get("tags") or []
    if not isinstance(tags, list):
        raise _422("Tags must be a list.")
    cleaned: list[str] = []
    for tag in tags:
        t = str(tag).strip().lower()
        if not TAG_RE.fullmatch(t):
            raise _422(f"Invalid tag {tag!r}.")
        if t not in cleaned:
            cleaned.append(t)
    if len(cleaned) > MAX_TAGS:
        raise _422(f"At most {MAX_TAGS} tags.")
    fields["tags"] = cleaned
    doi = fields.get("source_doi")
    if not _blank(doi):
        doi_s = str(doi).strip()
        if not DOI_RE.fullmatch(doi_s):
            raise _422("Source DOI must be a bare DOI such as 10.4121/12345678.")
        fields["source_doi"] = doi_s
    for key in URL_FIELDS:
        url = fields.get(key)
        if _blank(url):
            fields[key] = None
            continue
        url_s = str(url).strip()
        if not URL_RE.fullmatch(url_s):
            raise _422(f"{key} must be an absolute http(s) URL.")
        fields[key] = url_s


def compose_search_text(ds: HubDataset) -> str:
    parts = [ds.title, ds.summary or "", f"{ds.namespace}/{ds.name}", *(ds.tags or [])]
    return " ".join(p for p in parts if p).lower()


def license_out(ds: HubDataset) -> LicenseOut:
    return LicenseOut.model_validate(license_dict(ds.license_id, ds.license_name, ds.license_url))


# ── canonical results ──────────────────────────────────────────────────────


def _results_dir(user_id: str, log_id: str, module_id: str):  # -> Path
    # Not ResultCache(...).dir: its constructor mkdirs, and probing must not
    # create empty dirs for every module in the curator's tree.
    return (
        get_settings().module_results_dir_for(user_id)
        / safe_segment(log_id, "log id")
        / safe_segment(module_id, "module id")
    )


async def canonical_candidates(
    session: AsyncSession, loader: ModuleLoader, log_row: EventLog, curator: CurrentUser
) -> dict[str, dict[str, Any]]:
    """Modules whose results on the curator's log may be shipped with a dataset.

    Only a precompute module with a fresh ``ready`` run row qualifies. Its run row
    proves the results describe the log's current data: every edit, reimport and
    filter commit bumps ``data_revision``. Results of on-demand modules never
    ship. They judge freshness by the Parquet mtime alone (a committed filter
    leaves it unchanged), and some write results computed against a *second* log
    into this log's cache dir (``pcomp``, ``process_comparison``) - shipping them
    would leak the curator's other logs.

    Each module dir is hydrated first: in S3 mode the reaper evicts result dirs
    on their own, and a cold cache must not silently ship nothing.
    """
    out: dict[str, dict[str, Any]] = {}
    for mid in sorted(await user_module_ids(session, curator.id)):
        if mid not in loader.loaded or not loader.has_precompute(mid):
            continue
        version = loader.run_version(mid)
        run = await session.get(LogModuleRun, (log_row.id, mid))
        if (
            run is None
            or run.status != "ready"
            or int(run.data_revision) != int(log_row.data_revision or 0)
            or (run.module_version is not None and run.module_version != version)
        ):
            continue
        dir_ = _results_dir(curator.id, log_row.id, mid)
        await storage_sync.hydrate_dir(dir_)
        files = await asyncio.to_thread(storage.result_files, dir_)
        if not files:
            continue
        out[mid] = {
            "module_version": version,
            "config_hash": await loader.config_fingerprint(session, mid, curator.id),
            "config_is_default": await loader.config_is_default(session, mid, curator.id),
            "keys": sorted({f.stem for f in files}),
        }
    return out


async def _ineligible_reason(
    session: AsyncSession, loader: ModuleLoader, log_row: EventLog, curator: CurrentUser, mid: str
) -> str:
    if mid not in await user_module_ids(session, curator.id) or mid not in loader.loaded:
        return "not installed"
    if not loader.has_precompute(mid):
        return "only results of modules that precompute on import can be published"
    if not storage.result_files(_results_dir(curator.id, log_row.id, mid)):
        return "no results on this log"
    return "results are not fresh (run the module on the current data first)"


async def publish_candidates(
    session: AsyncSession, loader: ModuleLoader, curator: CurrentUser, log_id: str
) -> list[dict[str, Any]]:
    """What the publish dialog offers: exactly the modules :func:`publish` ships by
    default, with a flag when the curator's settings differ from the defaults (a
    student on default settings would then recompute instead of reading them)."""
    src = await get_owned_event_log(session, log_id, curator.id)
    candidates = await canonical_candidates(session, loader, src, curator)
    out: list[dict[str, Any]] = []
    for mid, info in candidates.items():
        loaded = loader.loaded.get(mid)
        out.append(
            {
                "module_id": mid,
                "name": loaded.manifest.name if loaded is not None else mid,
                "config_is_default": bool(info.get("config_is_default", True)),
            }
        )
    return out


# ── publish / edit ─────────────────────────────────────────────────────────


async def publish(
    session: AsyncSession, loader: ModuleLoader, curator: CurrentUser, body: DatasetCreate
) -> HubDataset:
    src = await get_owned_event_log(session, body.source_log_id, curator.id)
    if src.dataset_revision_id is not None:
        raise HTTPException(status_code=409, detail="A derived log cannot be republished.")
    if src.status != "ready":
        raise HTTPException(status_code=409, detail="Only a ready event log can be published.")
    if src.log_model != "case_centric":
        raise HTTPException(
            status_code=409, detail="Only case-centric event logs can be published (for now)."
        )
    if src.active_filter:
        raise HTTPException(status_code=409, detail="Clear the committed filter before publishing.")

    fields = body.model_dump(exclude={"source_log_id", "modules"})
    await validate_card(session, fields, visibility=body.visibility)
    namespace, name = fields.pop("namespace"), fields.pop("name")
    clash = await session.execute(
        select(HubDataset.id).where(HubDataset.namespace == namespace, HubDataset.name == name)
    )
    if clash.first() is not None:
        raise HTTPException(status_code=409, detail="Dataset slug already exists.")

    candidates = await canonical_candidates(session, loader, src, curator)
    if body.modules is not None:
        bad = [m for m in body.modules if m not in candidates]
        if bad:
            reasons = [
                f"{m}: {await _ineligible_reason(session, loader, src, curator, m)}" for m in bad
            ]
            raise _422("Module results cannot be published - " + "; ".join(reasons))
        chosen = {m: candidates[m] for m in body.modules}
    else:
        chosen = candidates

    await storage_sync.hydrate_log(curator.id, src.id)
    for mid in chosen:
        await storage_sync.hydrate_dir(_results_dir(curator.id, src.id, mid))

    ds_id, rev_id = uuid7_str(), uuid7_str()
    rev_dir, nbytes, sha = await asyncio.to_thread(
        storage.snapshot_into_revision,
        log_paths(src.id, curator.id),
        {m: _results_dir(curator.id, src.id, m) for m in chosen},
        ds_id,
        1,
    )
    try:
        stats, preview, n_act = await build_stats_and_preview(src, curator.id)
    except Exception:
        await asyncio.to_thread(storage.purge_revision, ds_id, 1)
        raise

    now = _utcnow()
    ds = HubDataset(
        id=ds_id,
        namespace=namespace,
        name=name,
        current_revision_id=rev_id,
        status="active",
        created_by=curator.id,
        created_at=now,
        updated_at=now,
        published_at=now if body.visibility != "private" else None,
        **fields,
    )
    ds.search_text = compose_search_text(ds)
    rev = HubDatasetRevision(
        id=rev_id,
        dataset_id=ds_id,
        number=1,
        source_log_id=src.id,
        source_format=src.source_format,
        log_model=src.log_model,
        detected_schema=copy.deepcopy(src.detected_schema),
        column_roles=copy.deepcopy(src.column_roles),
        column_overrides=copy.deepcopy(src.column_overrides),
        events_count=src.events_count,
        cases_count=src.cases_count,
        variants_count=src.variants_count,
        activities_count=n_act,
        date_min=src.date_min,
        date_max=src.date_max,
        bytes_parquet=nbytes,
        events_sha256=sha,
        stats=stats,
        preview=preview,
        canonical_results=chosen,
        created_by=curator.id,
        created_at=now,
    )
    session.add(ds)
    await session.flush()
    session.add(rev)
    try:
        await session.commit()
    except IntegrityError as exc:
        await session.rollback()
        await asyncio.to_thread(storage.purge_revision, ds_id, 1)
        raise HTTPException(status_code=409, detail="Dataset slug already exists.") from exc
    log.info(
        "hub.dataset_published",
        dataset_id=ds_id,
        slug=f"{namespace}/{name}",
        source_log_id=src.id,
        modules=sorted(chosen),
        bytes=nbytes,
        rev_dir=str(rev_dir),
    )
    return ds


async def update_card(session: AsyncSession, ds: HubDataset, body: DatasetUpdate) -> HubDataset:
    merged: dict[str, Any] = {k: getattr(ds, k) for k in CARD_FIELDS}
    merged["namespace"], merged["name"] = ds.namespace, ds.name
    for key in body.model_fields_set:
        merged[key] = getattr(body, key)
    if merged.get("title") is None:
        raise _422("Title is required.")
    if merged.get("summary") is None:
        merged["summary"] = ""
    if merged.get("license_id") is None:
        merged["license_id"] = "unknown"
    if merged.get("allow_external_ai") is None:
        merged["allow_external_ai"] = True
    if merged.get("tags") is None:
        merged["tags"] = []
    if merged.get("visibility") is None:
        merged["visibility"] = "private"
    await validate_card(session, merged, visibility=str(merged["visibility"]))
    for key in CARD_FIELDS:
        setattr(ds, key, merged[key])
    ds.search_text = compose_search_text(ds)
    now = _utcnow()
    ds.updated_at = now
    if ds.published_at is None and ds.visibility != "private":
        ds.published_at = now
    await session.commit()
    return ds


# ── use (copy-on-use) ──────────────────────────────────────────────────────

# One lock per (user, revision): two concurrent "use" clicks must not both copy.
# Single-process (prod runs one uvicorn process, like the in-memory job queue).
_USE_LOCKS: weakref.WeakValueDictionary[tuple[str, str], asyncio.Lock] = (
    weakref.WeakValueDictionary()
)


def _use_lock(user_id: str, rev_id: str) -> asyncio.Lock:
    key = (user_id, rev_id)
    lock = _USE_LOCKS.get(key)
    if lock is None:
        lock = asyncio.Lock()
        _USE_LOCKS[key] = lock
    return lock


async def current_revision(session: AsyncSession, ds: HubDataset) -> HubDatasetRevision:
    rev = (
        await session.get(HubDatasetRevision, ds.current_revision_id)
        if ds.current_revision_id
        else None
    )
    if rev is None:
        raise HTTPException(status_code=404, detail="Dataset not found.")
    return rev


def license_snapshot(ds: HubDataset, rev: HubDatasetRevision) -> dict[str, Any]:
    return {
        "dataset_id": ds.id,
        "slug": f"{ds.namespace}/{ds.name}",
        "title": ds.title,
        "revision": rev.number,
        "license_id": ds.license_id,
        "license_name": ds.license_name,
        "license_url": ds.license_url,
        "citation": ds.citation,
        "publisher_name": ds.publisher_name,
        "publisher_url": ds.publisher_url,
        "allow_external_ai": bool(ds.allow_external_ai),
    }


async def _copies_of(session: AsyncSession, user_id: str, rev_id: str) -> list[EventLog]:
    """The user's live logs carrying ``rev_id``'s lineage, newest first (direct
    copies and duplicates of them)."""
    return list(
        (
            await session.execute(
                select(EventLog)
                .where(
                    EventLog.user_id == user_id,
                    EventLog.dataset_revision_id == rev_id,
                    EventLog.deleted_at.is_(None),
                )
                .order_by(EventLog.created_at.desc(), EventLog.id.desc())
            )
        ).scalars()
    )


def _is_direct_copy(row: EventLog) -> bool:
    """A log made by :func:`use`, not a duplicate of one (``duplicate_log`` marks
    its snapshot with ``duplicate_of``)."""
    snap = row.license_snapshot
    return not (isinstance(snap, dict) and snap.get("duplicate_of"))


def _copy_name(title: str, taken: set[str]) -> str:
    """``title``, or ``title (n)`` with the smallest n >= 2 not in ``taken``."""
    base = title[:255]
    if base not in taken:
        return base
    n = 2
    while True:
        suffix = f" ({n})"
        name = base[: 255 - len(suffix)] + suffix
        if name not in taken:
            return name
        n += 1


async def use(
    session: AsyncSession, ds: HubDataset, user: CurrentUser, *, new_copy: bool
) -> tuple[EventLog, bool]:
    """Copy the dataset's current revision into a new log owned by ``user``.

    Idempotent per (user, revision) unless ``new_copy``. A synchronous copy, not a
    job (Parquet only - 18 MB for the 579 MB course XES). Emits no bus event and
    no ``log.imported``: modules seed lazily from the shipped results on open.
    """
    if ds.status != "active":
        raise HTTPException(status_code=404, detail="Dataset not found.")
    rev = await current_revision(session, ds)
    if rev.purged_at is not None:
        raise HTTPException(status_code=404, detail="Dataset not found.")

    async with _use_lock(user.id, rev.id):
        copies = await _copies_of(session, user.id, rev.id)
        if not new_copy:
            existing = next((r for r in copies if _is_direct_copy(r)), None)
            if existing is not None:
                return existing, False

        if await asyncio.to_thread(over_quota_sync):
            raise HTTPException(
                status_code=507,
                detail="Storage quota reached. Delete data or have the operator raise "
                "STORAGE_S3_QUOTA_BYTES.",
            )

        new_id = uuid7_str()
        rev_dir = storage.revision_dir(ds.id, rev.number)
        module_ids = sorted((rev.canonical_results or {}).keys())
        try:
            await asyncio.to_thread(storage.hydrate_revision, rev_dir, module_ids)
            await asyncio.to_thread(storage.copy_into_user, rev_dir, user.id, new_id, module_ids)
        except Exception as exc:
            log.exception("hub.use_copy_failed", dataset_id=ds.id, user_id=user.id)
            await asyncio.to_thread(storage.remove_user_copy, user.id, new_id)
            raise HTTPException(status_code=500, detail="Copying the dataset failed.") from exc

        now = _utcnow()
        row = EventLog(
            id=new_id,
            user_id=user.id,
            name=_copy_name(ds.title, {r.name for r in copies}),
            description=ds.summary or None,
            source_format=rev.source_format,
            source_filename=f"{ds.namespace}/{ds.name}@r{rev.number}",
            log_model=rev.log_model or "case_centric",
            status="ready",
            data_revision=0,
            events_count=rev.events_count,
            cases_count=rev.cases_count,
            variants_count=rev.variants_count,
            date_min=rev.date_min,
            date_max=rev.date_max,
            detected_schema=copy.deepcopy(rev.detected_schema),
            column_roles=copy.deepcopy(rev.column_roles),
            column_overrides=copy.deepcopy(rev.column_overrides),
            mapping_needs_review=False,
            active_filter=None,
            folder_id=None,
            position=0,
            created_at=now,
            imported_at=now,
            dataset_revision_id=rev.id,
            license_snapshot=license_snapshot(ds, rev),
        )
        session.add(row)
        try:
            await session.commit()
        except Exception as exc:
            await session.rollback()
            await asyncio.to_thread(storage.remove_user_copy, user.id, new_id)
            raise HTTPException(status_code=500, detail="Copying the dataset failed.") from exc

        await storage_sync.persist_log(user.id, new_id)
        for mid in module_ids:
            await storage_sync.persist_dir(ResultCache(new_id, mid, user.id).dir)
        log.info("hub.dataset_used", dataset_id=ds.id, revision=rev.number, log_id=new_id)
        return row, True


# ── takedown ───────────────────────────────────────────────────────────────


async def _derived_log_ids(session: AsyncSession, ds: HubDataset) -> list[str]:
    rows = await session.execute(
        select(EventLog.id)
        .join(HubDatasetRevision, EventLog.dataset_revision_id == HubDatasetRevision.id)
        .where(HubDatasetRevision.dataset_id == ds.id)
    )
    return [r[0] for r in rows]


async def disable(
    session: AsyncSession, ds: HubDataset, actor: CurrentUser, reason: str
) -> HubDataset:
    from mate.api.jobs.runtime import get_job_runtime

    ds.status = "disabled"
    ds.disabled_at = _utcnow()
    ds.disabled_by = actor.id
    ds.disabled_reason = reason.strip()
    ds.updated_at = _utcnow()
    await session.commit()
    ids = await _derived_log_ids(session, ds)
    if ids:
        # Stop queued/running precomputes on the copies (no bus event: owners see
        # the 410 on their next request).
        await get_job_runtime().cancel_for_logs(ids)
    log.info("hub.dataset_disabled", dataset_id=ds.id, derived_logs=len(ids))
    return ds


async def enable(session: AsyncSession, ds: HubDataset) -> HubDataset:
    rev = await current_revision(session, ds)
    if rev.purged_at is not None:
        raise HTTPException(
            status_code=409, detail="The dataset's data was purged; it cannot be re-enabled."
        )
    ds.status = "active"
    ds.disabled_at = None
    ds.disabled_by = None
    ds.disabled_reason = None
    ds.updated_at = _utcnow()
    await session.commit()
    return ds


async def purge(
    session: AsyncSession, ds: HubDataset, actor: CurrentUser, confirm: str
) -> HubDataset:
    """Delete the revision's files for good. Derived logs stay blocked (410);
    their owners can still delete them."""
    if ds.status != "disabled":
        raise HTTPException(status_code=409, detail="Disable the dataset before purging it.")
    if confirm != f"{ds.namespace}/{ds.name}":
        raise _422("Type the dataset's namespace/name to confirm the purge.")
    rev = await current_revision(session, ds)
    try:
        await asyncio.to_thread(storage.purge_revision, ds.id, rev.number, strict=True)
    except Exception as exc:
        # Nothing is stamped: the admin UI must not say "purged" while files remain.
        raise HTTPException(
            status_code=500, detail="Purging the dataset's files failed; nothing was recorded."
        ) from exc
    if rev.purged_at is None:
        rev.purged_at = _utcnow()
        rev.purged_by = actor.id
    ds.updated_at = _utcnow()
    await session.commit()
    log.info("hub.dataset_purged", dataset_id=ds.id, revision=rev.number)
    return ds


# ── reads ──────────────────────────────────────────────────────────────────


def _uses_subquery() -> Any:
    return (
        select(func.count(func.distinct(EventLog.user_id)))
        .select_from(EventLog)
        .join(HubDatasetRevision, EventLog.dataset_revision_id == HubDatasetRevision.id)
        .where(HubDatasetRevision.dataset_id == HubDataset.id, EventLog.deleted_at.is_(None))
        .correlate(HubDataset)
        .scalar_subquery()
    )


def _escape_like(token: str) -> str:
    return token.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")


def _summary(ds: HubDataset, rev: HubDatasetRevision | None, uses: int) -> dict[str, Any]:
    return {
        "id": ds.id,
        "namespace": ds.namespace,
        "name": ds.name,
        "slug": f"{ds.namespace}/{ds.name}",
        "title": ds.title,
        "summary": ds.summary or "",
        "license": license_out(ds),
        "tags": list(ds.tags or []),
        "publisher_name": ds.publisher_name,
        "visibility": ds.visibility,
        "status": ds.status,
        "events_count": rev.events_count if rev else None,
        "cases_count": rev.cases_count if rev else None,
        "date_min": rev.date_min if rev else None,
        "date_max": rev.date_max if rev else None,
        "uses": int(uses or 0),
        "updated_at": ds.updated_at,
        "published_at": ds.published_at,
    }


async def list_datasets(
    session: AsyncSession,
    user: CurrentUser,
    *,
    q: str | None,
    license: str | None,
    sort: str,
    limit: int,
    offset: int,
    namespace: str | None = None,
) -> tuple[list[DatasetSummary], int]:
    if sort not in SORTS:
        raise _422(f"sort must be one of {', '.join(SORTS)}.")
    uses = _uses_subquery().label("uses")
    stmt: Select[Any] = select(HubDataset, uses)
    count_stmt: Select[Any] = select(func.count()).select_from(HubDataset)
    clause = readable_clause(user, await live_team_ids(session, user.id))
    conds: list[Any] = [] if clause is None else [clause]
    for tok in (q or "").lower().split():
        conds.append(HubDataset.search_text.like(f"%{_escape_like(tok)}%", escape="\\"))
    if license:
        conds.append(HubDataset.license_id == license)
    if namespace is not None:
        # The namespace page (/datasets/{namespace}). An unknown or malformed
        # namespace is simply empty - same as one whose datasets the user
        # cannot read, so the page never reveals that private datasets exist.
        conds.append(HubDataset.namespace == namespace)
    for cond in conds:
        stmt = stmt.where(cond)
        count_stmt = count_stmt.where(cond)
    if sort == "uses":
        stmt = stmt.order_by(uses.desc(), HubDataset.updated_at.desc(), HubDataset.id)
    else:
        stmt = stmt.order_by(HubDataset.updated_at.desc(), HubDataset.id)
    total = int((await session.execute(count_stmt)).scalar_one())
    rows = (await session.execute(stmt.limit(limit).offset(offset))).all()
    rev_ids = [ds.current_revision_id for ds, _ in rows if ds.current_revision_id]
    revs: dict[str, HubDatasetRevision] = {}
    if rev_ids:
        revs = {
            r.id: r
            for r in (
                await session.execute(
                    select(HubDatasetRevision).where(HubDatasetRevision.id.in_(rev_ids))
                )
            ).scalars()
        }
    items = [
        DatasetSummary.model_validate(
            _summary(ds, revs.get(ds.current_revision_id or ""), int(n or 0))
        )
        for ds, n in rows
    ]
    return items, total


async def uses_of(session: AsyncSession, ds: HubDataset) -> int:
    return int(
        (
            await session.execute(
                select(_uses_subquery()).select_from(HubDataset).where(HubDataset.id == ds.id)
            )
        ).scalar_one()
        or 0
    )


async def get_detail(
    session: AsyncSession, ds: HubDataset, user: CurrentUser, loader: ModuleLoader | None = None
) -> DatasetDetail:
    del loader  # module installs are per user; the loader is not needed here
    rev = await current_revision(session, ds)
    curator = may_curate_datasets(user.roles)
    mine = (
        await session.execute(
            select(EventLog)
            .where(
                EventLog.user_id == user.id,
                EventLog.dataset_revision_id == rev.id,
                EventLog.deleted_at.is_(None),
            )
            .order_by(EventLog.created_at.desc(), EventLog.id.desc())
        )
    ).scalars()
    required = sorted((rev.canonical_results or {}).keys())
    owned = await user_module_ids(session, user.id)
    team: TeamRef | None = None
    if curator and ds.audience_team_id:
        t = await session.get(Team, ds.audience_team_id)
        if t is not None:
            team = TeamRef(id=t.id, name=t.name)
    data = _summary(ds, rev, await uses_of(session, ds))
    data.update(
        description_md=ds.description_md,
        citation=ds.citation,
        source_url=ds.source_url,
        source_doi=ds.source_doi,
        publisher_url=ds.publisher_url,
        allow_external_ai=bool(ds.allow_external_ai),
        retention_until=ds.retention_until,
        audience_team=team,
        disabled_reason=ds.disabled_reason if curator else None,
        revision=DatasetRevisionOut(
            number=rev.number,
            created_at=rev.created_at,
            source_format=rev.source_format,
            log_model=rev.log_model,
            events_count=rev.events_count,
            cases_count=rev.cases_count,
            variants_count=rev.variants_count,
            activities_count=rev.activities_count,
            date_min=rev.date_min,
            date_max=rev.date_max,
            bytes_parquet=int(rev.bytes_parquet or 0),
            stats=dict(rev.stats or {}),
            preview=dict(rev.preview or {}),
            canonical_modules=required,
            purged=rev.purged_at is not None,
        ),
        required_modules=required,
        missing_modules=[m for m in required if m not in owned],
        viewer=DatasetViewer(
            can_curate=curator,
            my_logs=[DatasetLogRef(id=r.id, name=r.name, created_at=r.created_at) for r in mine],
        ),
    )
    return DatasetDetail.model_validate(data)


# ── event-log decoration (lineage on log payloads) ─────────────────────────


async def published_datasets_for(session: AsyncSession, log_id: str) -> list[PublishedDatasetRef]:
    rows = await session.execute(
        select(HubDataset.namespace, HubDataset.name, HubDataset.title)
        .join(HubDatasetRevision, HubDatasetRevision.dataset_id == HubDataset.id)
        .where(HubDatasetRevision.source_log_id == log_id)
        .order_by(HubDataset.created_at)
    )
    return [PublishedDatasetRef(slug=f"{ns}/{n}", title=t) for ns, n, t in rows]


async def log_detail(session: AsyncSession, row: EventLog, user: CurrentUser) -> EventLogDetail:
    """``EventLogDetail`` with ``source_dataset`` (+ ``published_datasets`` for curators)."""
    detail = EventLogDetail.model_validate(row)
    info = await source_dataset_for(session, row)
    if info is not None:
        detail.source_dataset = SourceDataset.model_validate(info.as_dict())
    if may_curate_datasets(user.roles):
        detail.published_datasets = await published_datasets_for(session, row.id)
    return detail
