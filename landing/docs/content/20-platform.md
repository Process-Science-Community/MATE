<!-- Platform — how MATE works, for contributors and operators. -->

<!-- group: Platform -->

# Architecture and data
<!-- slug: architecture -->

Services, state locations, and the path from an uploaded file to a queryable log.

## Services

| Service | Technology | Role | Port |
| --- | --- | --- | --- |
| `api` | Python 3.12, FastAPI | Routes, module loader, jobs, ingest, MATE AI proxy, MCP server | `8000` |
| `web` | Next.js 15, React 19 | Interface, plus esbuild bundling of module frontends | `3000` |
| `keycloak` | Keycloak 26 | OIDC login, realms, roles | `8080` |
| `app-db` | Postgres 16 | Application metadata — the only database the platform reads | internal |
| `keycloak-db` | Postgres 16 | Keycloak's own store | internal |
| `neo4j` | Neo4j 5 | Sidecar for graph-backed modules, behind the `graph` compose profile | loopback |

A host development API runs on SQLite instead, so `make dev` and the test suite need no database server.

:::stack title="How a request travels" caption="Two client paths into one API. The API owns the stores, and reaches outside the host only for what you configured."
- Browser
  - Next.js server components · cookie-backed session
  - Direct API calls · `NEXT_PUBLIC_API_URL`, CORS, Bearer token
- `web` · renders the interface, bundles module frontends
- `api` · routes, modules, jobs, ingest, MCP
  - `app-db` · users, logs, jobs, settings, shares
  - Parquet · event logs and module results under `data/`
  - DuckDB · in-process SQL over Parquet
- Outside the host, only when configured
  - Keycloak · login and JWKS token validation
  - LLM providers · MATE AI and module AI features
  - S3-compatible bucket · `STORAGE_MODE=s3`
:::

## Repository layout

```tree title="Repository"
mate/
├── apps/api/                # FastAPI backend (src/mate/api)
├── apps/web/                # Next.js frontend (app/, components/, lib/)
├── modules/                 # bundled modules – discovered at startup
├── packages/
│   ├── module-sdk-py/       # Python SDK (mate.sdk)
│   ├── module-sdk-ts/       # frontend SDK for panels and widgets
│   ├── module-sdk-jvm/      # Java SDK
│   └── shared-types/        # generated TypeScript types
├── data/                    # bind-mounted state
├── docs/                    # design spec, deploy runbook, MCP reference
├── infra/                   # Caddyfile, Keycloak realm and scripts
└── landing/                 # public site and this manual
```

`modules/` sits outside `apps/` on purpose: it is the extension point, and a module is authored and shipped on its own. Folder names are arbitrary — the manifest's `id` is authoritative.

## Technology choices

| Concern | Choice | Why |
| --- | --- | --- |
| Metadata | Postgres (SQLite in dev and tests) | Transactions and concurrency where they matter, zero setup where they do not. |
| Event data | Parquet | Columnar, compressed, 5–20× smaller than XES, readable by every Python data tool. |
| Queries | DuckDB | Embedded SQL over Parquet; no service to run, sub-second aggregation. |
| Jobs | asyncio queue + job rows | No broker, no extra container, state that survives a restart. |
| Live updates | Server-Sent Events | The production proxy chain carries HTTP streaming but drops WebSocket upgrades. |
| Types | Pydantic → OpenAPI → `openapi-typescript` | One schema, generated types, no drift. |

## Metadata tables

| Group | Tables |
| --- | --- |
| Identity and access | `users` (a mirror of the Keycloak subject), `api_tokens`, `user_settings`, `system_settings`, `control_policies` |
| Logs | `process_logs`, `process_folders`, `watched_folders`, `watched_folder_files`, `event_edits` |
| Work | `jobs` (status, progress, payload, parent job) |
| Modules | `module_configs`, `module_installs`, `module_layouts` |
| Sharing | `dashboards`, `teams`, `team_members`, `dashboard_shares` |
| Usage (optional) | `analytics_sessions`, `analytics_events`, plus the OCEL-shaped `analytics_objects`, `analytics_object_relations`, `analytics_event_objects` |

Logs, folders, watched folders, and teams use soft deletes: the row stays for audit and cascade bookkeeping while the data is removed.

## On-disk layout

