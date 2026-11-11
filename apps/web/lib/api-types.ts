/**
 * Manually-mirrored API types – the canonical source is FastAPI's
 * `/openapi.json`. Run `pnpm codegen` against a running backend to refresh.
 *
 * This file holds the minimum the frontend needs at the moment so we don't
 * block on having the backend up during build.
 */

export type EventLogStatus = "importing" | "processing" | "ready" | "failed";

/** Case-centric (XES/CSV/XML) vs object-centric (OCEL). The two are fully
 * isolated – drives the detail-page tabs, header counts, and which endpoints /
 * modules apply. */
export type LogModel = "case_centric" | "object_centric";

export interface EventLogSummary {
  id: string;
  name: string;
  status: EventLogStatus | string;
  source_format: string | null;
  source_filename: string | null;
  log_model: LogModel;
  events_count: number | null;
  /** Case-centric counts – null for object-centric logs. */
  cases_count: number | null;
  variants_count: number | null;
  /** Object-centric counts – null for case-centric logs. */
  objects_count: number | null;
  object_types_count: number | null;
  relations_count: number | null;
  date_min: string | null;
  date_max: string | null;
  error: string | null;
  folder_id: string | null;
  position: number;
  created_at: string;
  imported_at: string | null;
  last_edited_at: string | null;
  /** The importer had to guess a mandatory column – prompts a settings review. */
  mapping_needs_review?: boolean;
}

// ── Import wizard: staged upload + column probe (POST /event-logs/stage) ─────

/** How a canonical role was matched to a source column – the wizard's
 * confidence chip. `user` = the user picked it, `exact` = header matched a
 * canonical name/alias outright, `fuzzy` = substring match, `fallback` = guessed
 * from the data itself. */
export type ColumnRoleQuality = "user" | "exact" | "fuzzy" | "fallback";

export interface ProbeColumn {
  name: string;
  /** Fraction of sampled rows carrying a non-empty value (0–1). */
  coverage: number;
  samples: string[];
}

/** Result of staging an upload: the bytes are on the server (referenced by
 * `staging_token`), described well enough to confirm the mapping before the
 * import job is queued. */
export interface LogProbeResponse {
  kind: "log";
  staging_token: string;
  /** The sniffed format – a `.json` that turned out to be OCEL reads "ocel". */
  source_format: string;
  log_model: LogModel;
  /** False for object-centric logs, whose schema the OCEL parser owns. */
  needs_mapping: boolean;
  columns: ProbeColumn[];
  /** role → source column, from the same resolver the import job runs. */
  roles: Record<string, string>;
  quality: Record<string, ColumnRoleQuality>;
  events_sampled: number;
  size_bytes: number;
  filename: string | null;
  delimiter: string | null;
  event_element: string | null;
  event_path: string | null;
}

/** One file inside an uploaded archive. */
export interface ArchiveMember {
  /** Name inside the archive – what `POST …/extract` takes back. */
  path: string;
  name: string;
  /** Unpacked size as the archive declares it (enforced on extraction). */
  size_bytes: number;
  /** Zip only – a tar is compressed as a whole. */
  compressed_bytes: number | null;
  /** Coarse format from the file name; `null` when it isn't an event log. */
  format: string | null;
  importable: boolean;
  /** Why it can't be imported, for the greyed-out row. */
  reason: string | null;
  /** Refused by a zip-bomb guard rather than for being the wrong kind of file. */
  unsafe: boolean;
}

/** Staging an archive that holds several event logs: nothing is unpacked yet.
 * Each member the user picks is staged on its own via
 * `POST /event-logs/stage/{staging_token}/extract`. */
export interface ArchiveProbeResponse {
  kind: "archive";
  staging_token: string;
  filename: string;
  size_bytes: number;
  /** "zip" | "tar" | "tar.gz" | "tar.bz2" | "tar.xz" */
  archive_format: string;
  members: ArchiveMember[];
  /** What the picked members may unpack to, all of them together. */
  limit_bytes: number;
}

export type StageResponse = LogProbeResponse | ArchiveProbeResponse;

// ── Object-centric (OCEL) data shapes (GET /event-logs/{id}/ocel/*) ──────────

export interface OcelObjectTypeEntry {
  type: string;
  count: number;
}

export interface OcelOverview {
  events_count: number;
  objects_count: number;
  object_types_count: number;
  relations_count: number;
  date_min: string | null;
  date_max: string | null;
  object_types: OcelObjectTypeEntry[];
  activities: string[];
}

export interface OcelPage {
  rows: Record<string, unknown>[];
  columns: string[];
  total: number;
  offset: number;
  limit: number;
}

export type OcelObjectsPage = OcelPage;
export type OcelEventsPage = OcelPage;
export type OcelRelationsPage = OcelPage;

export interface FolderSummary {
  id: string;
  name: string;
  parent_id: string | null;
  position: number;
  created_at: string;
}

export interface ReorderItem {
  kind: "folder" | "log";
  id: string;
  parent_id: string | null;
  position: number;
}

export interface EventLogDetail extends EventLogSummary {
  detected_schema: Record<string, unknown> | null;
  description: string | null;
  column_overrides: EventLogColumnOverrides | null;
  /** The committed Events-tab filter applied to every module's view of the
   * log. `null` means the full dataset. */
  active_filter: FilterEntry[] | null;
  /** Resolved role → source-column mapping (case_id / activity / timestamp / …). */
  column_roles: Record<string, string> | null;
}

/** Manual column-role mapping submitted from settings → re-imports the log. */
export interface RemapColumnRoles {
  case_id: string;
  activity: string;
  timestamp: string;
  end_timestamp?: string | null;
  resource?: string | null;
  cost?: string | null;
  role?: string | null;
  lifecycle?: string | null;
}

