"""Pydantic schema for `manifest.yaml` (INSTRUCTIONS.md §5.1).

Validated by the SDK so module authors can sanity-check their manifest
locally, and by the platform loader at startup. The loader rejects manifests
with hard-dep cycles, missing required fields, or `inherit:`/`packages:`
overlap (§5.4 inherit-conflict rule).
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path, PurePosixPath
from typing import Any, Literal, Self, cast

import structlog
import yaml
from packaging.specifiers import InvalidSpecifier, SpecifierSet
from packaging.version import InvalidVersion, Version
from pydantic import (
    AliasChoices,
    BaseModel,
    ConfigDict,
    Field,
    ValidationInfo,
    field_validator,
    model_validator,
)

from mate.sdk.artifacts import WORKSPACE_MODULE_ID, DatasetShape, get_artifact_type
from mate.sdk.errors import ModuleManifestError

log = structlog.get_logger(__name__)

# SemVer 2.0.0 (semver.org's reference pattern). `version` is also the run-staleness
# signal and lands in `String(64)` columns, hence the length cap.
SEMVER_RE = re.compile(
    r"(0|[1-9]\d*)\.(0|[1-9]\d*)\.(0|[1-9]\d*)"
    r"(?:-((?:0|[1-9]\d*|\d*[a-zA-Z-][0-9a-zA-Z-]*)(?:\.(?:0|[1-9]\d*|\d*[a-zA-Z-][0-9a-zA-Z-]*))*))?"
    r"(?:\+([0-9a-zA-Z-]+(?:\.[0-9a-zA-Z-]+)*))?"
)
MAX_VERSION_LENGTH = 64

_MODULE_REQUIREMENT_RE = re.compile(r"\s*([a-z0-9_]+)\s*(.*?)\s*")
# What precedes a specifier: the id a lenient parse keeps from an unparseable entry.
_SPECIFIER_START_RE = re.compile(r"[\s<>=!~,]")

# `Manifest.load_yaml(path, lenient=True)` / `model_validate(data, context=LENIENT)`:
# a non-SemVer `version` or an unparseable `requirements.modules` entry is logged and
# accepted, as every manifest was before P8, and a dataset `route` without its leading
# "/" gets one (with a warning). Only for manifests already on disk (boot
# discovery, restoring a previous version) - new uploads and `mate-module validate`
# stay strict.
LENIENT = {"lenient": True}


def _lenient(info: ValidationInfo) -> bool:
    return bool(info.context and info.context.get("lenient"))


@dataclass(frozen=True)
class ModuleRequirement:
    """One `requirements.modules` entry: a module id plus an optional PEP 440
    specifier on its version (`"discovery>=1,<2"`; a bare `"discovery"` takes any)."""

    id: str
    specifier: SpecifierSet

    @classmethod
    def parse(cls, text: str, *, lenient: bool = False) -> ModuleRequirement:
        """Strict by default; *lenient* reads an unparseable entry as the bare id in
        front of its specifier (the whole text when there is none), as before P8."""
        try:
            return cls._parse(text)
        except ModuleManifestError:
            if not lenient:
                raise
            stripped = text.strip()
            return cls(
                _SPECIFIER_START_RE.split(stripped, maxsplit=1)[0] or stripped, SpecifierSet()
            )

    @classmethod
    def _parse(cls, text: str) -> ModuleRequirement:
        match = _MODULE_REQUIREMENT_RE.fullmatch(text)
        if match is None:
            raise ModuleManifestError(
                f"requirements.modules entry {text!r} must be a module id, optionally followed "
                "by a version specifier (e.g. `discovery>=1,<2`)."
            )
        try:
            specifier = SpecifierSet(match.group(2))
        except InvalidSpecifier as exc:
            raise ModuleManifestError(
                f"requirements.modules entry {text!r} has an invalid version specifier: {exc}"
            ) from exc
        return cls(match.group(1), specifier)

    def allows(self, version: str) -> bool:
        """Whether a loaded module at *version* satisfies this requirement."""
        if not self.specifier:
            return True
        try:
            # shortcut: a SemVer prerelease PEP 440 can't spell (`1.0.0-alpha.beta`)
            # never satisfies a specifier; map SemVer to PEP 440 if anyone needs one.
            return self.specifier.contains(Version(version), prereleases=True)
        except InvalidVersion:
            return False

    def __str__(self) -> str:
        return f"{self.id}{self.specifier}"


ModuleCategory = Literal[
    "foundation", "attribute", "external_input", "advanced", "comparison", "other"
]
IsolationMode = Literal["in_process", "subprocess"]
# How an `in_process` module's @job / @on_event handlers execute. `thread` (the
# default) runs them in the API's thread pool - fast, but a sync handler deep in
# a native CPU call can't be cancelled (a thread can't be killed). `worker` runs
# each job in a throwaway killable child process so cancel/shutdown SIGKILL it
# and its whole tree. Ignored for `subprocess` isolation (already a worker).
ExecutionMode = Literal["thread", "worker"]


class EventLogRequirements(BaseModel):
    # Which log model this module operates on. The platform makes a module
    # available only on logs of the matching model - case-centric and
    # object-centric (OCEL) modules never run against each other's logs.
    # Defaults to "case_centric" so every existing module stays case-centric.
    log_model: Literal["case_centric", "object_centric"] = "case_centric"
    required_columns: list[str] = Field(default_factory=list)
    optional_columns: list[str] = Field(default_factory=list)
    min_events: int | None = None
    min_cases: int | None = None


class OptionalModuleDep(BaseModel):
    id: str
    reason: str | None = None


class Requirements(BaseModel):
    event_log: EventLogRequirements = Field(default_factory=EventLogRequirements)
    # Hard deps: module ids, each optionally constrained by a PEP 440 specifier on
    # its version (`discovery>=1,<2`). Read them through `module_requirements` /
    # `module_ids`, never as plain ids.
    modules: list[str] = Field(default_factory=list)
    optional_modules: list[OptionalModuleDep] = Field(default_factory=list)

    @field_validator("modules")
    @classmethod
    def _parse_modules(cls, value: list[str], info: ValidationInfo) -> list[str]:
        for entry in value:
            try:
                ModuleRequirement.parse(entry)
            except ModuleManifestError as exc:
                if not _lenient(info):
                    raise
                treated_as = ModuleRequirement.parse(entry, lenient=True).id
                log.warning(
                    "modules.manifest.requirement_invalid",
                    requirement=entry,
                    treated_as=treated_as,
                    error=str(exc),
                )
        return value

    @property
    def module_requirements(self) -> list[ModuleRequirement]:
        # Lenient only matters for a manifest validated leniently: a strict one has no
        # unparseable entry left.
        return [ModuleRequirement.parse(entry, lenient=True) for entry in self.modules]

    @property
    def module_ids(self) -> list[str]:
        return [r.id for r in self.module_requirements]


class DependenciesPython(BaseModel):
    requires_python: str | None = Field(default=None, alias="requires-python")
    packages: list[str] = Field(default_factory=list)
    inherit: list[str] = Field(default_factory=list)
    isolation: IsolationMode = "in_process"
    execution: ExecutionMode = "thread"

    model_config = ConfigDict(populate_by_name=True)

    @model_validator(mode="after")
    def _no_inherit_conflict(self) -> Self:
        # `pandas` cannot appear in both `packages` and `inherit`.
        pkg_names = {
            p.split(">=", 1)[0].split("==", 1)[0].split("<", 1)[0].split("~", 1)[0].strip().lower()
            for p in self.packages
        }
        for name in self.inherit:
            if name.lower() in pkg_names:
                raise ModuleManifestError(
                    f"`{name}` is in both dependencies.python.inherit and dependencies.python.packages - "
                    "pick one. Inherit reuses the platform's version; packages installs a private copy."
                )
        return self


class Dependencies(BaseModel):
    python: DependenciesPython = Field(default_factory=DependenciesPython)
    npm: list[str] = Field(default_factory=list)


class RuntimePython(BaseModel):
    """The default runtime: the module is Python, executed in-process or in a
    subprocess worker per `dependencies.python.isolation`."""

    kind: Literal["python"] = "python"


class RuntimeJvm(BaseModel):
    """A module implemented on the JVM, shipped as a self-contained fat jar.

    The platform never resolves Java dependencies (no server-side
    Maven/Gradle) - the jar must bundle everything except the JRE itself. The
    worker is always process-isolated and speaks the wire protocol documented
    in `modules/PROTOCOL.md` (the `mate-sdk-jvm` library implements it).
    """

    kind: Literal["jvm"]
    # Folder-relative path to the runnable fat jar (must carry a `Main-Class`).
    jar: str
    # Minimum Java *feature* version the jar needs (17 = oldest supported LTS;
    # the SDK itself targets 17, and Unix-socket support requires 16+).
    requires_java: int = Field(default=17, ge=17, alias="requires-java")
    # Extra JVM flags, e.g. ["-Xmx1g"]. Prepended before `-jar`.
    jvm_args: list[str] = Field(default_factory=list, alias="jvm-args")

    model_config = ConfigDict(populate_by_name=True)


# The platform's dashboard grid, duplicated here rather than imported: the SDK
# must stay dependency-free. Mirrors `GRID` in apps/web/lib/dashboard-queries.ts
# and `GRID_COLS` in apps/api/.../schemas/dashboards.py.
GRID_COLS = 12
_ROW_HEIGHT_PX = 18
_ROW_GAP_PX = 8


def _rows_for_px(px: int) -> int:
    """Absolute pixel height -> rows on the dashboard grid."""
    if px <= 0:
        return 0
    return -(-(px + _ROW_GAP_PX) // (_ROW_HEIGHT_PX + _ROW_GAP_PX))


class WidgetHelp(BaseModel):
    """Plain-language explanation of a widget, shown behind its ⓘ.

    Split into three questions a reader actually asks, rather than one blob:
    `description` stays the one-line palette blurb, this is the real answer.
    Also used for a module's panel via `ManifestFrontend.panel_help`.
    """

    # Required once `help:` is declared at all - a help popover with no "what"
    # is worse than none.
    what: str
    read: str | None = None
    computed: str | None = None
    docs_url: str | None = None


class WidgetKpi(BaseModel):
    """One figure a multi-KPI widget can show.

    Declaring these lets a placed card render a *subset*: the platform offers
    the list in the card's settings and passes the chosen ids as `config.kpis`.
    A widget that shows a single number needs none of this.
    """

    id: str
    title: str
    # Per-KPI ⓘ text, distinct from the widget-level `help`.
    info: str | None = None
    # Whether this KPI is on by default when the card is first placed.
    default: bool = True


class WidgetView(BaseModel):
    """One of the module's views a widget can render.

    This is what lets a dashboard card expose the module's real capabilities
    instead of a fixed slice of them: a widget that can draw several of its
    module's views declares them here, and `exposes` names the `config_schema`
    keys that are meaningful for each one, so the card's settings show the
    knobs that actually apply to the selected view.
    """

    id: str
    title: str
    description: str | None = None
    exposes: list[str] = Field(default_factory=list)


class WidgetDrill(BaseModel):
    """Where clicking into this widget navigates.

    Absent, the platform still offers "open in module" targeting the declaring
    module with no parameters; declare this to point somewhere else (e.g. a
    discovery card drilling into `performance`) or to pin extra query params.
    """

    # Defaults to the declaring module.
    module_id: str | None = None
    # Static params merged into every drill from this widget; a param the
    # widget passes at click time wins.
    params: dict[str, str] = Field(default_factory=dict)
    label: str | None = None
    enabled: bool = True


class WidgetEntry(BaseModel):
    """A reusable frontend widget ("card") a module exposes.

    Beyond the `id`/`entry` the bundler needs, the optional display fields are
    surfaced by the platform's card catalog (`GET /api/v1/modules/cards`) so
    the Dashboards palette can list every module's cards without loading their
    bundles.

    SIZING. `default_w`/`min_w` are columns on the platform's fixed 12-column
    grid; `default_h`/`min_h` are rows. Declare `min_px_w`/`min_px_h` too: a
    grid unit is only a real size once the board's width is known, so the pixel
    floors are what actually keep a card above its usable size. The canvas
    takes whichever floor is larger.
    """

    id: str
    entry: str
    title: str | None = None
    description: str | None = None
    # Lucide icon name (e.g. "Activity"); the frontend maps it to a glyph and
    # falls back to a generic chart icon when unknown or absent.
    icon: str | None = None
    default_w: int = 6
    default_h: int = 8
    # Whether the user may resize the card on a dashboard.
    #   resizable: true  -> card can be resized; `min_w`/`min_h` are the floor and
    #                       `default_w`/`default_h` the initial (>= min) drop size.
    #   resizable: false -> card is a FIXED size: it can be moved but not resized,
    #                       and `default_w`/`default_h` ARE that fixed size.
    # Either way the relevant size (the minimum, or the fixed size) must be large
    # enough to show all of the widget's information.
    resizable: bool = True
    # Smallest size, in grid units, a *resizable* card may be shrunk to. The
    # Dashboards canvas feeds these to the grid item's `minW`/`minH` and also
    # grows an under-sized placed card up to them on load. Ignored when
    # `resizable` is false (the card is locked to `default_w`/`default_h`).
    min_w: int = 2
    min_h: int = 3
    # Absolute pixel floors, independent of the grid - the ones that make a
    # minimum mean something. Grid units alone can't: a column is a fraction of
    # the board's width, so the same `min_w` is a different real size on a wide
    # screen than a narrow one. Measure the widget's genuine minimum in the
    # browser and declare it here; the canvas resolves `min_px_w` against the
    # measured column width and `min_px_h` against the row height, then takes
    # whichever floor (grid or pixel) is larger. 0 = not declared.
    min_px_w: int = 0
    min_px_h: int = 0
    # Plain-language help behind the card's ⓘ (and the palette). See WidgetHelp.
    help: WidgetHelp | None = None
    # The module views this widget can render, and which config keys apply to
    # each. Empty = a single implicit view. See WidgetView.
    views: list[WidgetView] = Field(default_factory=list)
    # The individual figures a multi-KPI widget shows, so a placed card can pick
    # a subset. Empty = the widget is not KPI-structured. See WidgetKpi.
    kpis: list[WidgetKpi] = Field(default_factory=list)
    # Where clicking into this widget navigates. See WidgetDrill.
    drill: WidgetDrill | None = None
    # Folder-relative path to a settings component for this widget, bundled
    # alongside it as `widget-<id>-settings.js`. For widgets whose controls
    # can't be expressed as JSON Schema - a canvas with layout modes, live
    # thresholds and rendering toggles. The card's settings panel mounts this
    # instead of generating a form. Prefer `config_schema` when it suffices.
    settings_entry: str | None = None
    # Optional per-card settings, declared in the same JSON-Schema-flavoured
    # dialect as a module's top-level `config_schema` (`{properties: {...}}`
    # with `type`/`title`/`enum`/`minimum`/`ui.widget` ...). The Dashboards
    # palette surfaces it (`/modules/cards`) and renders a settings form per
    # placed card in edit mode; the chosen values land in the placement's
    # `config` and are passed to the widget as its `config` prop.
    config_schema: dict[str, Any] | None = None
    # Which log data model(s) this card applies to. A dashboard is created for
    # one model (case-centric vs object-centric/OCEL) and its palette only
    # offers cards whose `log_models` include the board's model. Defaults to
    # case-centric so every existing widget keeps working unchanged.
    log_models: list[Literal["case_centric", "object_centric"]] = Field(
        default_factory=lambda: ["case_centric"]
    )

    @model_validator(mode="after")
    def _clamp_defaults_to_min(self) -> Self:
        # For a resizable card the initial drop size must never be below its own
        # minimum, or the canvas would immediately bounce it up. For a fixed
        # card (`resizable=false`) `default_w/_h` is the authoritative size, so
        # leave it untouched.
        if self.resizable:
            self.default_w = max(self.default_w, self.min_w)
            # The pixel floor counts here too: a widget that declares only
            # `min_px_h` would otherwise drop at a height its own floor
            # immediately overrides.
            self.default_h = max(self.default_h, self.min_h, _rows_for_px(self.min_px_h))
        # A card can never be wider than the grid.
        self.default_w = min(self.default_w, GRID_COLS)
        self.min_w = min(self.min_w, GRID_COLS)
        return self


class ManifestFrontend(BaseModel):
    """A module's frontend surfaces: its full page, and its dashboard cards.

    `page_layout` and `side_rail` used to live here. Both are gone - nothing
    ever rendered them. Manifests still declaring them keep loading, because
    unknown keys are ignored.
    """

    panel: str | None = None
    widgets: list[WidgetEntry] = Field(default_factory=list)
    # Plain-language help for the module's *panel*, shown behind the same ⓘ the
    # dashboard cards use. Same shape as a widget's `help`.
    panel_help: WidgetHelp | None = None
    # Whether the platform renders its log-scoped filter bar (column filters +
    # time range) above this module's panel. Set false when narrowing the log
    # doesn't apply to what the panel shows - e.g. it reads a user-triggered
    # result, or it picks its own logs to compare - so the bar can't offer a
    # filter the panel won't honour.
    log_filter: bool = True


class DatasetEntry(BaseModel):
    """A named, typed *data* output a module exposes for the platform's generic
    visualization layer.

    Unlike a ``WidgetEntry`` (which ships a module-authored React component), a
    dataset is data only: it points at an existing module ``@route`` whose JSON
    response the platform normalizes into a shape-tagged ``DatasetEnvelope`` and
    renders with one of the platform's *generic* visualizations (bar, line,
    table, process-map, ...). Surfaced by the dataset catalog
    (``GET /api/v1/datasets/catalog``) so the Dashboards palette can offer it
    without loading any bundle.
    """

    id: str
    title: str | None = None
    description: str | None = None
    # Lucide icon name (e.g. "Network"); the frontend maps it to a glyph and
    # falls back to a generic icon when unknown or absent.
    icon: str | None = None
    # The data shape this dataset produces. Drives which generic visualizations
    # can render it - a viz declares which shape(s) it `accepts`. Optional when
    # `artifact_type` is set: the artifact type's default shape fills it in.
    shape: DatasetShape
    # Module sub-route (leading slash, e.g. "/dfg") whose JSON response carries
    # the data. The platform calls ``GET /api/v1/modules/{module_id}{route}``,
    # so the existing per-request ephemeral filter + result-cache variant apply
    # unchanged. Optional when `cache_key` is set.
    route: str | None = None
    # What this dataset is, as an artifact type id from `mate.sdk.artifacts`
    # (`petri_net` or `petri_net@1`). Other modules' `inputs:` match on it.
    artifact_type: str | None = None
    # The result-cache key the module's precompute writes this artifact under.
    # Inputs bound to this dataset read that entry; they never call `route`.
    cache_key: str | None = None
    # Which log data model(s) this dataset applies to. A dashboard is created
    # for one model (case-centric vs object-centric/OCEL) and its palette only
    # offers datasets whose `log_models` include the board's model. Defaults to
    # case-centric so the common case needs no declaration.
    log_models: list[Literal["case_centric", "object_centric"]] = Field(
        default_factory=lambda: ["case_centric"]
    )
    # Optional JSON-Schema-flavoured params (same dialect as `config_schema`)
    # the dataset's route accepts as query params. Passed through to the
    # frontend as-is; rendering/validation is the frontend's job.
    params_schema: dict[str, Any] | None = None

    @model_validator(mode="before")
    @classmethod
    def _shape_from_artifact_type(cls, data: Any) -> Any:
        if not isinstance(data, dict):
            return data
        fields = cast(dict[str, Any], data)
        type_id = fields.get("artifact_type")
        artifact_type = get_artifact_type(type_id) if isinstance(type_id, str) else None
        if fields.get("shape") is None and artifact_type is not None:
            return {**fields, "shape": artifact_type.shape}
        return data

    @model_validator(mode="after")
    def _route_or_cache_key(self, info: ValidationInfo) -> Self:
        if self.route is None and self.cache_key is None:
            raise ModuleManifestError(
                f"Dataset {self.id!r} needs a `route`, a `cache_key` or both."
            )
        # Appended to `/api/v1/modules/{id}` as-is, so "" or "x" would build a bad URL.
        if self.route is not None and not self.route.startswith("/"):
            if _lenient(info):  # a module installed before the rule: fix it up, keep it
                log.warning(
                    "modules.manifest.dataset_route_slash", dataset=self.id, route=self.route
                )
                self.route = f"/{self.route}"
                return self
            raise ModuleManifestError(
                f"Dataset {self.id!r}: `route` must start with '/' (got {self.route!r})."
            )
        return self


class InputPort(BaseModel):
    """One artifact a module consumes (manifest `inputs:`).

    A handler reads it with `await ctx.inputs.get(<id>)`: the platform binds the
    port to the `from` module's dataset of this artifact type and returns that
    module's precomputed cache entry, or None while it is unbound or not yet
    produced. The `from` module runs first when the user owns it.
    """

    model_config = ConfigDict(populate_by_name=True)

    id: str
    artifact_type: str
    required: bool = False
    # Producer module id (YAML `from:`). Without it the port stays unbound.
    from_: str | None = Field(default=None, alias="from")
    # Producer dataset id. Default: the producer's only dataset of this type.
    dataset: str | None = None
    description: str | None = None


class AiModelSlot(BaseModel):
    """One labelled (provider, model) selector exposed on the module's
    settings page. The actual API keys come from the platform's global
    Settings → AI; the module only persists the user's chosen pair."""

    title: str
    description: str | None = None


