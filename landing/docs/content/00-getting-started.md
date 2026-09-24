<!-- Getting started — what MATE is, how to run it, and one pass through the product. -->

<!-- group: Getting started -->

# Introduction

MATE is a self-hosted platform for process mining: it turns event logs into process models, performance figures, conformance results, and drift reports, and it treats every analysis as an installable module.

## Audience

| Role | What MATE gives you |
| --- | --- |
| **Analyst** | A guided path from an uploaded log to charts, models, and dashboards — no code. |
| **Researcher** | A runtime for your method: scheduling, progress, caching, isolation, a settings UI, dashboard cards, and citations that travel with the code. |
| **Operator** | One compose stack to deploy, back up, and monitor. |

## How a log becomes a result

:::stack title="One log, from upload to result" caption="An import normalises the file once. Every module precomputes from that cached result, and the interface reads the cache — never the raw upload."
- `file` · XES, CSV, XML, or OCEL 2.0
- `import job` · parse, normalise, write Parquet
- `Parquet log` · the event table plus cached case aggregates
  - `precompute jobs` · one per module, in parallel
  - `filters & edits` · committing a filter re-runs the affected modules
- `module results` · cached per (log, module)
  - panels, widgets, and datasets
  - `MATE AI` · aggregates only, behind the data wall
- `dashboards` · cards from any module, on one canvas
:::

Three properties shape everything else in this manual:

- **Local-first.** Logs, results, and metadata stay on your host. Nothing is sent anywhere unless you configure an external service on purpose.
- **Modular.** The bundled analyses use the same SDK, loader, and gating rules as modules you install later. There are no privileged hooks.
- **Typed end to end.** Pydantic defines the API, one OpenAPI schema generates the web app's types, and module authors program against Protocols.

## Reading paths

| Goal | Read |
| --- | --- |
| Run MATE | [Install and start](install-and-start.html) |
| See it work once | [Your first analysis](your-first-analysis.html) |
| Import and explore real logs | [Using MATE](importing-event-logs.html) |
| Build a module | [Your first module](your-first-module.html) |
| Call MATE from a script or an agent | [REST API](rest-api.html) · [MCP server](mcp-server.html) |
| Run a deployment | [Deployment](deployment.html) · [Troubleshooting](troubleshooting.html) |
| Work on the platform itself | [How MATE works](architecture.html) · [Contributing](contributing.html) |

## Glossary

Process mining has its own vocabulary; MATE adds a few terms.

| Term | Meaning |
| --- | --- |
| **Event log** | A table of events: what happened, when, and in which case. |
| **Case** | A process instance (an order, a claim) identified by a case id; its events form a trace. |
| **Activity** | The label of what happened — `Registered`, `Approved`. |
| **Variant** | The sequence of activities a case followed. Cases that took the same path share one. |
| **Directly-follows graph (DFG)** | Activities as nodes, observed successions as edges. |
| **Petri net · process tree · BPMN** | Model notations. Discovery produces them; conformance replays a log against one. |
| **Conformance** | Fitness (was every trace replayable), precision, and the deviations found. |
| **Concept drift** | A change in the process over time: a new branch, a vanishing activity, shifted timing. |
| **OCEL** | Object-centric event log: events relate to many objects of different types, so there is no single case notion. |
| **Module** | The unit of extension: a folder with a manifest, a handler class, and optionally a frontend. |
| **Manifest** | `manifest.yaml` — identity, log requirements, dependencies, capabilities, frontend, citations. |
| **Precompute** | Work a module runs automatically after an import, so its page opens with results ready. |
| **Capability** | A named request/response function one module publishes for another. |
| **Event bus** | In-process pub/sub; modules emit and subscribe to declared topics. |
| **Job** | A persisted, observable long operation: import, precompute, install, re-import. |
| **Dataset** | A typed data output (`table`, `graph`, `kpi`, `tree`, `blob`) the platform renders itself. |
| **Widget** | A dashboard card a module ships as a React component. |
| **Guidance** | Cached, module-scoped AI explanation, generated on demand. |
| **Data wall** | The rule that raw event rows never reach an AI model — MATE AI or MCP. |

# Install and start

From a clean host to a logged-in workspace. The whole stack is one compose file.

## Requirements

- Docker Desktop or Docker Engine with Compose v2.
- Free ports `3000` (web), `8000` (API), `8080` (Keycloak).
- ~3 GB for images, plus room for your data.

Python, Node, `uv`, and `pnpm` are only needed for host development ([Contributing](contributing.html)).

## Quick start

:::steps
1. Clone and enter the repository.

   ```bash title="Terminal"
   git clone https://github.com/Process-Science-Community/MATE.git mate
   cd mate
   ```

2. Create the environment file, then rotate its two secrets before the stack is reachable by anyone else.

   ```bash title="Terminal"
   cp .env.example .env
   ```

3. Build and start everything.

   ```bash title="Terminal"
   make up
   ```