export interface EventLogColumnOverrides {
  labels?: Record<string, string>;
  order?: string[];
  hidden?: string[];
  /** Display-only renames for activity values, applied at render time. The
   * underlying parquet keeps the raw activity names so analytics modules
   * continue to operate on canonical values. Managed from the Activities tab.
   */
  activity_labels?: Record<string, string>;
}

export interface EventLogCreateResponse {
  log_id: string;
  job_id: string;
}

export type JobStatus =
  | "queued"
  | "running"
  | "paused"
  | "completed"
  | "failed"
  | "cancelled";

export interface JobDetail {
  id: string;
  type: string;
  title: string;
  subtitle: string | null;
  module_id: string | null;
  payload_json: Record<string, unknown>;
  status: JobStatus | string;
  progress_current: number;
  progress_total: number | null;
  stage: string | null;
  message: string | null;
  error: string | null;
  rate: number | null;
  eta_seconds: number | null;
  priority: number;
  parent_job_id: string | null;
  created_at: string;
  started_at: string | null;
  finished_at: string | null;
}

/** One cited work (manifest `source[]`). `fullCitation` carries the authors. */
export interface ManifestSource {
  title: string;
  fullCitation: string;
  url: string | null;
}

/** One named link (manifest `links[]`) — repo, dataset, demo, model. */
export interface ManifestLink {
  name: string;
  url: string;
}

/** One artifact a module consumes (manifest `inputs[]`). */
export interface ModuleInputPort {
  id: string;
  /** Artifact type id, e.g. `petri_net` or `petri_net@1`. */
  artifact_type: string;
  required: boolean;
  /** Producer module id; null leaves the port unbound. */
  from: string | null;
  /** Producer dataset id; null = the producer's only dataset of this type. */
  dataset: string | null;
  description: string | null;
}

export interface ModuleSummary {
  id: string;
  name: string;
  version: string;
  category:
    | "foundation"
    | "attribute"
    | "external_input"
    | "advanced"
    | "comparison"
    | "other"
    | string;
  description: string | null;
  /** Longer "with this module you can …" text for the About info box. */
  about: string | null;
  /** Cited works (max 20) — title + full citation string + optional DOI link.
   *  There are no author fields: `fullCitation` carries the author names. */
  source: ManifestSource[];
  /** Optional named links (max 20) — code repo, dataset, demo, released model. */
  links: ManifestLink[];
  license: string | null;
  provides: string[];
  consumes: string[];
  has_frontend: boolean;
  /** The module ships a `HOW_TO_USE.md` (compulsory for new modules); the topbar
   *  offers it as the "How to use" panel. False only for a module installed
   *  before the rule. `GET /modules/{id}/guide` serves the markdown. */
  has_guide: boolean;
  /** Whether the module page renders the log-scoped filter bar above the panel
   *  (manifest `frontend.log_filter`); false for panel-less modules. */
  supports_log_filter: boolean;
  enabled: boolean;
  is_confidential_safe: boolean;
  availability: { status: "available" | "unavailable" | "degraded"; reasons: string[] } | null;
  /** Modules that must have computed for a log before this one's panel can show
   *  data — itself when it precomputes, plus what it needs first, in dependency
   *  order. Empty = no gate: the module computes inline or on an explicit action.
   *  The module page / dashboard cards gate on exactly these. */
  precompute_deps: string[];
  /** Artifacts the module consumes from other modules (manifest `inputs[]`). */
  inputs: ModuleInputPort[];
}

/** One installed version of a module. `content_hash`: the uploaded archive's pristine
 *  content hash; null for a bundled module. */
export interface ModuleVersion {
  version: string;
  content_hash: string | null;
}

/** One successful install of an uploaded module (upload or rollback). */
export interface ModuleVersionEntry extends ModuleVersion {
  installed_at: string;
  user_id: string | null;
}

/** `GET /modules/{id}/versions` (owners only). `history` is newest first and empty
 *  for a bundled module. `previous_version` is kept for an admin rollback:
 *  `POST /admin/modules/{id}/rollback` reinstalls it (staged; 409 when there is none,
 *  400 for a bundled module, 500 with the reason when it fails to load – the current
 *  version stays) and answers the admin module row. `changelog`: the module's
 *  `CHANGELOG.md` (first 64 KiB), markdown. */
export interface ModuleVersions {
  current: ModuleVersion;
  history: ModuleVersionEntry[];
  previous_available: boolean;
  previous_version: string | null;
  changelog: string | null;
}

/** Where one module stands for one log (`GET /event-logs/{id}/module-runs`).
 *  `idle`: never computed. `stale`: computed, but the data, the module's code or a
 *  `ui.affects: "precompute"` setting has since changed. `queued` with no `job_id`:
 *  waiting on a module it depends on. */
export type ModuleRunStatus =
  | "idle"
  | "queued"
  | "running"
  | "ready"
  | "stale"
  | "failed"
  | "cancelled"
  | "unavailable";

export interface ModuleRunState {
  module_id: string;
  status: ModuleRunStatus;
  job_id: string | null;
  /** Import-time mode for a module that precomputes on import; null otherwise. */
  precompute: "eager" | "lazy" | null;
  error: string | null;
  finished_at: string | null;
}

/** A module's parameters for one log (`GET`/`PUT /event-logs/{id}/modules/{module}/config`).
 *  Covers only the module's `ui.scope: "dataset"` properties. A key missing from
 *  `overrides` inherits the module-wide value; `PUT {overrides}` replaces the whole
 *  object (a null value also inherits) and 422s on a key that is not per-log. */
