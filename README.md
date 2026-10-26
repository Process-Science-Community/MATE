# MATE

**A self-hosted, modular platform for process mining.** Upload an event log, then explore it with installable analysis modules: process discovery, performance, conformance checking, complexity, concept-drift detection, simulation, and more. Every user gets a fully isolated workspace.

- **Local-first.** Logs, results, and metadata stay on your machine. No cloud, no message broker. Metadata lives in SQLite (Postgres in the Docker stack) and event logs in DuckDB + Parquet. Nothing leaves the host unless you configure an external service (S3 storage, an LLM provider) on purpose.
- **Modular.** Every analysis is a module with the same SDK, loader, and rules, including the bundled ones. There are no privileged first-party hooks.
- **Multi-user.** Login via Keycloak (OIDC); every log, job, dashboard, and API key is scoped to its owner.

📖 **Manual:** [process-science-community.github.io/MATE](https://process-science-community.github.io/MATE/docs/site/introduction.html) · 🌐 **Hosted instance (WWU Münster):** [mate.uni-muenster.de](https://mate.uni-muenster.de/login)

---

## What do you want to do?

| I want to… | Go to |
| --- | --- |
| **Try MATE on my machine** | [Quick start](#quick-start) |
| **Develop on the platform** (API / web) | [Development](#development) |
| **Build my own analysis module** | [Building a module](#building-a-module) |
| **Deploy it for other people** | [`docs/DEPLOY.md`](./docs/DEPLOY.md) |
| **Use it from a script or an AI agent** | [REST API / MCP server](#use-mate-from-scripts-and-ai-agents) |
| **Understand how it works** | [Architecture & docs](#how-it-fits-together) |

---

## Quick start

You need **Docker** (Docker Desktop, or Docker Engine + Compose v2) and free ports `3000`, `8000`, `8080`. Nothing else has to be installed on your host.

```bash
git clone https://github.com/Process-Science-Community/MATE.git mate
cd mate
cp .env.example .env
make up
```

Then open <http://localhost:3000>.

- **No login needed locally.** `.env.example` sets `DEMO_MODE=true`, which signs you in automatically as a fixed demo user. This is for local development only; see [Before you share it](#before-you-share-it).
- **First start takes several minutes**: the images are built and each module installs its own Python dependencies (the `cv4cdd` module alone pulls TensorFlow, ~500 MB). Later starts reuse the cache and take seconds. Budget ~3 GB of disk.
- **Your first analysis:** on the Processes page, drop in an event log (XES, XES.gz, CSV, XML, or OCEL 2.0), open it, and pick a module such as **Discovery** or **Performance**. A walkthrough is in the manual: [Your first analysis](https://process-science-community.github.io/MATE/docs/site/your-first-analysis.html).
- **Stop:** `make down`. Follow logs with `docker compose logs -f api` (or `web`).

> Want real logins instead of the demo user? Set `DEMO_MODE=false` and a strong `AUTH_SECRET` (`openssl rand -base64 32`) in `.env`, restart, and sign in via the bundled Keycloak with `admin@flows-funds.local` / `flowsfunds` (you must change the password on first login). The Keycloak admin console is at <http://localhost:8080/admin> (`admin` / `admin`), realm `flows-funds`.

## Ways to run it

`make up` *is* `docker compose up -d --build`; the modes differ in which compose files are layered:

| Command | What you get | Needs on host |
| --- | --- | --- |
| `make up` | Production-style built images, detached, no reload. The default. | Docker |
| `make up-dev` | Same stack in Docker with hot reload (`uvicorn --reload` + `next dev`, source mounted). | Docker |
| `make dev` | **No Docker.** API + web dev servers directly on your machine; fastest inner loop. SQLite under `data/`. | Python 3.12, `uv`, Node 20+, `pnpm` |
| prod overlay | `docker-compose.yml` + `docker-compose.prod.yml` adds Caddy/TLS on one origin. No `make` target; see [`docs/DEPLOY.md`](./docs/DEPLOY.md). | Docker, a host |

## Development

```bash
make install    # uv sync --extra dev  +  pnpm install
make dev        # migrations, then API (:8000) and web (:3000) with reload
```

`make dev` runs from the same root `.env` (copy `.env.example` first) and, with `DEMO_MODE=true`, needs no Keycloak. Its preflight frees port 8000 and kills stale API workers. Run `make help` for the full list. The ones you will use most:

| Command | What it does |
| --- | --- |
| `make dev-api` / `make dev-web` | Run only one of the two servers |
| `make test` | Python API tests (`pytest apps/api/tests`) |
| `uv run pytest modules/<folder>/tests` | One module's tests |
| `uv run pyright` | Strict Python type check |
| `make typecheck` | Web app type check |
| `make fmt` | Format and lint Python with ruff |
| `make codegen` | Regenerate the web app's API types (API must be running on `:8000`) |
| `make docs` | Rebuild the manual from `landing/docs/content/*.md` |
| `make clean` | **Irrevocably** wipe local logs, results, and metadata under `data/` |

Database changes go in a new Alembic migration under `apps/api/alembic/versions/` (applied on boot). Contribution paths and conventions: [Contributing](https://process-science-community.github.io/MATE/docs/site/contributing.html). Using Claude Code? [`AGENT.md`](./AGENT.md) is the orientation file.

## Bundled modules

Everything under [`modules/`](./modules/) is discovered on startup. Enable, disable, and configure modules under **Settings → Modules**.

| Area | Modules |
| --- | --- |
| **Discovery** | `discovery` (DFG, Petri net, process tree, heuristics net, BPMN) · `ocel_discovery` (object-centric logs) |
| **Performance** | `performance` · `performance_java` (JVM runtime example) · `performance_over_time` · `actor_performance` |
| **Conformance & comparison** | `conformance` (replay against a BPMN model) · `process_comparison` · `pcomp` (statistical test between two logs) |
| **Complexity** | `complexity` · `complexity_v2` (28 metrics) · `complexity_over_time` · `complexity_v2_over_time` |
| **Change over time** | `cv4cdd` (concept-drift detection) · `concept_drift_explainer` (LLM, grounded in your documents) · `log_evolution` · `temporal_mapper` |
| **Simulation** | `agentsimulator` |

Users can also import their own modules from a zip or git URL via **Settings → Modules → Import**. Each module ships a `HOW_TO_USE.md` guide, shown behind the **How to use** button in the app.

The right-hand **Mate AI** chat panel talks to your own LLM provider (**Settings → AI**); keys stay in your local metadata database, and the assistant sees aggregates, never raw event rows.

## Building a module

A module is one self-contained folder; you never edit `apps/api` or `apps/web` to ship one.

```bash
mkdir modules/my_mod
$EDITOR modules/my_mod/manifest.yaml   # id, name, dependencies, frontend entry
$EDITOR modules/my_mod/module.py       # subclass mate.sdk.Module
make up-dev                            # or make dev; a restart picks it up
```

Start with [`modules/README.md`](./modules/README.md) (the authoring contract) and copy from `modules/temporal_mapper/` (a complete example, including its `HOW_TO_USE.md`). Python SDK: [`packages/module-sdk-py`](./packages/module-sdk-py); frontend SDK: [`packages/module-sdk-ts`](./packages/module-sdk-ts). Deleting the folder removes the module.

## Use MATE from scripts and AI agents

- **REST API:** every UI action is an API call. Interactive docs at <http://localhost:8000/docs> when running; reference in the manual ([REST API](https://process-science-community.github.io/MATE/docs/site/rest-api.html)).
- **MCP server:** lets Claude, Codex, and other MCP clients read your analyses (never raw event rows) and drive MATE. Off by default (`MCP_ENABLED=false`); the API refuses to start with both `MCP_ENABLED` and `DEMO_MODE` on. See [`docs/MCP.md`](./docs/MCP.md).

## How it fits together

```
mate/
├── apps/
│   ├── api/                 FastAPI backend (jobs, storage, auth, module loader, MCP)
│   └── web/                 Next.js 15 frontend
├── modules/                 Bundled analysis modules (+ authoring docs)
├── packages/
│   ├── module-sdk-py/       Python SDK for module authors
│   ├── module-sdk-ts/       TypeScript SDK for module frontends
│   ├── module-sdk-jvm/      JVM SDK for non-Python modules
│   └── shared-types/        Generated TS types from the OpenAPI schema
├── infra/                   Keycloak realm + scripts, Caddy, VM bootstrap
├── landing/                 Public landing page and the manual (landing/docs/)
├── docs/                    Design spec, deploy runbook, MCP and S3 references
├── data/                    Runtime state (gitignored): metadata DB, Parquet logs, results
├── docker-compose.yml       Base stack: api, web, Keycloak, Postgres
├── compose.dev.yml          Dev overlay: hot reload (make up-dev)
└── docker-compose.prod.yml  Prod overlay: Caddy/TLS
```

**Where to read next**

| Doc | What it covers |
| --- | --- |
| [`docs/README.md`](./docs/README.md) | Index of every document in the repo |
| [`docs/INSTRUCTIONS.md`](./docs/INSTRUCTIONS.md) | Design spec and the reasoning behind the architecture |
| [`modules/README.md`](./modules/README.md) | Module authoring contract |
| [`docs/DEPLOY.md`](./docs/DEPLOY.md) | Production deployment runbook |
| [`docs/S3_OFFLOAD.md`](./docs/S3_OFFLOAD.md) | Optional S3-compatible storage backend |
| [Manual](https://process-science-community.github.io/MATE/docs/site/introduction.html) | User, operator, and developer documentation (source in `landing/docs/content/`) |

## Configuration

All configuration is one root `.env` (template: [`.env.example`](./.env.example), every variable is commented). The ones you are most likely to touch:

| Variable | Purpose |
| --- | --- |
| `DEMO_MODE` / `DEMO_ADMIN` | Skip Keycloak and sign in as a local demo user (optionally an admin). **Local only.** |
| `AUTH_SECRET` | Session-cookie secret. Required when `DEMO_MODE=false`. |
| `NEXT_PUBLIC_API_URL` | Where the browser reaches the API. Baked in at build time; changing it needs a rebuild. |
| `STORAGE_MODE` (`local` / `s3`) | Keep data under `data/` or offload to an S3-compatible bucket. |
| `USER_TRACKING_ONBOARDING` (`force` / `on` / `off`) | Anonymous usage-tracking policy. Events stay in the local database. |
| `MCP_ENABLED` | Turn on the MCP server. |
| `KEYCLOAK_*` | Login provider settings; rotate the secrets before any shared deployment. |

**Data and backups:** everything MATE stores is in `./data/` (copy it to back up). Module folders and their `.venv/` live in `./modules/`; user-imported modules land in `data/uploaded_modules/`. Admins can download a full metadata snapshot at `/admin/export`.

### Before you share it

The defaults are tuned for a single developer on `localhost`. Before exposing MATE to anyone else: set `DEMO_MODE=false`, set a strong `AUTH_SECRET`, rotate `KEYCLOAK_CLIENT_SECRET` and the Keycloak admin credentials, and follow [`docs/DEPLOY.md`](./docs/DEPLOY.md).

## Contributing, issues, license

Bugs and feature requests: [GitHub Issues](https://github.com/Process-Science-Community/MATE/issues). Include the diagnostics blob from **Settings → About**. Modules are the highest-impact contribution; see [Building a module](#building-a-module) and [Contributing](https://process-science-community.github.io/MATE/docs/site/contributing.html).

MIT licensed. © 2026 European Research Center for Information Systems (ERCIS), University of Münster. See [`LICENSE`](./LICENSE).
