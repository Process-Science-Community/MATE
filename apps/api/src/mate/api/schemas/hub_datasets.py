"""Pydantic schemas for the dataset hub (``/api/v1/hub``)."""

from __future__ import annotations

from datetime import date
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field

from mate.api.schemas.common import UtcDateTime
from mate.api.schemas.event_logs import EventLogDetail

Visibility = Literal["private", "restricted", "public"]
DatasetStatus = Literal["active", "disabled"]


class LicenseOut(BaseModel):
    id: str
    spdx: str | None = None
    name: str
    url: str | None = None
    open: bool


class HubStatus(BaseModel):
    enabled: bool
    can_curate: bool


class DatasetRevisionOut(BaseModel):
    number: int
    created_at: UtcDateTime
    source_format: str | None = None
    log_model: str | None = None
    events_count: int | None = None
    cases_count: int | None = None
    variants_count: int | None = None
    activities_count: int | None = None
    date_min: UtcDateTime | None = None
    date_max: UtcDateTime | None = None
    bytes_parquet: int = 0
    stats: dict[str, Any] = Field(default_factory=dict)
    preview: dict[str, Any] = Field(default_factory=dict)
    canonical_modules: list[str] = Field(default_factory=list)
    purged: bool = False


class DatasetSummary(BaseModel):
    id: str
    namespace: str
    name: str
    slug: str
    title: str
    summary: str
    license: LicenseOut
    tags: list[str]
    publisher_name: str | None = None
    visibility: Visibility
    status: DatasetStatus
    events_count: int | None = None
    cases_count: int | None = None
    date_min: UtcDateTime | None = None
    date_max: UtcDateTime | None = None
    uses: int = 0
    updated_at: UtcDateTime
    published_at: UtcDateTime | None = None


class TeamRef(BaseModel):
    id: str
    name: str


class DatasetLogRef(BaseModel):
    id: str
    name: str
    created_at: UtcDateTime


class DatasetViewer(BaseModel):
    can_curate: bool
    my_logs: list[DatasetLogRef] = Field(default_factory=list)


class DatasetDetail(DatasetSummary):
    description_md: str | None = None
    citation: str | None = None
    source_url: str | None = None
    source_doi: str | None = None
    publisher_url: str | None = None
    allow_external_ai: bool = True
    retention_until: date | None = None
    # Curators only; null for everyone else.
    audience_team: TeamRef | None = None
    disabled_reason: str | None = None
    revision: DatasetRevisionOut
    required_modules: list[str] = Field(default_factory=list)
    missing_modules: list[str] = Field(default_factory=list)
    viewer: DatasetViewer


class DatasetPage(BaseModel):
    items: list[DatasetSummary]
    total: int
    limit: int
    offset: int


class DatasetCardFields(BaseModel):
    model_config = ConfigDict(extra="forbid")

    title: str = Field(min_length=1, max_length=200)
    summary: str = Field(default="", max_length=300)
    description_md: str | None = Field(default=None, max_length=100_000)
    license_id: str = "unknown"
    license_name: str | None = Field(default=None, max_length=200)
    license_url: str | None = Field(default=None, max_length=500)
    citation: str | None = Field(default=None, max_length=5000)
    source_url: str | None = Field(default=None, max_length=500)
    source_doi: str | None = Field(default=None, max_length=200)
    publisher_name: str | None = Field(default=None, max_length=200)
    publisher_url: str | None = Field(default=None, max_length=500)
    tags: list[str] = Field(default_factory=list)
    allow_external_ai: bool = True
    retention_until: date | None = None
    visibility: Visibility = "private"
    audience_team_id: str | None = None


class DatasetCreate(DatasetCardFields):
    source_log_id: str
    namespace: str
    name: str
    # Module results to ship; None = every eligible one.
    modules: list[str] | None = None


class DatasetUpdate(BaseModel):
    """Every card field optional; only the fields sent are applied."""

    model_config = ConfigDict(extra="forbid")

    title: str | None = Field(default=None, min_length=1, max_length=200)
    summary: str | None = Field(default=None, max_length=300)
    description_md: str | None = Field(default=None, max_length=100_000)
    license_id: str | None = None
    license_name: str | None = Field(default=None, max_length=200)
    license_url: str | None = Field(default=None, max_length=500)
    citation: str | None = Field(default=None, max_length=5000)
    source_url: str | None = Field(default=None, max_length=500)
    source_doi: str | None = Field(default=None, max_length=200)
    publisher_name: str | None = Field(default=None, max_length=200)
    publisher_url: str | None = Field(default=None, max_length=500)
    tags: list[str] | None = None
    allow_external_ai: bool | None = None
    retention_until: date | None = None
    visibility: Visibility | None = None
    audience_team_id: str | None = None


class PublishCandidate(BaseModel):
    """A module whose results on the source log would ship with the dataset."""

    module_id: str
    name: str
    # False: the curator's settings differ from the module defaults, so students
    # on default settings recompute instead of reading the shipped result.
    config_is_default: bool


class DatasetUseRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    new_copy: bool = False


class DatasetUseResponse(BaseModel):
    log: EventLogDetail
    created: bool


class DatasetDisableRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    reason: str = Field(min_length=1, max_length=500)


class DatasetPurgeRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    confirm: str