export interface LogModuleConfig {
  /** `config_schema` dialect, holding only the per-log properties. */
  schema: { type: "object"; properties: Record<string, Record<string, unknown>> };
  /** This log's stored overrides. */
  overrides: Record<string, unknown>;
  /** The module-wide value of each property (schema default when unset). */
  module_values: Record<string, unknown>;
  /** What the module uses for this log; an admin lock wins over an override. */
  effective: Record<string, unknown>;
  /** Properties an admin-locked card owns: read-only, a PUT keeps their stored override. */
  locked: string[];
}

/** A run variant's request (`POST /event-logs/{id}/module-runs/{module}/variants`).
 *  Its canonical JSON hashes to the variant key, so the same spec is the same variant. */
export interface RunSpec {
  /** Values for the module's `ui.scope: "dataset"` properties only (422 otherwise). */
  params?: Record<string, unknown>;
  /** `{port_id: ArtifactRef}`; an unbound port keeps its default producer. */
  inputs?: Record<string, string>;
  /** Replaces the log's committed filter (`{field, op, value?}` entries); `[]` = raw log. */
  filter?: Record<string, unknown>[] | null;
}

export type ModuleVariantStatus =
  | "queued"
  | "running"
  | "ready"
  | "stale"
  | "failed"
  | "cancelled"
  | "unavailable";

/** One run variant (`GET …/module-runs/{module}/variants[/{variant}]`). The POST
 *  answers 202 while it is in flight, 200 once settled; `force: true` in its body
 *  retries a failed or cancelled one. `DELETE …/variants/{variant}` cancels + removes. */
export interface ModuleVariantState {
  /** 16 hex chars. */
  variant: string;
  status: ModuleVariantStatus;
  spec: Required<RunSpec>;
  job_id: string | null;
  error: string | null;
  finished_at: string | null;
  /** The module's typed, cached outputs in this variant -
   *  `ref` = `{log_id}/{module_id}@{variant}/{cache_key}`. */
  refs: { dataset_id: string; artifact_type: string; ref: string }[];
}

/** `module_variant.updated` bus event. Always carries `user_id`. */
export interface ModuleVariantUpdatedPayload {
  log_id: string;
  module_id: string;
  variant: string;
  status: ModuleVariantStatus | "deleted";
  job_id: string | null;
}

// ── Artifact workspace (`/api/v1/artifacts`) ────────────────────────────────

/** One artifact in the user's workspace (`GET /artifacts?log_id=&artifact_type=&pinned=`,
 *  newest first). `run`: an output of a module the user has installed, indexed when its
 *  default run or run variant settled ready, or found by a list (the file stays in the
 *  module's cache). `upload`: a JSON artifact the user uploaded. A default result is
 *  listed while its cache entry exists (hidden during a rerun, keeping its name and pin);
 *  a variant's or upload whose file is gone is dropped by the server on read.
 *  `GET /artifacts/{id}/download`: the stored file, whole – `.json`, or a table's
 *  `.parquet` – with a `Content-Disposition` filename from `name`. */
export interface Artifact {
  id: string;
  /** `{log_id}/{module_id}[@{variant}]/{key}`, or `{log_id ?? "_"}/_workspace/{id}` for an
   *  upload – what a run variant's `inputs` binds and `GET /artifacts/content?ref=` reads. */
  ref: string;
  /** Artifact type id (`petri_net`) – match the viz registry's `acceptsArtifactTypes` on it. */
  artifact_type: string;
  type_version: number;
  source: "run" | "upload";
  log_id: string | null;
  /** The producer; null for an upload. */
  module_id: string | null;
  module_name: string | null;
  dataset_id: string | null;
  /** The run variant (16 hex) that produced it; null for a default result or an upload. */
  variant: string | null;
  key: string;
  name: string;
  pinned: boolean;
  producer_version: string | null;
  /** The producer's loaded version now – differs from `producer_version` when the
   *  result predates an upgrade or rollback (re-run it). Null for an upload. */
  current_version: string | null;
  producer_hash: string | null;
  /** Lineage: the input refs the producing run had bound. */
  inputs: string[];
  /** A run variant's spec. */
  spec: Required<RunSpec> | null;
  size_bytes: number | null;
  created_at: string;
  /** When the content was last produced (a rename/pin leaves it alone). */
  updated_at: string;
  /** `ready`: the file is there. A pinned variant's is listed `running` while its rerun
   *  is under way and `failed` once it failed – no content: re-run it (POST its `spec`
   *  with `force: true`) or delete it. `missing` only answers a PATCH of a hidden row
   *  (PATCH and DELETE work on hidden rows too). */
  state: ArtifactState;
}

export type ArtifactState = "ready" | "missing" | "failed" | "running";

/** An installed module whose input port takes this artifact type ("Use in…"): POST
 *  `/event-logs/{log_id}/module-runs/{module_id}/variants` with
 *  `{inputs: {[port_id]: artifact.ref}}` (a `_/_workspace/…` upload binds on any log). */
export interface ArtifactConsumer {
  module_id: string;
  module_name: string;
  port_id: string;
  required: boolean;
}

/** One of `Artifact.inputs`, resolved against the workspace (`id` null when it isn't in it). */
export interface ArtifactLineageEntry {
  ref: string;
  id: string | null;
  name: string | null;
  artifact_type: string | null;
}

/** `GET /artifacts/{id}`. 404 for someone else's or a vanished artifact.
 *  `DELETE /artifacts/{id}`: 204 for an upload or a variant's artifact (deletes the whole
 *  variant); 409 for a default-run result ("managed by the module"). A default result is
 *  also readable by a shared dashboard's viewer via `GET /artifacts/content?ref=`. */
export interface ArtifactDetail extends Artifact {
  consumers: ArtifactConsumer[];
  lineage: ArtifactLineageEntry[];
}

