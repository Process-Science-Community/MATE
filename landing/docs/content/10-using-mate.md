<!-- Using MATE — the app, screen by screen, without the tour. -->

<!-- group: Using MATE -->

# Importing event logs

The wizard accepts five format families, probes them server-side, and writes nothing until you confirm the interpretation.

## Formats

| Format | Extensions | Mapping step |
| --- | --- | --- |
| XES | `.xes`, `.xes.gz` | none — the file carries its own schema |
| CSV | `.csv` | required: case id, activity, timestamp |
| XML | `.xml` | required: the event element, then column roles |
| JSON event log | `.json` | required: the event path, then column roles |
| OCEL 2.0 | `.jsonocel`, `.xmlocel`, `.sqlite` | none — object-centric structure is in the file |

All formats accept `.gz`, `.bz2`, `.xz`, and `.zip` wrappers.

## The wizard

:::steps
1. **Choose a file** — drop it on the zone or pick it from disk.
2. **Upload** — streams to a staging area with byte-level progress; nothing is parsed yet.
3. **Probe** — the server samples a few hundred events and proposes column roles.
4. **Map** — confirm the roles and the format-specific extras.
5. **Import** — parse, normalise, write Parquet, dispatch module precompute.
:::

Staged files live under `data/staging/{user}/{token}/` and are swept after two hours, so an abandoned wizard leaves nothing behind.

| Role | Required | Unlocks |
| --- | --- | --- |
| `case_id` | yes | Cases, variants, everything case-scoped. |
| `activity` | yes | Activities, DFGs, models, complexity. |
| `timestamp` | yes | Ordering, time bounds, trends. |
| `end_timestamp` | no | Durations, waiting times, performance metrics. |
| `resource` | no | Actor analyses (`actor_performance` requires it). |
| `cost` | no | Cost aggregations where a module uses them. |

Format extras appear where they apply: CSV delimiter, XML event element, JSON event path, and an explicit timestamp format when samples are ambiguous. Each row shows its confidence (`user`, `fuzzy`, `fallback`); **AI assist** re-proposes low-confidence rows when a provider is configured. Any edit re-opens the confirmation gate, and the import only starts when you press **Confirm mapping**.

> [!TIP]
> A wrong timestamp format is the most common import mistake. If the probe preview shows dates but a module later reports empty time bounds, remap the roles in the log's settings tab.

## Progress and repair

The checklist mirrors the real job: *Reading data*, *Processing events*, *Saving*, then one row per module (`Queued`, `Running`, `Waiting on <module>`, `Skipped`, `Done`).

Fixable problems are repaired rather than rejected — case-insensitive duplicate columns are merged, values are coerced to their column type, timestamps normalised — and the count of fixes is reported. A module that fails is marked and leaves the log importable.

## After the import

| Action | Where | Effect |
| --- | --- | --- |
| Re-import | Row menu, or *Maintenance* in the log's settings | Rebuilds from the retained original upload. |
| Remap columns | Log settings → *Column roles* | Forces new roles and re-runs the import. |
| Duplicate | Row menu | Clones the log row and directory — useful for comparing filtered views. |
| Rename | Inline in the list | Display name only; URLs keep the log id. |
| Delete | Row menu | Cancels its jobs, removes the data on disk and the S3 prefix. |

# Working with logs

Filtering, editing, and organising logs — the parts of the workflow that happen after the first import.

## Filtering

| Level | Where | Scope | Effect |
| --- | --- | --- | --- |
| Column filter | Events tab | The log | Persisted, and **re-runs every module** on commit. |
| Board filter | Dashboard filter bar | One board | Scopes every card without touching module caches. |
| Card options | Card inspector | One card | Options the widget or dataset declares. |

Operators are deliberately few: `contains`, `equals`, `gte`, `lte`, `is_null`, `is_not_null`, `in`. Time ranges apply to the timestamp column.

A committed filter is part of the analysis: a bottleneck ranking after excluding a resource is genuinely a ranking of what remains. Clearing it restores the full view and re-runs the modules again.

> [!TIP]
> To compare two periods, duplicate the log, commit a different time range on each copy, and use a comparison module (`process_comparison`, `pcomp`) or a dashboard with cards from both.

## Editing