4. Open [http://localhost:3000](http://localhost:3000) and sign in as `admin@flows-funds.local` with `flowsfunds`. Keycloak forces a password change on first login.

5. Add users in the Keycloak console at [http://localhost:8080/admin](http://localhost:8080/admin) (`admin` / `admin`), realm `flows-funds`.
:::

> [!WARNING]
> The seeded account is not an administrator. The `admin` realm role gates the admin pages and the metadata export; assign it in the Keycloak console, then sign in again.

## First boot

| Stage | What happens | Time |
| --- | --- | --- |
| Images build | API, web, Keycloak | 3–8 min |
| Databases start | `app-db`, `keycloak-db` | seconds |
| Realm import | Keycloak imports the realm on an empty database | ~30 s |
| API boots | Migrations run, then the loader builds one virtual environment per bundled module | up to 10 min |
| Web boots | Next.js starts and bundles module frontends | ~1 min |

The API health check deliberately allows ten minutes for this. Later boots hash each module's dependency block, skip what is unchanged, and start in seconds.

## Running modes

| Command | What you get |
| --- | --- |
| `make up` | Base compose file: production-style images, detached. The default. |
| `make up-dev` | Base plus `compose.dev.yml`: hot reload inside Docker. |
| `make dev` | Host dev servers (API + web) with the fastest reload. Needs `uv`, `pnpm`, Python 3.12. |
| Prod overlay | `docker-compose.prod.yml`: Caddy with TLS, one public origin, no public app ports. See [Deployment](deployment.html). |

## Daily operations

```bash title="Terminal"
make down                     # stop (keeps volumes and data)
make up                       # start again, reusing module environments
docker compose logs -f api    # follow the API log (also: web, keycloak)
```

`make clean` wipes event logs, module results, the metadata database, and the Keycloak volume. This cannot be undone.

> [!NOTE]
> For a workshop without Keycloak, `DEMO_MODE=true` signs everyone in as a fixed local user. Never enable it on a shared deployment — the MCP server refuses the demo token by design.

# Your first analysis

One log from import to dashboard: import it, monitor the jobs, open a module, and build a board.

## 1. Import a log

:::steps
1. Open **Processes** and click **Import event log**.
2. Drop a file: `.xes`, `.xes.gz`, `.csv`, `.xml`, `.json`, `.jsonocel`, `.xmlocel`, `.sqlite`, and `.gz`/`.bz2`/`.xz`/`.zip` variants.
3. The upload stages, then the server probes a few hundred events and proposes column roles.
4. Confirm the mapping — the only step where a wrong answer costs a re-import.
5. Submit. The wizard switches to a live checklist: reading, processing, saving, preparing modules.
:::

| Role | Required | Unlocks |
| --- | --- | --- |
| `case_id` | yes | Cases, variants, everything case-scoped. |
| `activity` | yes | Activities, DFGs, models, complexity. |
| `timestamp` | yes | Ordering, time bounds, trends. |
| `end_timestamp` | no | Durations, waiting times, most performance metrics. |
| `resource` | no | Actor analyses; `actor_performance` requires it. |

Each row shows how the proposal was made — `user` → *Your choice*, `exact` → *Matched*, `fuzzy` → *Guessed*, `fallback` → *Inferred from data* — and low-confidence guesses are flagged. When a provider is configured, the wizard re-proposes those rows automatically and marks them **AI**. XES and OCEL files carry their own schema and skip this step.

## 2. Watch the jobs

The checklist lists every module that precomputes on import, with a state per module: *Queued*, *Running*, *Waiting on X*, *Skipped*, *Done*. A module that fails never blocks the log — the log flips to **ready** once every precompute job has settled, and the failure is in the jobs drawer.

| Status | Meaning |
| --- | --- |
| `importing` | The file is parsed and normalised. |
| `processing` | Module precompute is running; the log is not open yet. |
| `ready` | Everything settled. Modules are usable. |
| `failed` | The import failed; the row shows why, with a retry. |

## 3. Open the process

Click the row. The process page opens on the module grid, grouped by category.

| Card state | Meaning |
| --- | --- |
| Available | Everything the module needs is present — shown without a badge. |
| Limited | An optional module dependency is missing; the module runs with less context. |
| Unavailable | The log model, a required column, a minimum count, or a hard dependency does not match; the tooltip names the reason. |
| Disabled | Turned off for your account. |
| Running | Its precompute job is running right now. |

## 4. Read a module

Open **Discovery** for the process as a directly-follows graph (and Petri net, process tree, heuristics net, or BPMN), **Performance** for throughput and bottlenecks, **Complexity v2** for a metric suite. Each page is the module's own panel inside the platform's frame: breadcrumb, version, **Configure** (a form generated from the module's schema, saved per user), and an info hint explaining the view.

## 5. Compose a dashboard

:::steps
1. Open **Dashboards** and create a board, or start from a template.
2. In edit mode, drag cards from the palette: widgets modules ship, and datasets the platform renders.
3. Select a card to set its options; the board's filter bar scopes every card at once.
4. Share the board read-only with a user or a team.
:::

## 6. Ask the assistant

The MATE AI panel attaches context from the current view — process, open module, page — and answers with links that navigate the app. If no provider is configured it says so and links to *Settings → AI*.

> [!NOTE]
> The assistant reads aggregates and cached module outputs only. Raw event rows never enter a prompt, which is what lets it sit next to confidential data.

## Recap

| Action | What the platform did |
| --- | --- |
| Dropped a file | Staged, probed, parsed, normalised, written as Parquet under `data/users/{uid}/event_logs/{log_id}/`. |
| Confirmed a mapping | Column roles stored on the log; a `log.imported` event dispatched. |
| Watched a checklist | Each subscribing module's precompute job ran — in parallel, ordered by declared dependencies. |
| Opened a module | Its panel called its own routes; the platform served the cached precompute result. |
| Filtered the log | The filter was committed and the affected modules re-ran, so results match what you see. |