class AiModelsManifest(BaseModel):
    """Declares the AI-model selectors a module needs on its settings page.

    Typical usage is ``llm`` (for chat agents) + ``embedding`` (for retrieval),
    but any string-keyed slot is accepted so a module could declare extra
    roles (e.g. a separate vision model).
    """

    model_config = ConfigDict(extra="allow")

    # When true, the module manages its **own** OpenAI API key (persisted under
    # ``module_configs.config_json["ai"]``) and never reads the platform's
    # global Settings → AI. The settings page renders the module's isolated
    # OpenAI card (key + Check + model pickers) instead of the platform-keyed
    # provider/model selectors.
    self_hosted: bool = False

    llm: AiModelSlot | None = None
    embedding: AiModelSlot | None = None


class ModelStoreManifest(BaseModel):
    """Declares that a module accepts large pretrained-model uploads.

    When present, the module's settings page renders a generic "Model files"
    card: users upload an archive (e.g. ``.tar.zst``) that the module extracts
    into platform-shared storage, then pick which uploaded model this account
    uses. The chosen folder name is persisted under ``config_json[config_key]``.

    The actual upload / list / delete is served by the module's own routes
    (``GET``/``POST``/``DELETE`` ``/models``); this block only opts the card in
    and supplies its copy.
    """

    title: str = "Model files"
    description: str | None = None
    # Accepted upload extension(s), passed to the file picker's `accept` attr
    # (e.g. ".tar.zst"). Cosmetic - server-side validation is the route's job.
    accept: str = ".tar.zst"
    # Where the selected model's folder name is stored in the module config.
    config_key: str = "model"