| Action | How | Effect |
| --- | --- | --- |
| Edit a cell | Events tab → *Edit mode* → click a cell (<kbd>Enter</kbd> commits, <kbd>Esc</kbd> cancels) | Rewrites the value in `events.parquet` and appends to the edit history. |
| Bulk fill | Select rows → *Bulk fill* | One value across many rows. |
| Rename an activity | Activities tab → *Display name* | Changes the label across the app without touching the data. |
| Rename a column | Log settings → *Source & schema* | Sets a display label for a column. |

Every change is recorded in the log's **Edit history**. Re-importing from the original discards edits — the source file is the only truth.

## Data quality

The quality view reports per-column completeness, and the events table can be restricted to rows with missing values. Both answer one question: *can the module I want run on this log?* The module card already names any unmet requirement:

| Reason | Fix |
| --- | --- |
| Missing required column | Remap the column roles. |
| Below `min_events` / `min_cases` | Nothing to fix — the module needs more data. |
| Optional role missing | Nothing; the module runs with less context. |
| Log model mismatch | Use a module that declares your log's model. |

## Folders

Folders nest freely; moving one refuses to move it into its own descendant. Deleting a folder cascades to its subfolders and logs, after a confirmation that names the count. The tree supports drag-and-drop reordering and a *Move to folder* row action.

The list reads `?q=` and `?status=` from the URL, so a filtered view is a shareable link.

## Watched folders

A watched folder turns a directory — or an S3 prefix — into an import source.

| Aspect | Behaviour |
| --- | --- |
| Trigger | A poller compares the source against its ledger every 30 s (60 s in `continuous` mode). |
| Modes | `manual` (scan on demand), `interval` (every N seconds), `continuous`. |
| Deduplication | `watched_folder_files` records size plus mtime (local) or ETag (S3), so each file imports once. |
| Source files | Never moved, renamed, or deleted. |
| Mapping | A per-format default mapping is required — there is no wizard for an unattended file. |

The card per folder shows mode, interval, last scan, and a status badge (`active`, `paused`, `error`), with scan, pause/resume, and delete actions. For a local path, the directory must exist **inside the API container** — bind-mount it.

## Log settings

| Section | Holds |
| --- | --- |
| General | Display name and description. |
| Column roles | Required and optional roles with confidence hints; remap re-runs the import. |
| Data quality | Completeness per column. |
| Source & schema | Original filename, format, import time, time bounds, per-column labels. |
| Edit history | Every cell edit with its timestamp. |
| Maintenance | Re-import, delete. |

# Exploring a process

The process page is the workspace for one log. Its tabs are URL-backed, so any view — a filtered event list, one variant, one activity — is a link.

## The frame

The header carries the name, format, model, and a stat strip (cases, events, variants, date range, import time). Tabs stay disabled until the log is `ready`. Banners appear for a mapping that needs review, an import in progress, and a failure.

