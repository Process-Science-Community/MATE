<div align="center">

<img src="landing/assets/mate-icon-black.svg#gh-light-mode-only" alt="" width="64">
<img src="landing/assets/mate-icon-white.svg#gh-dark-mode-only" alt="" width="64">

# MATE

**A modular process science platform.**
Import event logs, run process mining analyses as plug-in modules, and combine their results on one dashboard. Self-hosted, every user in an isolated workspace.

[![License: MIT](https://img.shields.io/badge/license-MIT-blue.svg)](./LICENSE)
![Python 3.12](https://img.shields.io/badge/python-3.12-3776AB?logo=python&logoColor=white)
![Next.js 15](https://img.shields.io/badge/Next.js-15-000000?logo=nextdotjs&logoColor=white)
![Docker Compose](https://img.shields.io/badge/Docker-Compose-2496ED?logo=docker&logoColor=white)

[Website](https://process-science-community.github.io/MATE/) ·
[User manual](https://process-science-community.github.io/MATE/docs/site/introduction.html) ·
[Hosted instance](https://mate.uni-muenster.de) ·
[Build a module](#develop-modules)

</div>

<picture>
  <source media="(prefers-color-scheme: dark)" srcset="landing/assets/shot-hero-dark.png">
  <img src="landing/assets/shot-hero.png" alt="MATE workspace with a discovered process model">
</picture>

## Contents

- [Features](#features)
- [Quick start](#quick-start)
- [Prerequisites](#prerequisites)
- [Signing in](#signing-in)
- [Running modes](#running-modes)
- [Develop modules](#develop-modules)
- [Bundled modules](#bundled-modules)
- [Configuration](#configuration)
- [Data and persistence](#data-and-persistence)
- [Tests](#tests)
- [Repository layout](#repository-layout)
- [Documentation](#documentation)
- [License](#license)

## Features

- **Event log import**: XES, XES.gz, CSV and OCEL (`.jsonocel`, `.xmlocel`, OCEL 2.0 SQLite), also inside `.gz`, `.bz2`, `.xz`, `.zip` and tar archives. Logs are stored as Parquet and queried with DuckDB.
- **Analyses as modules**: discovery, performance, conformance, complexity, concept drift, simulation and more. Each module is a folder under [`modules/`](./modules/) and is picked up at startup.
- **Isolated workspaces**: event logs, jobs, AI keys and module settings are scoped to the signed-in user.
- **MATE AI**: a chat panel wired to an LLM provider you configure yourself.
- **MCP server** (off by default): lets external AI clients use the platform. See [`docs/MCP.md`](./docs/MCP.md).
- **Self-hosted**: FastAPI backend, Next.js frontend, Keycloak for login, all started with Docker Compose.

## Quick start

With [Git, Docker and make installed](#prerequisites):

```bash
git clone https://github.com/Process-Science-Community/MATE.git
cd MATE
cp .env.example .env
make up
```

`make up` runs `docker compose up -d --build`. It builds the `api` and `web` images and starts five containers: `api`, `web`, `keycloak`, `keycloak-db` and `app-db`.

The first start takes a while: the images are built, and the API installs every module's Python dependencies before it reports healthy (the healthcheck allows up to 10 minutes). Later starts reuse the cached images and dependencies.

Open <http://localhost:3000> once the containers are healthy:

```bash
docker compose ps        # STATUS shows "(healthy)" for api and web when ready
```

You are signed in automatically as a local demo user (see [Signing in](#signing-in)). Drop an event log on the **Processes** page to start.

Stop the stack with `make down`. Your data stays in `./data/` and in the Docker volumes.

> **No `make`?** (for example on Windows) Run the commands directly:
> `docker compose up -d --build` to start, `docker compose down` to stop.

## Prerequisites

### For the Docker stack (`make up`)

| Tool | Version | Check |
| --- | --- | --- |
| [Git](https://git-scm.com/downloads) | any recent | `git --version` |
| [Docker](https://docs.docker.com/get-started/get-docker/) with Compose v2 | Docker Desktop, or Docker Engine + Compose plugin | `docker compose version` |
| `make` | any (optional) | `make --version` |

Also needed: free ports **3000** (web), **8000** (API) and **8080** (Keycloak), and enough disk and memory for the images (see [System requirements](#system-requirements)).

No Python, Node.js, `uv` or `pnpm` is needed on the host for the Docker stack.

**macOS**

```bash
xcode-select --install            # Git and make (Apple Command Line Tools)
brew install --cask docker        # Docker Desktop (or download it from docker.com)
open -a Docker                    # start Docker Desktop once, accept the terms
```

**Ubuntu / Debian**

```bash
sudo apt update && sudo apt install -y git make
curl -fsSL https://get.docker.com | sh          # Docker Engine + Compose plugin
sudo usermod -aG docker "$USER"                 # run docker without sudo; log out and back in
```

For other distributions, follow the [Docker Engine install guide](https://docs.docker.com/engine/install/).

**Windows**

Use Docker Desktop with the WSL 2 backend and run all commands inside a WSL 2 Linux shell (for example Ubuntu), following the Ubuntu steps above for Git and make:

```powershell
wsl --install                                  # once, then reboot
winget install -e --id Docker.DockerDesktop
```

Clone the repository inside the WSL file system (for example `~/MATE`), not under `C:\`; bind mounts from the Windows file system are much slower. The Windows setup has not been tested by the maintainers.

### System requirements

| Resource | Recommendation |
| --- | --- |
| Free disk | **20 GB or more.** In a test from a fresh clone, Docker's disk image grew by about 12 GB (base images, the two built images, databases), and the module dependencies in `modules/<id>/.venv/` passed 3 GB (`cv4cdd` pulls TensorFlow, `agentsimulator` about 1 GB). |
| Docker memory | 8 GB assigned to Docker Desktop (the setting used in testing on an Apple Silicon Mac). |

## Signing in

The default `.env.example` sets `DEMO_MODE=true`. Login then skips Keycloak and signs you in as a fixed local **Demo User**. This is meant for local use only.

For real accounts, set in `.env`:

```bash
DEMO_MODE=false
AUTH_SECRET=<output of: openssl rand -base64 32>
```

Then restart with `make up`. Login goes through Keycloak:

The bundled Keycloak advertises itself as `keycloak:8080` (`KC_HOSTNAME` in [`docker-compose.yml`](./docker-compose.yml)), so the browser has to resolve that name. Add this line to `/etc/hosts` (on Windows: `C:\Windows\System32\drivers\etc\hosts`):

```text
127.0.0.1 keycloak
```

| What | Where | Credentials |
| --- | --- | --- |
| MATE | <http://localhost:3000> | `admin@flows-funds.local` / `flowsfunds` (you must set a new password on first login) |
| Keycloak admin console | <http://localhost:8080/admin> | `admin` / `admin` (from `KEYCLOAK_ADMIN` / `KEYCLOAK_ADMIN_PASSWORD`) |

Create further users in the Keycloak admin console, realm `flows-funds`. Admin pages in MATE (`/admin`) require the realm role `admin`; assign it under *Realm roles → admin → Users in role*. The seeded `admin@flows-funds.local` user does not have it by default.

> [!WARNING]
> Before exposing the stack to anyone else, set `DEMO_MODE=false`, a strong `AUTH_SECRET`, and change `KEYCLOAK_CLIENT_SECRET`, `KEYCLOAK_ADMIN_PASSWORD` and `KEYCLOAK_DB_PASSWORD`. Production deployments use the overlay described in [`docs/DEPLOY.md`](./docs/DEPLOY.md).

## Running modes

`make up` and `docker compose` are the same thing; what changes is which compose overlay is stacked on [`docker-compose.yml`](./docker-compose.yml), or whether Docker is used at all.

| Command | Docker | `.env` | What you get |
| --- | --- | --- | --- |
| `make lite` | no | no | Low-memory native stack for module work: demo login with admin rights, SQLite under `data/lite/`, only the modules in `MODULES=a,b`, debugger on port 5678, sample log imported. See [`docs/DEV_LITE.md`](./docs/DEV_LITE.md). |
| `make dev` | no | yes | API and web dev servers on the host with hot reload. Needs `uv` and `pnpm` (`make install` first). |
| `make up` | yes | yes | Production-style images, detached, no reload. The quick-start mode. |
| `make up DEV=1` | yes | yes | Adds [`compose.dev.yml`](./compose.dev.yml): `uvicorn --reload` and `next dev` with the source mounted. Runs in the foreground. |
| Production | yes | yes | Adds [`docker-compose.prod.yml`](./docker-compose.prod.yml) (Caddy, TLS, single origin). No `make` target; see [`docs/DEPLOY.md`](./docs/DEPLOY.md). |

Rule of thumb: build modules with `make lite`, work on the platform with `make up DEV=1` or `make dev`, check the production build with `make up`.

## Develop modules

Module work runs natively, without Docker or `.env`, and loads only the modules you name. With one module the whole stack (API, module bundler, Next.js dev server) used about 1 GB of RAM in testing and was up within 20 seconds after `make install`.

### Prerequisites

| Tool | Version | Install | Check |
| --- | --- | --- | --- |
| Git, `make` | any | see [above](#prerequisites) | `git --version` |
| [uv](https://docs.astral.sh/uv/) | recent | `curl -LsSf https://astral.sh/uv/install.sh \| sh` or `brew install uv` | `uv --version` |
| [Node.js](https://nodejs.org/) | 22 (as in the web image) | `brew install node@22`, or a version manager such as [fnm](https://github.com/Schniz/fnm): `fnm install 22` | `node --version` |
| [pnpm](https://pnpm.io/) | any recent | `corepack enable pnpm` (ships with Node 22) | `pnpm --version` |
| JDK 17+ | optional | only for JVM modules (`make jvm`) | `java -version` |

uv installs the Python version pinned in [`.python-version`](./.python-version) (3.12) by itself.

### Workflow

```bash
git clone https://github.com/Process-Science-Community/MATE.git
cd MATE
make install                  # uv + pnpm dependencies, once
make new ID=my_mod            # scaffold modules/my_mod/ (PANEL=1 adds a React panel)
make lite MODULES=my_mod      # API + web with hot reload, demo login, sample log
```

Then:

1. Open <http://localhost:3000/dev> and choose **Open Studio** to build the module page visually. Cards are saved to `modules/my_mod/layout.json`.
2. Edit `modules/my_mod/module.py`; the API reloads on save.
3. Set breakpoints and attach VS Code with **Attach: MATE API (lite)** (port 5678).
4. Run `make test ID=my_mod`, then `make validate` before you ship.

`make lite MODULES=a,b` loads several modules, `make modules` lists all modules, `make reset` wipes the lite data. To ship a module, commit its folder under `modules/`, or zip it and upload it under **Modules → Install a module** (`/modules/import`).

| Read | For |
| --- | --- |
| [`docs/DEV_LITE.md`](./docs/DEV_LITE.md) | Lite mode details, Studio card kinds, debugging, troubleshooting |
| [`modules/README.md`](./modules/README.md) | The module contract: manifest, `module.py`, context API, frontend, dependencies |
| [`modules/PROTOCOL.md`](./modules/PROTOCOL.md) | Wire protocol for subprocess and JVM modules |
| [`modules/SIDECAR_SERVICES.md`](./modules/SIDECAR_SERVICES.md) | Modules that need their own container (for example Neo4j) |

## Bundled modules

Every folder under [`modules/`](./modules/) is discovered at startup. Enable, disable and configure modules on the **Modules** page (`/modules`); `make modules` lists them.

| Module | What it does |
| --- | --- |
| `discovery` | Process models as directly-follows graph, Petri net (Alpha or Inductive miner), process tree, heuristics net or BPMN |
| `ocel_discovery` | Object types, activities and the object-centric directly-follows graph for OCEL logs |
| `conformance` | Checks a log against an uploaded BPMN model: fitness, precision, per-activity deviations |
| `performance` | Throughput, cycle and lead times, 90th-percentile cycle time, bottlenecks |
| `performance_java` | Timing metrics from a JVM module: cycle time, throughput, longest waits, slowest hand-offs |
| `performance_over_time` | The performance KPIs per time slice, as a trend |
| `actor_performance` | Waiting times decomposed by actor behaviour (needs the Neo4j sidecar, see [`docs/DEPLOY.md`](./docs/DEPLOY.md)) |
| `complexity` | Entropy-based variant and sequence complexity, Lempel-Ziv, affinity, structure, Pentland's task and process complexity |
| `complexity_v2` | 28 complexity metrics (entropy, size, variation, trace distance) plus the transition-probability matrix |
| `complexity_over_time`, `complexity_v2_over_time` | The complexity suites per time slice, as a trend |
| `log_evolution` | Case arrivals vs. completions, work in progress, activity mix over time, dotted chart |
| `temporal_mapper` | Recurring activity patterns across calendar periods |
| `process_comparison` | Two filtered views side by side: DFG diff, variant differences, similarity scores |
| `pcomp` | Statistical test whether two logs differ (Earth Mover's Distance, permutation or bootstrap test) |
| `cv4cdd` | Concept drift detection (sudden, gradual, incremental, recurring) with a computer-vision model |
| `concept_drift_explainer` | Ranked, cited explanations for detected drifts from your uploaded documents; sends data to an external LLM |
| `agentsimulator` | Agent-based simulation that learns from a log and generates synthetic logs |

## Configuration

All settings live in the root `.env`; [`.env.example`](./.env.example) documents every variable. The most important ones:

| Variable | Default | Purpose |
| --- | --- | --- |
| `DEMO_MODE` | `true` | Skip Keycloak and sign in as a local demo user. Never enable outside local development. |
| `DEMO_ADMIN` | `false` | Give the demo user the `admin` role. |
| `AUTH_SECRET` | empty | Encrypts the Auth.js session cookie. Required when `DEMO_MODE=false`. Generate with `openssl rand -base64 32`. |
| `NEXT_PUBLIC_API_URL` | `http://localhost:8000` | URL the browser uses to reach the API. Inlined at build time: rebuild after changing it (`make build`). |
| `USER_TRACKING_ONBOARDING` | `force` | Usage tracking policy: `force` (always on, no opt-out), `on` (opt-out), `off` (opt-in). |
| `STORAGE_MODE` | `local` | `local` keeps logs and results under `data/`; `s3` uses an S3-compatible bucket. See [`docs/S3_OFFLOAD.md`](./docs/S3_OFFLOAD.md). |
| `MCP_ENABLED` | `false` | Enables the MCP endpoint at `/mcp`. See [`docs/MCP.md`](./docs/MCP.md). |
| `KEYCLOAK_CLIENT_SECRET` | dev value | Secret of the `flows-funds-web` client. Change it before any non-local deployment. |
| `KEYCLOAK_ADMIN`, `KEYCLOAK_ADMIN_PASSWORD` | `admin`, `admin` | Keycloak admin console login. |

The browser origin allowed by the API (`CORS_ORIGINS`) is set to `http://localhost:3000` in [`docker-compose.yml`](./docker-compose.yml); change it there if the web origin changes.

### MATE AI

The chat panel on the right uses an LLM provider you configure under **Settings → AI**. The provider, model and API key are stored per user in the metadata database. Requests go only to the provider you configure.

### Privacy and usage tracking

Usage events are recorded in the local metadata database; nothing is sent to an external analytics service. With the default `USER_TRACKING_ONBOARDING=force`, tracking is on for everyone and the **Settings → Privacy** tab is hidden. Accounts with a role listed in `USER_TRACKING_EXEMPT_ROLES` (default `reviewer`) are never tracked.

## Data and persistence

| What | Docker stack (`make up`) | Host modes (`make dev`, `make lite`) |
| --- | --- | --- |
| Metadata (users, jobs, settings) | PostgreSQL 16 in the `app-db-data` volume | SQLite: `data/metadata.db` (`make dev`), `data/lite/metadata.db` (`make lite`) |
| Event logs (Parquet) and module results | `data/users/<user_id>/` | `data/users/<user_id>/` (`make lite`: under `data/lite/`) |
| Keycloak users and realm | PostgreSQL 16 in the `kc-data` volume | not used in demo mode |
| Python runtimes and module dependencies | `data/uv-python/`, `modules/<id>/.venv/` | host `uv` cache, `modules/<id>/.venv/` |

- `./data/` and `./modules/` are bind-mounted into the containers. To back up the Docker stack, copy `./data/` **and** dump the `app-db` database (or use **Admin → Export**, which downloads a `pg_dump` file; on SQLite it is a `.db` copy).
- Modules uploaded through the UI land in `data/uploaded_modules/<id>/`, separate from the bundled ones in `modules/`. Each user gets the default modules on first login; **Restore defaults** on the Modules page re-adds removed ones.
- Each user's event logs and results live under `data/users/<user_id>/`, keyed by the Keycloak user id; the API filters jobs and live events by user.

## Tests

```bash
make install       # once: uv + pnpm dependencies on the host
make test          # Python test suite (pytest, on the host)
make typecheck     # TypeScript check of the web app
make validate      # validate every module's manifest and layout
```

## Common commands

Run `make` (or `make help`) for the full list.

| Command | What it does |
| --- | --- |
| `make up` / `make down` | Start / stop the Docker stack |
| `make up DEV=1` | Docker stack with hot reload |
| `make build` | Rebuild both images |
| `docker compose logs -f api` | Follow the API logs (`web`, `keycloak` likewise) |
| `make lite MODULES=a,b` | Native low-memory stack for module work |
| `make new ID=x` | Scaffold a module |
| `make modules` | List modules |
| `make docs` | Rebuild the user manual from `landing/docs/content/` |
| `make clean` | Delete event logs, module results, user data and the SQLite metadata under `data/`. Irreversible. |

## Repository layout

```
MATE/
├── apps/
│   ├── api/                 # FastAPI backend (Python 3.12)
│   └── web/                 # Next.js 15 frontend
├── modules/                 # Bundled modules, one folder each
├── packages/
│   ├── module-sdk-py/       # Python SDK for module authors
│   ├── module-sdk-ts/       # TypeScript SDK for module frontends
│   ├── module-sdk-jvm/      # JVM SDK for Java modules
│   └── shared-types/        # TypeScript types generated from the OpenAPI schema
├── infra/                   # Keycloak realm, theme and setup scripts
├── landing/                 # Website and user manual (GitHub Pages)
├── docs/                    # Design spec, deploy runbook, MCP reference
├── scripts/                 # Helper scripts used by the Makefile
├── docker-compose.yml       # Base stack
├── compose.dev.yml          # Dev overlay: hot reload (make up DEV=1)
└── docker-compose.prod.yml  # Prod overlay: Caddy and TLS
```

## Documentation

| Document | Contents |
| --- | --- |
| [User manual](https://process-science-community.github.io/MATE/docs/site/introduction.html) | Using MATE: import, analyses, dashboards. Source in [`landing/docs/`](./landing/docs/). |
| [`docs/README.md`](./docs/README.md) | Index of all documentation in the repository |
| [`docs/INSTRUCTIONS.md`](./docs/INSTRUCTIONS.md) | Design spec and architecture rationale |
| [`docs/DEV_LITE.md`](./docs/DEV_LITE.md) | Module development with `make lite` |
| [`docs/DEPLOY.md`](./docs/DEPLOY.md) | Production deployment |
| [`docs/MCP.md`](./docs/MCP.md) | MCP server for AI clients |
| [`modules/README.md`](./modules/README.md) | Module authoring contract |

## License

[MIT](./LICENSE) © 2026 European Research Center for Information Systems (ERCIS), University of Münster.