```tree title="data/"
data/
├── users/{user_id}/
│   ├── event_logs/{log_id}/
│   │   ├── meta.json         # source format, ingest stats, detected schema, roles
│   │   ├── events.parquet    # event table, sorted by (case, timestamp)
│   │   ├── cases.parquet     # cached per-case aggregates
│   │   ├── original.*        # the untouched upload, for re-import and export
│   │   └── ocel/             # events, objects, relations, o2o (OCEL only)
│   └── module_results/{log_id}/{module_id}/
├── staging/{user_id}/{token}/     # uploads awaiting confirmation (swept after 2 h)
├── uploaded_modules/{module_id}/  # modules installed by upload
└── uv-python/                     # cached interpreters and wheels
```

Log ids and job ids are UUID v7: time-ordered, so they sort and index well without coordination. Display names never appear in URLs, so renaming never breaks a link.

## The import pipeline

| Stage | Module | Does |
| --- | --- | --- |
| Detect | `ingest/detect.py` | Format family from the extension, refined by content sniffing (including OCEL vs case-centric). |
| Decompress | `ingest/compression.py` | gzip, bzip2, xz, zip. |
| Stage | `ingest/staging.py` | Bytes land under `data/staging/{uid}/{token}/` so probing and confirming never re-upload. |
| Probe | `ingest/probe.py` | A bounded sample proposes column roles. |
| Parse | `ingest/{xes,csv_parser,xml_parser,json_parser,ocel}.py` | Format-specific parsing into one frame; XES streams. |
| Map · coerce · aggregate | `mapping.py`, `parquet_coerce.py`, `aggregation.py` | Apply roles, type the columns, normalise timestamps, compute case aggregates. |
| Store · dispatch | `storage.py`, `dispatch.py` | Write the Parquet set, emit `log.imported`, plan the precompute closure, flip the status. |

Automatic repairs (duplicate columns, text that should be numeric, mixed timestamp formats) are recorded and reported rather than rejected.

## Storage modes

| Mode | Behaviour |
| --- | --- |
| `local` (default) | One copy of everything under `data/`; the directory is the backup. |
| `s3` | An S3-compatible bucket is authoritative and local disk is a bounded cache with an eviction reaper. |

Details, including migration and quota: [Backup, storage and limits](backup-and-storage.html).

# Jobs, modules and isolation

The job queue, the event bus, and the module system, for module authors and operators.

## The job model

:::flow title="Job lifecycle" caption="Every job ends in a terminal state. A failed or cancelled upstream skips its dependents rather than blocking them."
`queued` → `running` → `completed` · dependents released
`running` → `failed` · the error is kept on the row
`running` → `cancelled` · cooperative, then SIGKILL for isolated workers
`queued` → `paused` · the queue was paused; running jobs continue
:::

| Aspect | Behaviour |
| --- | --- |
| Persistence | The job row and its payload survive restarts; a boot-time pass reconciles logs left mid-import. |
| Progress | A fraction or a count pair, persisted every 1000 events, plus a stage label. |
| Timeouts | `JOB_EXECUTION_TIMEOUT_SECONDS` (default 1800) force-stops a job and its offload children. |
| Retry | The stored payload replays the same work. |

The queue is an in-process asyncio queue sized by `WORKER_CONCURRENCY` (default `2`, admin-changeable live). Heavy CPU work moves to a process pool through `ctx.run_in_process(...)`, bounded by `MODULE_PROCESS_POOL_SIZE` and, per user, by `MAX_OFFLOADS_PER_USER`.

## The event bus

In-process pub/sub, with a bounded queue per subscriber: when one fills, the oldest entry is dropped — acceptable for a progress tick, not for a terminal event.

| Topic | Emitted when |
| --- | --- |
| `job.queued` · `job.started` · `job.progress` · `job.completed` · `job.failed` · `job.cancelled` | Job lifecycle |
| `job.plan` · `job.snapshot` · `job.queue.paused` · `job.queue.resumed` | Plan published, client resync, queue control |
| `log.imported` · `ocel.imported` · `log.ready` | Import normalised · precompute settled |
| `<module_id>.completed` | A module's precompute succeeded |
| `module.log.*` · `module.installed` | Module log line · install finished |

`GET /api/v1/events?topic=…` is the platform stream, `GET /api/v1/jobs/{id}/stream` the per-job one; both are SSE with a Bearer header, and the client reconnects with backoff.

## The module system