/** `PATCH /artifacts/{id}` (optimistic). Pinned variant artifacts survive the variant GC. */
export interface ArtifactPatch {
  name?: string;
  pinned?: boolean;
}

/** `POST /artifacts` as JSON (201 → `Artifact`), or multipart with the payload as `file`
 *  and these fields as form fields (with a `Content-Length`, else 411). Only JSON-stored
 *  types (`petri_net`, `bpmn`, `dfg`, `process_tree`, `conformance_result`,
 *  `drift_points`); 422 lists schema problems, 413 over `ARTIFACT_UPLOAD_MAX_BYTES`
 *  (20 MiB). The name is cut to 255 characters. PNML/BPMN files: Model Interop instead. */
export interface ArtifactUpload {
  artifact_type: string;
  name?: string;
  log_id?: string | null;
  payload: unknown;
}

/** `GET /artifacts/{id}/content` / `GET /artifacts/content?ref=` for a Parquet table:
 *  the first 200 rows. A JSON artifact comes back as stored (415 for binary, 413 too big). */
export interface ArtifactTablePreview {
  columns: string[];
  rows: Record<string, unknown>[];
  total_rows: number;
  /** `total_rows > rows.length` – the whole table is `GET /artifacts/{id}/download`. */
  truncated: boolean;
}

/** `module_run.updated` bus event — a run changed state. Always carries `user_id`. */
export interface ModuleRunUpdatedPayload {
  log_id: string;
  module_id: string;
  status: ModuleRunStatus;
  job_id: string | null;
}

export interface BusEnvelope<T = Record<string, unknown>> {
  topic: string;
  payload: T;
  ts: number;
}

// ── Flows (`/api/v1/flows`) ──────────────────────────────────────────────────
// A flow chains module runs: source -> module -> output nodes, edges from an output
// handle to an input port of the same artifact type. Private to its owner; the log is
// bound per run. Artifact types here are base ids (`petri_net`, no `@1`).

export type FlowLogModel = "case_centric" | "object_centric";

/** `{kind: "log"}`: the run's log (at most one per flow; every module node reads it
 *  implicitly - no edge needed). Output handle `"log"`, artifact type
 *  `FlowPalette.log_artifact_type`. `{kind: "artifact", ref}`: an artifact of your
 *  workspace (`Artifact.ref`; `""` until picked). Output handle = the ref itself. */
export type FlowSourceData = { kind: "log" } | { kind: "artifact"; ref: string };

/** A run variant of the module's precompute. `params` = values for its
 *  `ui.scope: "dataset"` properties only (`FlowPaletteModule.params`). Output handles:
 *  `FlowPaletteModule.outputs[].dataset_id`; input handles: `inputs[].id`. */
export interface FlowModuleData {
  module_id: string;
  params?: Record<string, unknown>;
}

/** Names (non-empty `name`) and pins (`pin`) the connected module output in the
 *  workspace once a run produced it. Input handle `"in"`. */
export interface FlowOutputData {
  name: string;
  pin: boolean;
}

interface FlowNodeBase {
  /** 1–64 chars, unique in the flow. */
  id: string;
  position: { x: number; y: number };
}

export type FlowNode =
  | (FlowNodeBase & { type: "source"; data: FlowSourceData })
  | (FlowNodeBase & { type: "module"; data: FlowModuleData })
  | (FlowNodeBase & { type: "output"; data: FlowOutputData });

export interface FlowEdge {
  id: string;
  source: string;
  /** `"log"` (log source), the ref (artifact source) or a dataset id (module node). */
  sourceHandle: string;
  target: string;
  /** An input port id (module node) or `"in"` (output node). One edge per input. */
  targetHandle: string;
}

/** Max 200 nodes / 500 edges. Extra node/edge keys (React Flow's `selected`,
 *  `measured`, …) are dropped on save. */
export interface FlowGraph {
  nodes: FlowNode[];
  edges: FlowEdge[];
}

/** One thing wrong with a flow, on the node or edge to mark (both null: the flow or
 *  the log as a whole). */
export interface FlowProblem {
  node_id: string | null;
  edge_id: string | null;
  message: string;
}

/** Body of every flow 422 (`ApiError.detail.detail`): POST/PUT refuse only what the
 *  graph alone gets wrong (duplicate ids, a dangling edge, an input connected twice, a
 *  second log source, a cycle) - an unfinished graph autosaves fine. Everything else
 *  comes from `POST /flows/{id}/validate` and refuses `POST /flows/{id}/runs`. */
export interface FlowInvalidDetail {
  message: string;
  errors: FlowProblem[];
}

export type FlowRunStatus = "queued" | "running" | "ready" | "failed" | "cancelled";

export interface FlowRunSummary {
  id: string;
  log_id: string;
  status: FlowRunStatus;
  created_at: string;
  finished_at: string | null;
}

/** `GET /flows` (newest first). */
export interface FlowSummary {
  id: string;
  name: string;
  description: string | null;
  log_model: FlowLogModel;
  created_at: string;
  updated_at: string;
  /** The newest run, whatever its log. */
  last_run: FlowRunSummary | null;
}

/** `GET /flows/{id}`, and the answer to `POST /flows` (201) / `PUT /flows/{id}`. */
export interface FlowDetail extends FlowSummary {
  graph: FlowGraph;
  /** `{module_id: "version+compute_hash"}` of the module nodes at the last save. */
  pins: Record<string, string>;
}

/** `POST /flows`. `log_model` is fixed at creation. */
export interface FlowCreate {
  name: string;
  description?: string | null;
  log_model?: FlowLogModel;
  graph?: FlowGraph;
}