| Tab | Contents |
| --- | --- |
| Overview | Stat strip, then the module grid with search; disabled cards state their reason. |
| Events | Virtualised event table with search, per-column filters, sorting, and edit mode. |
| Variants | Aggregated variants: cases, average duration, share of cases, last seen. |
| Activities | Activity list with event counts and editable display names. |
| Settings | Everything in [log settings](working-with-logs.html#log-settings). |

OCEL logs swap the middle tabs for **Objects**, **Events**, and **Relationships**, driven by the log's own Parquet schema.

## The events tab

| Control | Behaviour |
| --- | --- |
| Cross-column search | Debounced 300 ms. |
| Missing-values switch | Shows only rows with empty cells. |
| Column filters | Operators per column type: string (`contains`, `equals`, `is null`, `is not null`), numeric and datetime (`equals`, `gte`, `lte`), enum and boolean (`equals`). |
| Apply / Clear | Commits the filter to the log (re-running modules) or clears it. |
| Case chip | Arriving with `?case_id=` restricts the table to that case. |
| Paging | 25, 50, 100, or 200 rows. |

## Variants and activities

The variants tab filters by activity and minimum case count, sorts by cases, duration, or recency, and shows each variant's share of all cases. Opening a variant gives its sequence, a duration histogram, attribute breakdowns, and the list of its cases. The activities tab lists each activity with its event count and lets you set a display name used across the app.

## Cross-module navigation

Wherever a module renders an activity or a variant, the label links into the matching view, carrying the standard drill parameters (`activity`, `case`, `variant`, `from`/`to`, `view`, `metric`). That is what makes a chart a starting point rather than an endpoint.

# Modules

Modules are the platform's capabilities. The **Modules** page manages which ones your account has.

## The library

| Element | Detail |
| --- | --- |
| Card | Name, version, category, description, citation count. |
| Enable switch | Turns the module on or off for your account; disabled modules disappear from grids and dashboards. |
| Configure | The settings form generated from the module's schema. |
| Menu | *View manifest*, *Update*, *Uninstall*. |
| Import | Upload a `.zip`/`.tar.gz`, install from a git URL, or install from a registry package. |

Installing runs as a job: unpack, validate the manifest, `uv sync`, bundle the frontend, mount. A failed install rolls back, so nothing is left half-installed. No restart is involved.

The module detail page shows the rendered README, the parsed manifest, resolved dependencies, citations and artifacts, the configuration forms, and a live tail of that module's log lines.

## Availability

A module is offered for a log only when all of these hold:

- the log model matches (`case_centric` or `object_centric`),
- every column in `required_columns` exists,
- `min_events` and `min_cases` are met,
- every module in `requirements.modules` is installed for your account,
- the module is enabled for your account.

A missing `optional_modules` entry does not block it; the card is marked **Limited** and the module is expected to degrade gracefully.

## Bundled modules

| Module | Id | Category | Needs | Produces |
| --- | --- | --- | --- | --- |
| Discovery | `discovery` | foundation | `case_id`, `activity`, `timestamp`; ≥50 events, ≥2 cases | DFG, Petri nets, process tree, heuristics net, BPMN; datasets + 3 widgets |
| Performance | `performance` | foundation | case, activity, timestamp | Throughput, cycle and lead times, P90, bottleneck ranking; 3 widgets |
| OCEL discovery | `ocel_discovery` | foundation | object-centric log | OC-DFG, object-centric Petri net, per-type summaries; 4 widgets |
| Performance (JVM) | `performance_java` | foundation | case, activity, timestamp; a JRE | Timing metrics computed in Java; 3 datasets |
| Complexity | `complexity` | advanced | ≥50 events, ≥2 cases | Entropy, Lempel-Ziv, affinity, structure, Pentland metrics; 2 widgets |
| Complexity v2 | `complexity_v2` | advanced | ≥50 events, ≥2 cases | A 28-metric suite plus a transition-probability matrix |
| Complexity over time | `complexity_over_time` | advanced | ≥50 events, ≥2 cases | Complexity metrics per time slice |
| Complexity v2 over time | `complexity_v2_over_time` | advanced | ≥50 events, ≥2 cases | The 28-metric suite per time slice |
| Conformance | `conformance` | advanced | a reference BPMN; ≥50 events | Fitness, precision, ranked deviations; 2 widgets |
| CV4CDD | `cv4cdd` | advanced | ≥200 events, ≥20 cases, model upload | CNN-based concept-drift detections |
| Concept drift explainer | `concept_drift_explainer` | advanced | `cv4cdd`; ≥200 events, ≥20 cases; OpenAI + Pinecone | Ranked, citation-backed explanations for each drift |
| Log evolution | `log_evolution` | advanced | case, activity, timestamp | Arrivals vs completions, WIP, activity mix, dotted chart; 4 widgets |
| Performance over time | `performance_over_time` | advanced | case, activity, timestamp | Performance KPIs per time slice |
| Actor performance | `actor_performance` | advanced | `resource`; ≥100 events, ≥5 cases; Neo4j sidecar | Waiting-time decomposition per actor behaviour |
| Agent simulator | `agentsimulator` | advanced | case, activity, timestamp | Agent-based simulation plus a fidelity score; 3 widgets |
| PComp | `pcomp` | comparison | two logs | Two-sample hypothesis test on Earth Mover's Distance |
| Process comparison | `process_comparison` | comparison | two logs | Side-by-side DFG diff, variant differences, EMD similarity |

Three deserve a note: **Discovery** can derive Petri nets (Alpha, Alpha+, Inductive, ILP, IMF), process trees, heuristics nets, and BPMN, and publishes its models as datasets other modules can render. **Performance** declares an optional dependency on Discovery so graphs arrive labelled, and runs in a killable worker because its native work can be long. **Concept drift explainer** is the reference for a module with an external dependency chain and an `ai_models` block that renders its own model pickers.

## Ownership

Module ownership is per account and reference-counted: uninstalling removes *your* install, and shared artifacts (virtual environment, bundle, caches) are deleted when the last owner leaves. **Restore defaults** re-adds the modules you removed. Uploaded modules live in `data/uploaded_modules/`, never in the repository's `modules/` tree. Operators can withhold a module from everyone, or change what new accounts get, on *Admin → Modules*.

# Jobs and the interface

Every long operation is a job, and the interface surfaces them the same way everywhere.

## Four surfaces

| Surface | Where | What it gives you |
| --- | --- | --- |
| Toasts | Bottom-right | One per lifecycle event; failures stay until dismissed and offer *Details*. Rapid sequences collapse. |
| Dock | Bottom-left pill | Active count and the top job's progress; hover expands the three most recent, with per-job cancel. Retires 30 s after the last job. |
| Drawer | The pill, or <kbd>j</kbd> <kbd>j</kbd> | Every job: *All / Running / Queued / Finished*, filter by title or id, cancel all running, pause or resume the queue. |
| Plan checklist | Import groups and the wizard | The precompute plan: each module step as *waiting*, *running*, *skipped*, or *done*, with *Waiting on X* where an ordering edge was declared. |

## Reading a job row

| Element | Meaning |
| --- | --- |
| Status | `queued`, `running`, `paused`, `completed`, `failed`, `cancelled`. |
| Progress | A percentage when the module reports fractions or counts; indeterminate otherwise. |
| Rate and ETA | From the last 20 progress samples, or the module's own `eta_seconds` hint. |
| Stall hint | After three minutes without a tick. A hint about the job, not an error. |
| Actions | Cancel, retry, copy id, and *Open* when the job has a target. |

The queue runs `WORKER_CONCURRENCY` jobs in parallel (default `2`, changeable live by an admin). Queued jobs can be reordered by priority, the queue can be paused without interrupting running work, and a job past `JOB_EXECUTION_TIMEOUT_SECONDS` (default 1800) is force-stopped, offload children included.

## Keyboard

| Keys | Action |
| --- | --- |
| <kbd>j</kbd> <kbd>j</kbd> | Open the jobs drawer (ignored while typing). |
| <kbd>Enter</kbd> / <kbd>Space</kbd> | Open the focused job's details. |
| <kbd>Esc</kbd> | Close a sheet, dialog, or tour step. |
| <kbd>←</kbd> <kbd>→</kbd> | Move through the product tour. |
| <kbd>cmd</kbd>/<kbd>ctrl</kbd> + <kbd>A</kbd> · <kbd>D</kbd> · <kbd>Delete</kbd> | Select all, duplicate, remove cards (dashboard edit mode). |
| Arrows (+ <kbd>shift</kbd>) | Nudge the selected cards. |

## Conventions

| Convention | Meaning |
| --- | --- |
| Colour | Only semantic: good, warning, serious, critical — always with an icon or label, never colour alone. |
| Loading | Skeletons above the fold; a canvas keeps its last result instead of unmounting. |
| Empty and error states | Every list has a designed empty state with the action that fills it, and a designed error state. |
| Motion | `prefers-reduced-motion` disables animation across the app. |

# Dashboards

A dashboard is a canvas of cards from any module, scoped by one filter bar and shareable read-only.

## Creating and editing

:::steps
1. Open **Dashboards** and choose **New dashboard**, **Start from template**, or **Import** a JSON snapshot.
2. Switch to edit mode: the palette lists widgets modules ship and datasets the platform renders.
3. Drag a card onto the grid, move it by its body, resize it by its corner.
4. Select a card to open the inspector: title, and the options the card declares.
5. Leave edit mode — changes autosave, there is no save button.
:::

| Control | Behaviour |
| --- | --- |
| Zoom | Buttons, <kbd>cmd</kbd>/<kbd>ctrl</kbd> + wheel, pinch, *Fit*. |
| Selection | Click, shift-click, or drag; arrows nudge. |
| Undo / redo | Toolbar buttons. |
| Board filters | Column filters and a time range, applied to every card. |

Card filtering is deliberately not per card: filtering belongs to the board or to the card's own declared options. Sizes are enforced in two units — grid units keep the layout tidy, and the card's declared pixel floors keep it readable.

## Cards

| Kind | Source | When to use it |
| --- | --- | --- |
| Widget | A React component the module ships, declared in `manifest.frontend.widgets` | The visual is the point: a bespoke chart or layout. |
| Dataset | A typed data output (`table`, `graph`, `kpi`, `tree`, `blob`) rendered by the platform | The result is data: you get the platform's table, graph, or KPI rendering for free. |

## Sharing

| Action | Who | Notes |
| --- | --- | --- |
| Share with a user or a team | Owner | Read-only; recipients find it under *Shared with me*. |
| Revoke | Owner | Immediate. |
| Join a team | Admin-managed | Teams are the sharing unit; without one, the dialog says so. |

A shared board opens read-only — no palette, no inspector — but readers can still apply their own filters without changing what the owner saved. **Export** writes a portable JSON snapshot with placements and settings but no ids, so it can be imported into another account or installation.

# MATE AI

The assistant lives in a right-hand panel: context-aware, restricted by a data wall, and configured by you.

| Element | Behaviour |
| --- | --- |
| Context | Attached automatically from the URL: current page, log, and the modules in view. |
| Starters | Suggested questions that change with context; the strip hides itself once you have asked there. |
| Answers | Streamed over Server-Sent Events, with navigation chips that deep-link into the app. |
| Guidance cards | Per-module and per-process explanations you can generate, refresh, or regenerate; cached with the module's results. |
| No provider | The panel says so and links to *Settings → AI*; input stays disabled. |

## Providers

| Provider | Notes |
| --- | --- |
| Anthropic | Native transport; no embeddings. |
| OpenAI | Also used for embeddings. |
| UniGPT | Institutional gateway; base URL required. |
| Custom | Any OpenAI-compatible endpoint. |

The form takes a key (masked afterwards; leaving it blank keeps the stored key), a base URL where needed, then *Fetch models* to populate the pickers. When an administrator controls the AI configuration, the panel is read-only and says so. Modules can go further: `ai_models` in their manifest renders their own provider and model selectors, and `self_hosted: true` gives a module an isolated key field.

## The data wall

> [!WARNING]
> Neither MATE AI nor the MCP server can read raw event rows. The restriction is structural: the context built for an AI request has its event-log accessors replaced by objects that raise on any access. What reaches a model is aggregates, cached module outputs, and metadata the page already shows.

Two switches decide how much context is used, both off by default: *process list in prompts* (which logs exist and their headline figures) and *access process data* (activity and variant aggregates, capped at 40 activities and the top 15 variants; skipped for OCEL logs).

# Settings and admin

Settings are per user and stored server-side, so they follow the account across browsers. The **Profile** page holds the account itself.

## Settings tabs

| Tab | Contents |
| --- | --- |
| General | Theme (light/dark/system), back-button behaviour, notification muting, process proficiency, confidential-modules-only filter, time zone, date format, CSV delimiter and timestamp format, storage gauge. |
| Privacy | Tracking explainer, master switch and its granular toggles (form values, keyboard, pointer), your data by type, exports (NDJSON, OCEL 2.0 JSON/SQLite), delete-all. Hidden when the operator forces tracking. |
| AI | Provider, key, base URL, model, and prompt fields for [MATE AI](mate-ai.html). |
| API & MCP | MCP endpoint and client snippet, the external-data-access consent, and personal access tokens (name + scopes at creation, secret shown once, revoke). |
| About | Version, restart the product tour, and **Copy diagnostics** — one blob with system facts, versions, module summaries, and recent log lines. |

## Onboarding

A first login walks through a welcome step (experience level), the privacy choice (omitted when tracking is forced), an embedded import form, then a seven-step product tour. Completion is per-user server state, resettable from *About*.

## Admin

The `admin` realm role unlocks an **Admin** section. Every page is gated server-side and says so when the role is missing.

| Page | Use it for |
| --- | --- |
| Overview | Platform analytics: new users, logs by status and format, top users, job throughput, sessions, top pages, activity by hour. |
| Users | Search, per-user ownership detail, account deletion (Mate data plus the Keycloak identity). |
| Teams | Create, rename, delete teams; manage members. |
| Jobs | The whole queue: pause/resume, cancel, retry, force-kill a process tree, live per-job log tail. |
| Modules | Per-user installs, defaults, withholding a module platform-wide. |
| Controls | Platform-wide module configuration, the shared AI configuration, per-module model pins, worker-pool sizing. |
| Logs | Every log with owner, status, format, and a download of the original upload. |
| System | Live CPU and memory, load by source, running jobs. |
| Export | The metadata export with preview. It spans all users — treat the file as sensitive. |

Anything an administrator sets at platform level wins over a user's own setting; users see a read-only banner naming who controls it.