| Stage | Behaviour |
| --- | --- |
| Discovery | Scans `modules/*/manifest.yaml` one level deep, plus installed packages exposing the `mate.modules` entry point. |
| Validation | Parses manifests and builds the dependency graph; a cycle or a missing hard dependency logs a named error (`modules.discovery.dependency_cycle`, `…requirement_missing`) and drops that module plus everything depending on it — the rest still boots. |
| Materialisation | Creates or reuses `modules/<folder>/.venv` (hashed dependency block) and bundles the frontend into `.dist/`. |
| Mount | Routes under `/api/v1/modules/{id}/*`, event handlers on the bus, job handlers on the queue, capabilities in the registry. |
| Gating | Per log: log model, required columns, `min_events`/`min_cases`, required modules, and the user's enable switch. |
| Hot reload | Dev only: changed files reload in place; a changed dependency block triggers `uv sync` for that module. |

**Precompute** is declared by stacking `@on_event("log.imported")` and `@job` on one handler. The closure — those handlers plus everything chained off `<module_id>.completed` — is frozen onto the log at import time. A module's precompute that succeeds publishes `<module_id>.completed`; if an upstream job fails or is cancelled, its dependents are **skipped**, so a log always reaches `ready` instead of hanging in `processing`. Skipped steps appear in the import's plan; no job row is created for them.

**Install** is a job: an uploaded archive (`POST /api/v1/modules/install`) lands in `data/uploaded_modules/{id}/`, the manifest is validated, dependencies materialised, the frontend bundled, and the module mounted — a failure rolls back. Git-URL and registry installs are specified in the design docs but not implemented; discovery separately picks up installed packages that expose the `mate.modules` entry point. Ownership is reference-counted per user, and shared artifacts are deleted at zero owners.

## Isolation modes

| Mode | Declared by | Runs in | Use it when |
| --- | --- | --- | --- |
| In-process, thread | `isolation: in_process`, `execution: thread` (default) | The API process, thread pool | Almost everything. |
| In-process, killable worker | `execution: worker` | A throwaway child of the API | Long native calls that ignore cooperative cancellation. |
| Subprocess | `isolation: subprocess` | A long-lived worker on the module's own interpreter | A different Python version, or a native-library conflict. |
| Foreign runtime | `runtime: { kind: jvm }` | A worker started from the module's own fat jar | Java methods. |

In-process modules run on the platform's interpreter (currently 3.12) and are ABI-locked to it: `requires-python` is a *validation gate* there, and an interpreter selector for subprocess modules. Each module's `.venv` is private; `inherit` names libraries the platform already ships (pandas, numpy, pm4py, duckdb) — an in-process module resolves them from the platform's interpreter, while a subprocess module installs them into its own environment, because no interpreter is shared across the process boundary.

The **subprocess bridge** is a Unix socket with newline-delimited JSON: a 30-second handshake, `ctx.*` calls proxied back to the host, DataFrames handed over as Parquet, a three-second soft-cancel window followed by `SIGKILL`, and automatic respawn with exponential backoff (capped at 30 s, five consecutive attempts, ladder reset after 60 s of stable uptime). The **JVM runtime** speaks the same protocol, so the SDK surface (`eventLog()`, `cache()`, `bus()`, `progress()`, …) is equivalent.

**Sidecars** are optional compose profiles the operator starts; the module only connects to a configured address, probes health, wipes its scratch space before and after a run, and keeps results in `ctx.cache`.

## MATE AI internals

Prompt assembly per call: base system prompt → navigation note → optionally the process list → optionally activity and variant aggregates (≤40 activities, top 15 variants; skipped for OCEL) → cached module outputs within a character budget → the user's own prompt. Raw rows are never part of any of it: the module context built for an AI call has its event-log accessors replaced by objects that raise on access — the same mechanism the MCP server uses.

Guidance is cached per module and log together with a hash of the payload it was generated from, so it invalidates itself when the underlying result changes.

## Observability

| Endpoint | Returns |
| --- | --- |
| `GET /health` | Liveness for containers and the deploy script. |
| `GET /api/v1/system/storage` | Disk usage by category. |
| `GET /api/v1/system/resources` | Live CPU and memory (admin). |
| `GET /api/v1/system/jobs` · `PUT` | Worker concurrency; resize it live (admin). |
| `GET /api/v1/system/diagnostics` | The copy-paste diagnostics blob. |
| `GET /api/v1/system/mcp-metrics` | Prometheus text for MCP calls, latency, and rate limits. |