/** `PUT /flows/{id}`: replaces name, description and graph. */
export interface FlowUpdate {
  name: string;
  description?: string | null;
  graph: FlowGraph;
}

/** `POST /flows/{id}/validate` with `{log_id?}` (also checks the log: yours - else 404 -,
 *  ready, of the flow's model). Always 200. */
export interface FlowValidation {
  ok: boolean;
  errors: FlowProblem[];
}

/** `skipped`: an upstream node failed, or the run was cancelled before it ran. */
export type FlowNodeRunStatus = "queued" | "running" | "ready" | "failed" | "skipped" | "cancelled";

export interface FlowNodeRunState {
  status: FlowNodeRunStatus;
  /** A module node's run variant key; null when the node is the module's default run
   *  (no params, no input but what its ports read unconnected). */
  variant: string | null;
  /** A module node's job: its variant job (a child of the run's `job_id`), or its
   *  default run's job (not a child). */
  job_id: string | null;
  error: string | null;
  /** What the node produced: a module node its typed outputs, an artifact source its
   *  ref, an output node the artifact it saved. Open one in the workspace by `ref`. */
  refs: { dataset_id: string | null; artifact_type: string; ref: string }[];
  /** A module node whose fresh result was reused - nothing ran. */
  reused: boolean;
  /** An output node: the workspace `Artifact.id` it named / pinned. */
  artifact_id: string | null;
}

/** `POST /flows/{id}/runs {log_id}` (202; 404 for a log that is not yours, 422 +
 *  `FlowInvalidDetail` when it cannot run), `GET /flows/{id}/runs` (newest first,
 *  `?limit=` ≤ 100), `GET /flows/{id}/runs/{run_id}`, `POST …/runs/{run_id}/cancel`
 *  (returns the run; it turns `cancelled` once the job has stopped - poll or listen). */
export interface FlowRun extends FlowRunSummary {
  flow_id: string;
  /** The parent `flow.run` job in the jobs drawer (cancellable there too). */
  job_id: string | null;
  error: string | null;
  /** By node id; empty while queued. */
  nodes: Record<string, FlowNodeRunState>;
}

/** `flow_run.updated` bus event (SSE `?topic=flow_run.updated`). Always carries
 *  `user_id`. `node_id` null: the run's own status changed. */
export interface FlowRunUpdatedPayload {
  flow_id: string;
  run_id: string;
  node_id: string | null;
  status: FlowRunStatus | FlowNodeRunStatus;
}

export interface FlowPalettePort {
  id: string;
  artifact_type: string;
  required: boolean;
  /** The producer it reads when unconnected. */
  from: string | null;
  /** Unconnected, it reads `from`'s default result (installed + resolvable) - a
   *  required port like that needs no edge. */
  default_bound: boolean;
  description: string | null;
}

/** One installed module usable as a node (it has a precompute and fits the log model). */
export interface FlowPaletteModule {
  module_id: string;
  name: string;
  description: string;
  category: string;
  inputs: FlowPalettePort[];
  /** Typed, cached datasets: the output handles. */
  outputs: { dataset_id: string; artifact_type: string; title: string }[];
  /** SchemaForm schema of the `ui.scope: "dataset"` params. */
  params: { type: "object"; properties: Record<string, Record<string, unknown>> };
}

/** "Insert converter": `module_id`'s `input` port takes `from_type`, its `output`
 *  dataset is `to_type`. */
export interface FlowConverter {
  from_type: string;
  to_type: string;
  module_id: string;
  input: string;
  output: string;
}

/** `GET /flows/palette?log_model=`. */
export interface FlowPalette {
  /** The log source's type: `event_log` or `ocel`. */
  log_artifact_type: string;
  modules: FlowPaletteModule[];
  converters: FlowConverter[];
}

// ── Events / Variants / Data quality / Edits ────────────────────────────────

export type ColumnType = "string" | "number" | "datetime" | "duration" | "enum" | "boolean";

export type ColumnRole =
  | "case_id"
  | "activity"
  | "timestamp"
  | "end_timestamp"
  | "resource"
  | "cost"
  | "role"
  | "lifecycle"
  | "custom";

export interface ColumnSpec {
  name: string;
  label: string;
  role: ColumnRole;
  type: ColumnType;
  nullable: boolean;
  required: boolean;
  enum_values: string[] | null;
}

export interface EventsHeader {
  events_count: number;
  cases_count: number;
  variants_count: number;
  date_min: string | null;
  date_max: string | null;
}

export type EventRow = Record<string, unknown> & { _has_missing?: boolean };

export interface EventsPage {
  rows: EventRow[];
  total: number;
  offset: number;
  limit: number;
  columns: ColumnSpec[];
  header: EventsHeader;
}

export type FilterOp =
  | "contains"
  | "equals"
  | "gte"
  | "lte"
  | "is_null"
  | "is_not_null"
  | "in";

export interface FilterEntry {
  field: string;
  op: FilterOp;
  /** A string array when `op` is "in" (the multi-select checklist); a scalar
   * for the single-value operators; omitted for is_null / is_not_null. */
  value?: string | number | boolean | string[] | null;
}

export interface ColumnValueEntry {
  value: string;
  count: number;
}

export interface ColumnValuesPage {
  field: string;
  values: ColumnValueEntry[];
  total_distinct: number;
  truncated: boolean;
}

export interface ActiveFilterResult {
  active_filter: FilterEntry[];
  modules_retriggered: boolean;
}

export interface CellPatch {
  field: string;
  value: unknown;
}

export interface CellPatchResult {
  row: EventRow;
  row_index: number;
  new_row_index: number;
  header: EventsHeader;
}

export interface BulkFillBody {
  row_indices: number[];
  field: string;
  value: unknown;
}