# A manifest may cite up to this many sources and list up to this many links.
MAX_SOURCES = 20
MAX_LINKS = 20

# Author credits were removed from the manifest: a source's `fullCitation`
# carries the author names, so these keys are rejected instead of silently
# dropped by `extra="ignore"` (see `_reject_removed_credit_fields`).
_REMOVED_CREDIT_FIELDS = ("author", "author_url", "authors", "paper_url", "papers")


class Source(BaseModel):
    """One work a module implements/cites - a paper, a book, a report.

    `title` is the short label the UI links. `full_citation` (YAML
    `fullCitation`) is the full reference in **IEEE style with the DOI omitted**
    and no final period - the DOI belongs in `url`, so repeating it would print
    the same link twice in one row (house style, see `modules/README.md` §3).
    `url` is optional; omit it and the title renders as plain text with the
    citation underneath.
    """

    model_config = ConfigDict(populate_by_name=True)

    title: str
    full_citation: str = Field(alias="fullCitation")
    url: str | None = None


class Link(BaseModel):
    """One named link a module points at - code, data, or a model.

    A plain named link (`name` is what the UI shows, `url` is where it goes),
    used for the reference implementation's repo, a dataset, a demo, or a
    released model. Unlike a `Source` it carries no citation.
    """

    name: str
    url: str