Logs are structured, namespaced per module, and mirrored into a bounded in-memory ring buffer that the admin Jobs page tails. Container logs are capped by the compose log driver so a noisy module cannot fill the disk.

# Security and privacy

The invariants that protect a deployment, and the controls on data leaving it.

## Tenant isolation

- Every resource carries the owning user's id, and ownership is checked on every read and write path.
- Filesystem paths are derived from `data/users/{user_id}/`.
- Every user-scoped bus event carries `user_id`; the SSE fan-out filters on it. An event without it reaches every connected client.
- A module receives only its caller's log, an ownership-checked second log, and a result cache scoped to `(log_id, module_id)`.
- The only cross-account read is an explicit, read-only dashboard share.
- A foreign user's resource is indistinguishable from a missing one (`404`).

## Authentication and roles

Login is mandatory and delegated to Keycloak. The web app keeps a JWT-only session (Auth.js v5) with token rotation; the API validates every Bearer token against the realm's JWKS endpoint and inserts a `users` row the first time it sees a subject. If the session cookie outgrows the browser's 4 KB limit, `SESSION_STORE_DIR` moves session state server-side.

| Role | Grants |
| --- | --- |
| default realm roles | Normal use. |
| `admin` | Admin pages and routes, cross-user job control, module policies, the metadata export, the admin MCP toolset. |

Roles arrive in the access token, so a change takes effect at the next refresh rather than the next logout.

## Credentials

| Credential | Lifetime | Scope |
| --- | --- | --- |
| Session JWT | 24 h, rotated | The web app. |
| Personal access token (`mate_pat_…`) | Until revoked | Explicit scopes, never `admin`, only its owner's data. |
| OAuth access token (MCP) | Keycloak lifetimes | Scopes plus, for the admin toolset, the `admin` realm role. |

Tokens are stored as hashes with a display prefix; the secret is shown once. Independently of the credential, each user must opt in to external data access before MCP tools return their data.

## The data wall

Raw event rows, distinct column values, OCEL object rows, and file downloads are structurally unavailable to MATE AI and to every MCP tool. It is enforced by construction — restricted event-log accessors on AI-invoked contexts — not by prompt instructions.

## Usage analytics

Capture is optional and local, stored in the same OCEL-shaped schema the platform uses for process analysis, so the platform's own usage data can be analysed with the same methods. Client events (page views, clicks, web vitals, errors) and server events (every authenticated request, named by route template) are covered by a master switch plus granular toggles for form values, keyboard, and pointer movement. `USER_TRACKING_ONBOARDING` decides whether tracking starts on, off, or forced on with the opt-out hidden. Exports are NDJSON, OCEL 2.0 JSON, or OCEL 2.0 SQLite, and nothing leaves the host.

Routes deliberately avoid ad-blocker trigger words: `/usage`, `/sync`, `/insights` instead of `/analytics`, `/events`, `/track`.

## Resource limits on a shared host

| Setting | Bounds |
| --- | --- |
| `WORKER_CONCURRENCY` | Concurrent jobs (1–8). |
| `MODULE_PROCESS_POOL_SIZE` | Concurrent CPU-offload processes. |
| `MAX_OFFLOADS_PER_USER` | One account's share of that pool — set it below the pool size. |
| `DUCKDB_THREADS` / `DUCKDB_MEMORY_LIMIT` | One query's CPU and memory. |
| Container memory limits | The production overlay caps `api` at 8 GB, `web` at 768 MB, Keycloak at 1 GB, each database at 512 MB, the proxy at 128 MB. |

## Hardening checklist

- [ ] Rotate `AUTH_SECRET` and `KEYCLOAK_CLIENT_SECRET` before any non-local deployment.
- [ ] Replace the seeded realm user's password; grant `admin` deliberately, never by default.
- [ ] Keep `DEMO_MODE` off everywhere except a disposable workshop host.
- [ ] Keep `MCP_ENABLED=false` unless you need it; prefer PATs over broad OAuth grants.
- [ ] Set `MAX_OFFLOADS_PER_USER`, `DUCKDB_THREADS`, and container memory limits on a shared host.
- [ ] Back up both `data/` and the metadata database, and verify a restore once.