export interface BulkFillResult {
  updated: number;
  header: EventsHeader;
}

export interface VariantRow {
  rank: number;
  variant_id: string;
  activities: string[];
  case_count: number;
  case_pct: number;
  avg_duration_seconds: number | null;
  median_duration_seconds: number | null;
  first_seen: string | null;
  last_seen: string | null;
}

export interface VariantsPage {
  rows: VariantRow[];
  total: number;
  offset: number;
  limit: number;
}

export interface AttributeBreakdownEntry {
  value: unknown;
  count: number;
}

export interface AttributeBreakdown {
  column: string;
  label: string;
  top: AttributeBreakdownEntry[];
}

export interface VariantDetail {
  rank: number;
  variant_id: string;
  activities: string[];
  case_count: number;
  case_pct: number;
  avg_duration_seconds: number | null;
  median_duration_seconds: number | null;
  p90_duration_seconds: number | null;
  first_seen: string | null;
  last_seen: string | null;
  duration_histogram: number[];
  duration_bin_edges_seconds: number[];
  attribute_breakdowns: AttributeBreakdown[];
}

export interface VariantCase {
  case_id: string;
  case_start: string | null;
  case_end: string | null;
  case_duration_seconds: number | null;
  event_count: number;
}

export interface VariantCasesPage {
  rows: VariantCase[];
  total: number;
  offset: number;
  limit: number;
}

export interface ColumnQuality {
  column: string;
  label: string;
  type: ColumnType;
  role: ColumnRole;
  null_count: number;
  null_pct: number;
  distinct_count: number;
}

export interface DataQuality {
  total_events: number;
  columns: ColumnQuality[];
}

export interface ActivityRow {
  activity: string;
  count: number;
}

export interface ActivitiesPage {
  rows: ActivityRow[];
  total: number;
}

export interface ActivityDetail {
  activity: string;
  event_count: number;
  event_pct: number;
  case_count: number;
  case_pct: number;
  avg_occurrences_per_case: number | null;
  max_occurrences_per_case: number | null;
  start_case_count: number;
  start_case_pct: number;
  end_case_count: number;
  end_case_pct: number;
  first_seen: string | null;
  last_seen: string | null;
  variant_count: number;
  top_variants: VariantRow[];
}

export interface ActivityCase extends VariantCase {
  occurrences: number;
}

export interface ActivityCasesPage {
  rows: ActivityCase[];
  total: number;
  offset: number;
  limit: number;
}

export interface EventEditEntry {
  id: number;
  log_id: string;
  row_index: number;
  field: string;
  old_value_json: unknown;
  new_value_json: unknown;
  edited_at: string;
}

export interface EventEditsPage {
  rows: EventEditEntry[];
  total: number;
  offset: number;
  limit: number;
}

export interface EventLogUpdatePayload {
  name?: string;
  description?: string | null;
  column_overrides?: EventLogColumnOverrides | null;
  folder_id?: string | null;
  position?: number;
}

// ── Watched folders ─────────────────────────────────────────────────────────

export type WatchMode = "manual" | "interval" | "continuous";
export type WatchStatus = "active" | "paused" | "error";

/** Optional per-format column mapping forced on every imported file. */
export interface WatchDefaultMapping {
  csv_mapping?: Record<string, unknown>;
  xml_mapping?: Record<string, unknown>;
  json_mapping?: Record<string, unknown>;
}

export interface WatchedFolderSummary {
  id: string;
  name: string;
  source_path: string;
  mode: WatchMode | string;
  interval_seconds: number | null;
  status: WatchStatus | string;
  dest_folder_id: string | null;
  last_scanned_at: string | null;
  last_error: string | null;
  created_at: string;
  imported_count: number;
  failed_count: number;
}

export interface WatchedFileSummary {
  source_name: string;
  status: string;
  size: number | null;
  log_id: string | null;
  error: string | null;
  imported_at: string;
}

export interface WatchedFolderDetail extends WatchedFolderSummary {
  default_mapping: WatchDefaultMapping | null;
  files: WatchedFileSummary[];
}

export interface WatchedFolderCreatePayload {
  name: string;
  source_path?: string | null;
  mode: WatchMode;
  interval_seconds?: number | null;
  default_mapping?: WatchDefaultMapping | null;
  create_dest_folder?: boolean;
  dest_folder_id?: string | null;
}

export interface WatchedFolderUpdatePayload {
  name?: string;
  mode?: WatchMode;
  interval_seconds?: number | null;
  status?: "active" | "paused";
  default_mapping?: WatchDefaultMapping | null;
}

export interface ScanResponse {
  found: number;
  imported: number;
  skipped: number;
  failed: number;
}

// ── Admin behaviour-event export (GET /admin/export/*) ───────────────────────

/** Backend ``EventSource`` literal on AnalyticsEvent rows. */
export type EventSource = "client" | "server";

/** XES trace (case) notion for the event-log export. */
export type ExportCaseNotion = "session" | "user";

/** Output format for the behaviour export. The ``ocel-*`` variants download
 * the object-centric UI log as OCEL 2.0 (GET /admin/export/events-ocel2). */
export type ExportFormat = "xes" | "ndjson" | "csv" | "ocel-json" | "ocel-sqlite" | "ocel-xml";

/** Optional filters shared by every /admin/export/* route. Empty/undefined
 * fields add no predicate (the export then spans everything). Mirrors the
 * backend ``_event_filters`` params. */
export interface ExportFilters {
  user_id?: string | null;
  source?: EventSource | null;
  event_type?: string | null;
  event_name?: string | null;
  path_prefix?: string | null;
  /** ISO-8601; half-open [start, end) window on occurred_at. */
  start?: string | null;
  end?: string | null;
  session_id?: string | null;
}