class Manifest(BaseModel):
    """The top-level manifest object - `manifest.yaml`."""

    model_config = ConfigDict(extra="ignore", populate_by_name=True)

    id: str
    name: str
    version: str
    category: ModuleCategory
    description: str | None = None
    # Optional longer "what you can do with this module" text, shown in the
    # platform's "About this module" info box on the module detail page.
    # 2-4 sentences, user-facing, plain language. Falls back to `description`
    # when omitted.
    about: str | None = None
    # The works this module implements/cites (max 20). Each entry is
    # `{title, fullCitation, url?}` - the citation string carries the author
    # names, which is why the manifest has no author fields at all (declaring
    # one is an error, see `_reject_removed_credit_fields`).
    source: list[Source] = Field(default_factory=list, max_length=MAX_SOURCES)
    # Optional named links (max 20) - the reference implementation's repo, a
    # dataset, a demo, a released model. Rendered as their own row in the "About
    # this module" box, below the cited sources. A link is `{name, url}`, both
    # required, and an empty list simply renders nothing. The old YAML key
    # `artifacts:` is still read (deprecated): "artifact" now means what a
    # module produces or consumes, not a link.
    links: list[Link] = Field(
        default_factory=list,
        max_length=MAX_LINKS,
        validation_alias=AliasChoices("links", "artifacts"),
    )
    license: str | None = None

    # Which language runtime executes this module. Absent = Python (every
    # pre-existing manifest keeps working unchanged). Non-Python runtimes are
    # always process-isolated: the validator below normalises
    # `dependencies.python.isolation` to "subprocess" so the loader's existing
    # isolation checks route them through the worker bridge.
    runtime: RuntimePython | RuntimeJvm = Field(default_factory=RuntimePython, discriminator="kind")
    requirements: Requirements = Field(default_factory=Requirements)
    provides: list[str] = Field(default_factory=list)
    consumes: list[str] = Field(default_factory=list)
    # Free-form hints (verbs, synonyms, domain terms) that help MATE AI's intent
    # classifier route a user's chat message to this module. Optional - when
    # omitted the platform derives keywords from the name/description/provides.
    keywords: list[str] = Field(default_factory=list)
    dependencies: Dependencies = Field(default_factory=Dependencies)
    frontend: ManifestFrontend = Field(default_factory=ManifestFrontend)
    # Named, typed *data* outputs (vs `frontend.widgets`, which are rendered
    # React components). Each points at an existing module route; the platform
    # normalizes the response into a shape-tagged envelope and renders it with a
    # generic visualization. Surfaced by `GET /api/v1/datasets/catalog`.
    datasets: list[DatasetEntry] = Field(default_factory=list)
    # Artifacts this module consumes from other modules' datasets, read with
    # `ctx.inputs.get(<port id>)`. See InputPort and `mate.sdk.artifacts`.
    inputs: list[InputPort] = Field(default_factory=list)
    permissions: list[str] = Field(default_factory=list)
    default_enabled: bool = True
    # When the module's import-time precompute (`@on_event("log.imported")` +
    # `@job`) runs. `lazy` (the default) computes it the first time a log needs
    # the module - the module page, a dashboard card or an MCP read - so a fresh
    # import costs nothing; `eager` also runs it in the background right after
    # every import. Keep `eager` for the one or two modules whose output is the
    # first thing people open. An admin can override this per module, so treat it
    # as the author's recommendation, not a guarantee. Irrelevant for a module
    # with no precompute handler.
    precompute: Literal["eager", "lazy"] = "lazy"
    # Whether the module is safe to run against confidential data - i.e. it
    # processes the event log entirely locally and never ships data to an
    # external service. When the user enables "Show only confidential modules"
    # in platform settings, modules with this set to `false` are hidden.
    # Defaults to `false` so a module is only treated as safe when it
    # explicitly opts in.
    is_confidential_safe: bool = Field(default=False, alias="isConfidentialSafe")
    # JSON-Schema-flavoured dict so module authors write it in YAML. The
    # platform passes it through to the frontend as-is (`/config-schema`);
    # form-rendering and validation are the frontend's responsibility. Two
    # per-property flags under `ui` are read by the platform itself:
    # `scope: dataset` (default `module`) lets a user override the value per event
    # log, the module-wide value being every log's default; `affects: precompute`
    # (next to `compute`/`view`) marks a value the precompute job reads, so
    # changing its effective value makes the log's stored run stale.
    config_schema: dict[str, Any] | None = None
    # Optional declaration of AI-model selectors. When present, the module's
    # settings page renders an "AI models" card and the chosen (provider,
    # model) pairs are persisted under ``module_configs.config_json["ai"]``.
    ai_models: AiModelsManifest | None = None
    # Optional declaration that the module accepts large pretrained-model
    # uploads. When present, the settings page renders a "Model files" card and
    # the selected model's folder name is persisted under
    # ``module_configs.config_json[model_store.config_key]``.
    model_store: ModelStoreManifest | None = None

    @model_validator(mode="before")
    @classmethod
    def _reject_removed_credit_fields(cls, data: Any) -> Any:
        """Fail loud on the removed author/paper credit fields.

        `model_config` uses `extra="ignore"`, so a manifest still declaring
        `author:` or `papers:` would silently lose its credits instead of
        showing them. Reject it with the migration hint: everything is a
        `source:` entry now, and the author names live in its `fullCitation`.
        """
        if not isinstance(data, dict):
            return data
        found = [key for key in _REMOVED_CREDIT_FIELDS if key in data]
        if found:
            raise ModuleManifestError(
                f"Manifest field(s) {', '.join(repr(f) for f in found)} are no longer supported. "
                "Cite the work under `source:` instead - a list of "
                "`{title, fullCitation, url?}` entries whose `fullCitation` carries the "
                "author names."
            )
        return data

    @field_validator("version")
    @classmethod
    def _semver(cls, value: str, info: ValidationInfo) -> str:
        if len(value) > MAX_VERSION_LENGTH or SEMVER_RE.fullmatch(value) is None:
            if _lenient(info):
                log.warning(
                    "modules.manifest.version_not_semver",
                    module_id=info.data.get("id"),
                    version=value,
                )
                return value
            raise ModuleManifestError(
                f"Manifest version {value!r} must be a SemVer 2.0 version (MAJOR.MINOR.PATCH, "
                f"e.g. 1.0.0 or 1.1.0-rc.1) of at most {MAX_VERSION_LENGTH} characters."
            )
        return value

    @model_validator(mode="after")
    def _validate_id(self) -> Self:
        if not self.id.replace("_", "").isalnum() or not self.id.islower():
            raise ModuleManifestError(
                f"Manifest id {self.id!r} must be lowercase snake_case (letters, digits, underscores)."
            )
        if self.id == WORKSPACE_MODULE_ID:
            raise ModuleManifestError(
                f"Manifest id {self.id!r} is reserved for uploaded workspace artifacts."
            )
        return self

    @model_validator(mode="after")
    def _validate_runtime(self) -> Self:
        """Cross-validate the runtime block against the Python deps block, then
        normalise isolation so non-Python runtimes always bridge-mount.

        The `dependencies.python` knobs describe a Python toolchain; on a
        foreign runtime they are at best dead weight and at worst a sign the
        author misunderstood the contract - reject loudly instead of ignoring.
        """
        if self.runtime.kind == "python":
            return self
        py = self.dependencies.python
        if py.packages or py.inherit or py.requires_python:
            raise ModuleManifestError(
                f"runtime: {self.runtime.kind} modules must not declare dependencies.python "
                "(packages/inherit/requires-python) - the runtime block owns the toolchain."
            )
        if "isolation" in py.model_fields_set and py.isolation == "in_process":
            raise ModuleManifestError(
                f"runtime: {self.runtime.kind} modules cannot run in_process - they always "
                "execute in their own worker process. Drop the isolation key."
            )
        # Normalise: every foreign-runtime module is bridge-mounted, so the
        # loader's existing `isolation == "subprocess"` checks all apply.
        py.isolation = "subprocess"

        if self.runtime.kind == "jvm":
            jar = PurePosixPath(self.runtime.jar)
            if not self.runtime.jar.strip() or jar.is_absolute() or ".." in jar.parts:
                raise ModuleManifestError(
                    "runtime.jar must be a folder-relative path inside the module "
                    f"(got {self.runtime.jar!r})."
                )
        return self

    @classmethod
    def load_yaml(cls, path: Path | str, *, lenient: bool = False) -> Manifest:
        """Parse and validate *path*. *lenient* (see `LENIENT`) is for manifests
        already on disk; anything new is validated strictly."""
        path = Path(path)
        try:
            data = yaml.safe_load(path.read_text())
        except yaml.YAMLError as exc:
            raise ModuleManifestError(f"Invalid YAML in {path}: {exc}") from exc
        if not isinstance(data, dict):
            raise ModuleManifestError(f"Manifest at {path} is not a YAML mapping.")
        try:
            return cls.model_validate(data, context=LENIENT if lenient else None)
        except Exception as exc:
            raise ModuleManifestError(f"Manifest validation failed for {path}: {exc}") from exc

    def dependencies_hash(self) -> str:
        """Stable hash of the dependencies block - used to skip `uv sync` on
        unchanged boots (§5.4).

        The runtime block is folded in ONLY for non-Python runtimes: for the
        (default) Python runtime the payload must stay byte-identical to what
        it was before `runtime:` existed, or every deployed module venv would
        rebuild on upgrade.
        """
        import hashlib
        import json

        payload_obj: Any = self.dependencies.model_dump(by_alias=True)
        if self.runtime.kind != "python":
            payload_obj = {
                "dependencies": payload_obj,
                "runtime": self.runtime.model_dump(by_alias=True),
            }
        payload = json.dumps(payload_obj, sort_keys=True)
        return hashlib.blake2b(payload.encode("utf-8"), digest_size=16).hexdigest()