export interface ExportTypeCount {
  label: string;
  count: number;
}

/** GET /admin/export/preview – counts + span for the current filter set. */
export interface ExportPreview {
  matched_events: number;
  matched_sessions: number;
  distinct_users: number;
  date_min: string | null;
  date_max: string | null;
  event_types: ExportTypeCount[];
}

export interface ExportUserOption {
  id: string;
  email: string | null;
  preferred_username: string | null;
}

/** GET /admin/export/facets – dropdown options for the filter UI. */
export interface ExportFacets {
  users: ExportUserOption[];
  event_types: string[];
  event_names: ExportTypeCount[];
  paths: ExportTypeCount[];
}

// ── AI config (masked) – GET/PUT /ai/config ────────────────────────────────
// The GET response never carries an api_key (mirrors backend AiConfigOut). The
// key flows to the provider call server-side only; the form shows `*_key_set`
// and a "leave blank to keep" placeholder.

export type AiProviderId = "anthropic" | "openai" | "unigpt" | "custom";

export interface AiConfigOut {
  system_prompt: string;
  anthropic_base_url: string | null;
  openai_base_url: string | null;
  unigpt_base_url: string | null;
  custom_base_url: string | null;
  anthropic_key_set: boolean;
  openai_key_set: boolean;
  unigpt_key_set: boolean;
  custom_key_set: boolean;
  selected_provider: AiProviderId | null;
  selected_model: string | null;
  classifier_model: string | null;
  allow_process_data: boolean;
  /** When true, an admin has locked AI settings for all users (read-only). */
  controlled_by_admin: boolean;
}

// ── Admin control framework – /admin/controls ──────────────────────────────

export type ControlScope = "setting" | "card";
export type ControlMode = "user" | "admin";

/** GET /admin/controls/items – one controllable server setting, or one settings
 *  card of a module (config / ai / model, keyed "<module_id>:<card_id>"). */
export interface ControlItem {
  scope: ControlScope;
  key: string;
  label: string;
  description: string | null;
  control_mode: ControlMode | string;
  /** Whether the admin value has been set (never the secret itself). */
  admin_value_set: boolean;
  /** Echoed admin value for non-secret items (cards, analytics, concurrency). */
  admin_value: unknown | null;
  /** True when any secret (ai.config key) is stored in the admin value. */
  secret_set: boolean;
  /** Config card's JSON-schema so the editor can render inputs. */
  config_schema: Record<string, unknown> | null;
  // ── card scope only ──
  /** The module this card belongs to. */
  module_id?: string | null;
  /** Which card: "config" | "ai" | "model". */
  card_id?: string | null;
  /** The card's human title. */
  title?: string | null;
  /** model_store manifest (model card) so the editor knows accept/config_key. */
  model_store?: { title?: string; description?: string | null; accept?: string; config_key?: string } | null;
  /** ai_models manifest (ai card) so the editor renders the right selectors. */
  ai_models?: Record<string, unknown> | null;
}

export interface ControlItems {
  items: ControlItem[];
}

/** PUT /admin/controls/items/{scope}/{key}. */
export interface ControlUpdate {
  control_mode: ControlMode;
  admin_value?: unknown | null;
}

// ── Admin insights metric groups – /admin/insights/{users,storage,jobs,usage} ─

export interface InsightsDayCount {
  day: string;
  count: number;
}
export interface InsightsLabelCount {
  label: string;
  count: number;
}
export interface InsightsTopUser {
  user_id: string;
  email: string | null;
  username: string | null;
  count: number;
}

export interface LastSeenBucket {
  bucket: string;
  count: number;
}

/** GET /admin/insights/users */
export interface UsersInsights {
  days: number;
  user_count: number;
  active_users_in_range: number;
  onboarding_completed: number;
  onboarding_completion_pct: number;
  active_users_by_day: InsightsDayCount[];
  sessions_by_day: InsightsDayCount[];
  last_seen_buckets: LastSeenBucket[];
  top_users_by_events: InsightsTopUser[];
}

export interface StorageUserRow {
  user_id: string;
  email: string | null;
  username: string | null;
  log_count: number;
  events_total: number;
  disk_bytes: number | null;
}

export interface StorageLogRow {
  id: string;
  name: string;
  owner_id: string;
  events_count: number | null;
  cases_count: number | null;
}

/** GET /admin/insights/storage */
export interface StorageInsights {
  backend_mode: string;
  s3_used_bytes: number | null;
  s3_object_count: number | null;
  s3_quota_bytes: number | null;
  s3_error: string | null;
  total_logs: number;
  total_events: number;
  per_user: StorageUserRow[];
  largest_logs: StorageLogRow[];
  disk_included: boolean;
}

export interface JobsRuntimeStats {
  concurrency: number;
  live_workers: number;
  queue_depth: number;
  running: number;
  paused_users: number;
}

/** GET /admin/insights/jobs */
export interface JobsInsights {
  days: number;
  runtime: JobsRuntimeStats;
  by_status: InsightsLabelCount[];
  by_type: InsightsLabelCount[];
  failures_by_day: InsightsDayCount[];
  completions_by_day: InsightsDayCount[];
  avg_duration_by_day: InsightsDayCount[];
  avg_duration_seconds: number;
  slowest_seconds: number;
}

export interface ModuleUsageRow {
  module_id: string;
  installs: number;
  runs: number;
  avg_duration_seconds: number;
}

export interface AiUsage {
  chat_requests: number;
  guidance_requests: number;
  /** AI token counts / cost are not tracked anywhere – always false today. */
  tokens_tracked: boolean;
}

/** GET /admin/insights/usage */
export interface UsageInsights {
  days: number;
  installs_by_module: InsightsLabelCount[];
  modules: ModuleUsageRow[];
  most_used_module: string | null;
  ai: AiUsage;
}

// --- Admin → Modules (cross-user ownership dashboard + controls) -----------
// Mirrors apps/api/.../routes/admin_modules.py.

export interface AdminModuleOwner {
  user_id: string;
  email: string | null;
  username: string | null;
  /** "default" | "upload" | "admin" – best-effort provenance. */
  source: string | null;
  installed_at: string;
}

/** GET /admin/modules – one row per module known to the platform. */
export interface AdminModuleRow {
  id: string;
  name: string;
  version: string;
  category: string;
  has_frontend: boolean;
  /** Ships in the repo modules/ folder; always default, cannot be un-defaulted. */
  is_bundled: boolean;
  /** In the effective default set (bundled or admin-declared) – every user gets it. */
  is_default: boolean;
  default_locked: boolean;
  /** Withheld from new seeding: existing owners keep it, but users who don't have
   * it yet are no longer auto-seeded it. The only way to stop a bundled default
   * reaching future users. */
  withheld_from_new_users: boolean;
  owner_count: number;
  /** Earliest source="upload" owner – best-effort "who uploaded this". */
  uploaded_by: AdminModuleOwner | null;
  owners: AdminModuleOwner[];
  /** Null when the module has no import-time precompute – nothing to switch. */
  precompute: AdminModulePrecompute | null;
}

/** Whether a module also computes in the background right after every import. */
export interface AdminModulePrecompute {
  /** What the module's manifest recommends. */
  manifest: "eager" | "lazy";
  /** What applies now (the admin's override, else the manifest's). */
  effective: "eager" | "lazy";
  overridden: boolean;
}

// --- GET /system/resources (Admin → System, live CPU/RAM monitor) ----------

export interface PerCoreStat {
  index: number;
  current_pct: number;
  max_pct: number;
}

export interface CpuStat {
  current_pct: number;
  max_pct: number;
  cores_logical: number;
  cores_physical: number;
  per_core: PerCoreStat[];
}

export interface MemoryStat {
  used_bytes: number;
  total_bytes: number;
  max_used_bytes: number;
  current_pct: number;
}

export interface ResourceSample {
  ts: number;
  cpu_pct: number;
  mem_used_bytes: number;
}

export type ResourceBreakdownSource =
  | "module_subprocess"
  | "module_inproc"
  | "api_baseline"
  | "system"
  | "idle";

export interface ResourceBreakdownSlice {
  label: string;
  module_id: string | null;
  source: ResourceBreakdownSource;
  /** CPU breakdown: percent of the host (0-100). Memory breakdown: bytes. */
  value: number;
  estimated: boolean;
}

export interface RunningJobInfo {
  id: string;
  module_id: string | null;
  user_id: string;
  type: string;
  title: string;
}

/** GET /system/resources – live host CPU/RAM + per-source breakdown (admin only). */
export interface SystemResources {
  cpu: CpuStat;
  memory: MemoryStat;
  history: ResourceSample[];
  cpu_breakdown: ResourceBreakdownSlice[];
  memory_breakdown: ResourceBreakdownSlice[];
  running_jobs: RunningJobInfo[];
  sample_interval_seconds: number;
  history_window_seconds: number;
}

/** GET /api/v1/api-tokens item – a per-user personal access token (no secret). */
export interface ApiTokenInfo {
  id: string;
  name: string;
  token_prefix: string;
  scopes: string[];
  created_at: string;
  last_used_at: string | null;
  expires_at: string | null;
  revoked: boolean;
}

/** POST /api/v1/api-tokens – adds the cleartext `token`, shown exactly once. */
export interface CreateTokenResponse extends ApiTokenInfo {
  token: string;
}

export interface McpScopeInfo {
  id: string;
  description: string;
}

export interface McpOAuthInfo {
  authorization_server: string;
  client_id: string | null;
  metadata_url: string;
}

/** GET /api/v1/api-tokens/mcp-info – MCP availability, endpoint, OAuth + scopes. */
export interface McpInfo {
  enabled: boolean;
  url: string;
  require_consent: boolean;
  consented: boolean;
  mint_allowed: boolean;
  /** Live write lock – write tools are refused (or not even registered when boot-forced). */
  read_only: boolean;
  /** Toolsets registered at boot (env MCP_TOOLSETS). */
  toolsets: string[];
  scopes_supported: McpScopeInfo[];
  oauth: McpOAuthInfo;
}

/** GET/PUT /api/v1/api-tokens/consent – per-user external-egress consent. */
export interface McpConsentState {
  required: boolean;
  consented: boolean;
}

/** GET/PUT /api/v1/system/mcp – admin live config. */
export interface McpAdminConfig {
  /** env MCP_ENABLED (mount happens at boot) */
  boot_enabled: boolean;
  /** live effective availability */
  enabled: boolean;
  mint_policy: string;
  /** env MCP_READ_ONLY (write tools not even registered) */
  boot_read_only: boolean;
  /** live effective write lock */
  read_only: boolean;
  /** registered at boot (env MCP_TOOLSETS) */
  toolsets: string[];
}

/** PUT /api/v1/system/mcp body – all fields optional, only the given ones change. */
export interface McpAdminUpdate {
  enabled?: boolean;
  mint_policy?: string;
  read_only?: boolean;
}

/** GET /api/v1/admin/api-tokens – org-wide token (admin). */
export interface AdminApiTokenInfo {
  id: string;
  user_id: string;
  user_email: string | null;
  name: string;
  token_prefix: string;
  created_at: string;
  last_used_at: string | null;
  revoked: boolean;
}
