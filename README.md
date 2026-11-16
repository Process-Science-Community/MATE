<div align="center">

<img src="landing/assets/mate-icon-black.svg#gh-light-mode-only" alt="" width="64">
<img src="landing/assets/mate-icon-white.svg#gh-dark-mode-only" alt="" width="64">

# MATE

**A modular process science platform.**

MATE is an open-source platform for process mining research and teaching. Analysis methods are packaged as modules, so that a method published in a paper becomes a tool that other researchers can apply to their own event logs.

[![License: MIT](https://img.shields.io/badge/license-MIT-blue.svg)](./LICENSE)
![Python 3.12](https://img.shields.io/badge/python-3.12-3776AB?logo=python&logoColor=white)
![Next.js 15](https://img.shields.io/badge/Next.js-15-000000?logo=nextdotjs&logoColor=white)
![Docker Compose](https://img.shields.io/badge/Docker-Compose-2496ED?logo=docker&logoColor=white)

[Website](https://process-science-community.github.io/MATE/) ·
[User manual](https://process-science-community.github.io/MATE/docs/site/introduction.html) ·
[Hosted instance](https://mate.uni-muenster.de) ·
[Module development](#module-development)

</div>

<picture>
  <source media="(prefers-color-scheme: dark)" srcset="landing/assets/shot-hero-dark.png">
  <img src="landing/assets/shot-hero.png" alt="MATE workspace with a discovered process model">
</picture>

## Contents

1. [Overview](#overview)
2. [Choosing a setup](#choosing-a-setup)
3. [Prerequisites](#prerequisites)
4. [Running MATE locally with Docker](#running-mate-locally-with-docker)
5. [Module development](#module-development)
6. [Production deployment](#production-deployment)
7. [Bundled modules](#bundled-modules)
8. [Configuration](#configuration)
9. [Data and persistence](#data-and-persistence)
10. [Quality checks](#quality-checks)
11. [Repository layout](#repository-layout)
12. [Documentation](#documentation)
13. [Crediting methods](#crediting-methods)
14. [License](#license)

## Overview

MATE is developed at the European Research Center for Information Systems (ERCIS), University of Münster. It provides:

- **Event log management.** Import of XES, XES.gz, CSV and OCEL (`.jsonocel`, `.xmlocel`, OCEL 2.0 SQLite), also inside `.gz`, `.bz2`, `.xz`, `.zip` and tar archives. Logs are stored as Parquet files and queried with DuckDB.
- **Analysis modules.** Discovery, conformance checking, performance, complexity, concept drift detection, simulation and further methods (see [Bundled modules](#bundled-modules)). Each module is a self-contained folder under [`modules/`](./modules/) and is loaded at startup.
- **Dashboards.** Results of different modules can be combined on one canvas.
- **Isolated workspaces.** Event logs, jobs, AI settings and module configuration are scoped to the signed-in user.
- **MATE AI.** An optional chat assistant that uses an LLM provider configured by each user.
- **MCP server.** An optional [Model Context Protocol](https://modelcontextprotocol.io) endpoint for external AI clients, disabled by default (see [`docs/MCP.md`](./docs/MCP.md)).

The platform consists of a FastAPI backend, a Next.js frontend and Keycloak for authentication. All services run under Docker Compose.

## Choosing a setup

| Goal | Setup | Section |
| --- | --- | --- |
| Use MATE without installing anything | Hosted instance at <https://mate.uni-muenster.de> | – |
| Evaluate the full platform on a workstation | Docker stack, `make up` | [Running MATE locally](#running-mate-locally-with-docker) |
| Develop or adapt an analysis module | Native lite stack, `make lite` | [Module development](#module-development) |
| Operate MATE for a research group or institution | Docker stack with the production overlay | [Production deployment](#production-deployment) |

## Prerequisites

| Tool | Required for | Check |
| --- | --- | --- |
| [Git](https://git-scm.com/downloads) | all setups | `git --version` |
| `make` | all setups (optional for Docker, see below) | `make --version` |
| [Docker](https://docs.docker.com/get-started/get-docker/) with Compose v2 | Docker stack, production | `docker compose version` |
| [uv](https://docs.astral.sh/uv/) | module development | `uv --version` |
| [Node.js](https://nodejs.org/) 22 and [pnpm](https://pnpm.io/) | module development | `node --version`, `pnpm --version` |
| JDK 17 or newer | only for JVM modules (`make jvm`) | `java -version` |

The Docker stack needs neither Python nor Node.js on the host. For module development, uv installs the Python version pinned in [`.python-version`](./.python-version) (3.12) automatically.

### macOS

```bash
xcode-select --install                               # Git and make (Apple Command Line Tools)
brew install --cask docker                           # Docker Desktop; start it once afterwards
brew install uv node@22                              # module development only
corepack enable pnpm                                 # module development only
```

### Ubuntu and Debian

```bash
sudo apt update && sudo apt install -y git make
curl -fsSL https://get.docker.com | sh               # Docker Engine and the Compose plugin
sudo usermod -aG docker "$USER"                      # use Docker without sudo; sign in again afterwards
curl -LsSf https://astral.sh/uv/install.sh | sh      # module development only
```

For module development, install Node.js 22 from [nodejs.org](https://nodejs.org/en/download) or with a version manager such as [fnm](https://github.com/Schniz/fnm) (`fnm install 22`), then run `corepack enable pnpm`. For other distributions, follow the [Docker Engine installation guide](https://docs.docker.com/engine/install/).

### Windows

Use Docker Desktop with the WSL 2 backend and run all commands in a WSL 2 Linux shell (for example Ubuntu), following the Ubuntu instructions above:

```powershell
wsl --install                                        # once, then restart
winget install -e --id Docker.DockerDesktop
```

Clone the repository into the WSL file system (for example `~/MATE`) rather than under `C:\`, because bind mounts from the Windows file system are considerably slower. The Windows setup has not been tested by the maintainers.

## Running MATE locally with Docker

### Resource requirements

| Resource | Requirement |
| --- | --- |
| Free disk space | **At least 25 GB** for the first start; about 6 GB can be reclaimed afterwards (see below) |
| Memory | At least 8 GB assigned to Docker |
| Free ports | 3000 (web), 8000 (API), 8080 (Keycloak) |

The disk requirement has three components, measured on a first start from a fresh clone:

1. **Container images.** The API image contains Python 3.12, a Java runtime for JVM modules and the scientific Python stack (pm4py, DuckDB, PyArrow, SciPy and others). Together with the web, PostgreSQL and Keycloak images, the images amount to about 4 GB. Docker Desktop's containerd image store keeps images both compressed and unpacked, which increases the space they occupy on disk considerably.
2. **Build cache.** Building the two images leaves about 6 GB of intermediate layers in Docker's build cache.
3. **Module dependencies.** At first start, the API installs the Python dependencies of every bundled module into `modules/<id>/.venv/` (more than 3 GB; `cv4cdd` alone requires TensorFlow). The package cache used for this step resides inside the API container, so these packages occupy space twice until the container is removed.

Once the stack is running, the build cache can be removed without affecting it:

```bash
docker builder prune -af
```

### Installation

```bash
git clone https://github.com/Process-Science-Community/MATE.git
cd MATE
cp .env.example .env
make up
```

`make up` executes `docker compose up -d --build`. It builds the `api` and `web` images and starts five containers: `api`, `web`, `keycloak`, `keycloak-db` and `app-db`. Without `make`, run `docker compose up -d --build` directly.

The first start takes several minutes, because the API installs all module dependencies before it reports itself as healthy; the health check allows up to ten minutes for this step. Later starts reuse the installed dependencies and complete within seconds. To follow the progress:

```bash
docker compose ps                  # api and web show "(healthy)" when ready
docker compose logs -f api
```

Then open <http://localhost:3000>.

### Signing in

The configuration in `.env.example` sets `DEMO_MODE=true`. In this mode, MATE skips Keycloak and signs every visitor in as a fixed local demo user. The mode is intended for a single user on a workstation.

To use individual accounts, set the following in `.env` and run `make up` again:

```bash
DEMO_MODE=false
AUTH_SECRET=<output of: openssl rand -base64 32>
```

Authentication then runs through the bundled Keycloak:

| Service | Address | Initial credentials |
| --- | --- | --- |
| MATE | <http://localhost:3000> | `admin@flows-funds.local` / `flowsfunds` (a new password is required at first sign-in) |
| Keycloak administration | <http://localhost:8080/admin> | `admin` / `admin` (`KEYCLOAK_ADMIN`, `KEYCLOAK_ADMIN_PASSWORD`) |

The local Keycloak announces itself under the host name `keycloak` (`KC_HOSTNAME` in [`docker-compose.yml`](./docker-compose.yml)). Add `127.0.0.1 keycloak` to `/etc/hosts` so that the browser can resolve it.

Further users are created in the Keycloak administration console, realm `flows-funds`. The administration pages of MATE (`/admin`) require the realm role `admin`, which is assigned under *Realm roles → admin → Users in role*. The seeded user `admin@flows-funds.local` does not hold this role initially.

### Stopping and updating

```bash
make down                          # stop; data in ./data and the Docker volumes is kept
git pull && make up                # update to the latest version
```

## Module development

Modules are developed with the **lite stack**: the API and the web frontend run natively with hot reload, without Docker and without a `.env` file. Only the modules named in `MODULES` are loaded.

### Resource requirements

Measured with a fresh clone and empty package caches on an Apple Silicon Mac:

| Resource | Requirement |
| --- | --- |
| Disk space | About 2.5 GB: repository (66 MB), Python environment (0.6 GB), Node.js packages (0.6 GB) and the package caches of uv and pnpm (1.2 GB). Modules with large dependencies add to this, for example `cv4cdd` more than 1.5 GB. |
| Memory | About 1.5 GB with one module loaded (API, module bundler and Next.js development server) |
| Time | `make install` about 20 seconds on a fast connection; `make lite` ready after about 15 seconds |
| Free ports | 3000 (web), 8000 (API), 5678 (Python debugger) |

### Step by step

1. **Install the dependencies** (once per clone):

   ```bash
   git clone https://github.com/Process-Science-Community/MATE.git
   cd MATE
   make install                    # uv sync --extra dev and pnpm install
   ```

2. **Create a module** (skip this step to work on an existing module):

   ```bash
   make new ID=my_module           # creates modules/my_module/
   make new ID=my_module PANEL=1   # alternatively: with a custom React panel
   ```

   The scaffold contains `manifest.yaml` (metadata and dependencies), `module.py` (the analysis), `layout.json` (the module page), `HOW_TO_USE.md` (user documentation) and `tests/`.

3. **Start the lite stack:**

   ```bash
   make lite MODULES=my_module
   ```

   The stack signs you in as a demo user with administrator rights and imports a sample event log (120 cases, about 1,000 events) at first start. `Ctrl-C` stops all processes.

4. **Design the module page.** Open <http://localhost:3000/dev> and select **Open Studio**. Text, controls, run buttons, charts, tables and tabs are placed visually and saved to `modules/my_module/layout.json`.

5. **Implement the analysis** in `modules/my_module/module.py`. The module is reloaded on every save.

6. **Debug.** In VS Code, run the launch configuration **Attach: MATE API (lite)**, which connects to port 5678.

7. **Test and validate** before publishing:

   ```bash
   make test ID=my_module          # the module's tests
   make validate                   # manifest, HOW_TO_USE.md and layout.json of all modules
   ```

8. **Publish.** Commit the folder under `modules/` and open a pull request, or package it as a ZIP file and upload it in MATE under **Modules → Install a module** (`/modules/import`).

### Command reference

| Command | Purpose |
| --- | --- |
| `make install` | Install Python and Node.js dependencies |
| `make new ID=x [PANEL=1]` | Create a module scaffold |
| `make widget ID=x NAME=y` | Add a custom TSX widget to a module |
| `make lite MODULES=a,b` | Start the lite stack with the listed modules; without `MODULES`, all modules are loaded |
| `make lite PORT=8001 WEB_PORT=3001` | Use other ports, for example alongside a running Docker stack |
| `make modules` | List all modules with version and runtime |
| `make test ID=x` | Run the tests of one module |
| `make validate` | Validate all modules |
| `make reset` | Delete the data of the lite stack (`data/lite/`) |

Further reading:

| Document | Contents |
| --- | --- |
| [`docs/DEV_LITE.md`](./docs/DEV_LITE.md) | Lite stack in detail, Studio card types, debugging per execution mode, troubleshooting |
| [`modules/README.md`](./modules/README.md) | Module contract: manifest, `module.py`, context API, frontend, dependencies |
| [`modules/PROTOCOL.md`](./modules/PROTOCOL.md) | Wire protocol for subprocess and JVM modules |
| [`modules/SIDECAR_SERVICES.md`](./modules/SIDECAR_SERVICES.md) | Modules that require an additional service such as Neo4j |

## Production deployment

A production instance adds the overlay [`docker-compose.prod.yml`](./docker-compose.prod.yml) to the base stack. The overlay adds a Caddy reverse proxy that terminates TLS and serves the web frontend, the API (`/api/v1`) and Keycloak (`/auth`) from a single origin; the individual service ports are no longer published. The complete runbook, including verification steps and updates, is [`docs/DEPLOY.md`](./docs/DEPLOY.md).

> [!IMPORTANT]
> The overlay and the Caddy configuration are written for the instance at `mate.uni-muenster.de`. Other institutions have to replace this host name in [`docker-compose.prod.yml`](./docker-compose.prod.yml) and [`infra/caddy/Caddyfile`](./infra/caddy/Caddyfile).

### Requirements

- A Linux server with Docker Engine and Compose v2.24 or newer.
- A DNS name and a TLS certificate whose `fullchain.pem` and `privkey.pem` are readable by Docker.
- Resources as for the [local Docker stack](#resource-requirements), plus storage for event logs. Event logs can alternatively be kept in S3-compatible object storage ([`docs/S3_OFFLOAD.md`](./docs/S3_OFFLOAD.md)).

### Setup

```bash
git clone https://github.com/Process-Science-Community/MATE.git ~/mate
cd ~/mate
sudo apt install -y jq
PUBLIC_URL=https://mate.example.org ./infra/bootstrap-vm.sh --no-start
```

[`infra/bootstrap-vm.sh`](./infra/bootstrap-vm.sh) generates a `.env` file with random values for `AUTH_SECRET`, `KEYCLOAK_CLIENT_SECRET`, `KEYCLOAK_DB_PASSWORD` and `KEYCLOAK_ADMIN_PASSWORD`, and registers `PUBLIC_URL` in the Keycloak realm. Add the following to `.env`:

```bash
TLS_CERT_DIR=/etc/letsencrypt/live/<host name>   # required by the overlay
APP_DB_PASSWORD=<random value>                   # otherwise the default "mate" is used
```

Then start the stack:

```bash
docker compose -f docker-compose.yml -f docker-compose.prod.yml up -d --build
```

### Checklist before going live

| Item | Action |
| --- | --- |
| Demo login | `DEMO_MODE` must not be `true`. The bootstrap script does not set it, and the compose default is `false`. |
| Secrets | All values generated by the bootstrap script; `APP_DB_PASSWORD` set |
| Administrator | Assign the realm role `admin` to at least one account in Keycloak |
| Institutional login | The realm contains a placeholder identity provider `keycloak-oidc`. Configure it ([`docs/DEPLOY.md`](./docs/DEPLOY.md), "University login") or set `KEYCLOAK_IDP_HINT=` (empty) to use Keycloak's own login form |
| Usage tracking | The default `USER_TRACKING_ONBOARDING=force` records usage for every user without an opt-out. Choose `on` or `off` according to your data protection requirements |
| `cv4cdd` | Requires a detection model, which MATE does not ship. Upload one on the module's settings page or pin one for all users under **Admin → Controls**, or disable the module |
| `actor_performance` | Requires the Neo4j service: set `COMPOSE_PROFILES=graph` and `NEO4J_PASSWORD` in `.env` |
| MCP server | Remains disabled unless `MCP_ENABLED` is set; see [`docs/DEPLOY.md`](./docs/DEPLOY.md), "MCP server" |
| Backups | Back up `./data/` and the PostgreSQL database (see [Data and persistence](#data-and-persistence)) |

## Bundled modules

All modules under [`modules/`](./modules/) are discovered at startup. They are enabled, disabled and configured on the **Modules** page (`/modules`); `make modules` lists them.

| Module | Method |
| --- | --- |
| `discovery` | Process discovery: directly-follows graph, Petri net (Alpha or Inductive Miner), process tree, heuristics net, BPMN |
| `ocel_discovery` | Object-centric discovery: object types, activities and the object-centric directly-follows graph |
| `conformance` | Conformance checking against a BPMN model: fitness, precision and deviations per activity |
| `performance` | Throughput, cycle and lead times, 90th-percentile cycle time, bottlenecks |
| `performance_java` | Cycle time, throughput, longest waiting activities and slowest hand-offs (JVM module) |
| `performance_over_time` | Performance indicators per time slice |
| `actor_performance` | Decomposition of waiting times by actor behaviour, based on event knowledge graphs (requires Neo4j) |
| `complexity` | Variant and sequence entropy, Lempel-Ziv complexity, affinity, structure, Pentland's task and process complexity |
| `complexity_v2` | 28 complexity measures (entropy, size, variation, trace distance) and the transition-probability matrix |
| `complexity_over_time`, `complexity_v2_over_time` | The two complexity suites per time slice |
| `log_evolution` | Case arrivals and completions, work in progress, activity mix over time, dotted chart |
| `temporal_mapper` | Recurring activity patterns across calendar periods |
| `process_comparison` | Comparison of two filtered views: directly-follows graph difference, variants, similarity measures |
| `pcomp` | Statistical test for differences between two event logs (Earth Mover's Distance, permutation or bootstrap test) |
| `cv4cdd` | Concept drift detection (sudden, gradual, incremental, recurring) with a computer vision model; requires a detection model |
| `concept_drift_explainer` | Explanations for detected drifts based on uploaded documents; sends data to an external LLM provider |
| `agentsimulator` | Agent-based simulation that learns from an event log and generates synthetic logs |

## Configuration

All settings are read from the root `.env` file. [`.env.example`](./.env.example) documents every variable; the most relevant are:

| Variable | Default | Purpose |
| --- | --- | --- |
| `DEMO_MODE` | `true` in `.env.example` | Sign in as a local demo user without Keycloak. Local use only |
| `DEMO_ADMIN` | `false` | Grant the demo user the `admin` role |
| `AUTH_SECRET` | empty | Encryption key of the session cookie. Required when `DEMO_MODE=false`; generate with `openssl rand -base64 32` |
| `NEXT_PUBLIC_API_URL` | `http://localhost:8000` | API address used by the browser. Compiled into the web image; run `make build` after a change |
| `USER_TRACKING_ONBOARDING` | `force` | Usage tracking: `force` (always on, no opt-out), `on` (opt-out), `off` (opt-in) |
| `STORAGE_MODE` | `local` | `local` stores event logs under `data/`; `s3` uses an S3-compatible bucket ([`docs/S3_OFFLOAD.md`](./docs/S3_OFFLOAD.md)) |
| `MCP_ENABLED` | `false` | Enables the MCP endpoint at `/mcp` ([`docs/MCP.md`](./docs/MCP.md)) |
| `KEYCLOAK_CLIENT_SECRET` | development value | Secret of the `flows-funds-web` client; must match the realm configuration |
| `KEYCLOAK_ADMIN`, `KEYCLOAK_ADMIN_PASSWORD` | `admin`, `admin` | Credentials of the Keycloak administration console |

### MATE AI

The chat assistant uses an LLM provider that each user configures under **Settings → AI**. Provider, model and API key are stored per user in the metadata database. Requests are sent only to the configured provider.

### Usage tracking

Usage events are stored in the metadata database of the instance; no external analytics service is involved. With `USER_TRACKING_ONBOARDING=force`, tracking applies to every user and the **Settings → Privacy** tab is hidden. Accounts holding a role listed in `USER_TRACKING_EXEMPT_ROLES` (default `reviewer`) are never tracked.

## Data and persistence

| Data | Docker stack | Lite stack (`make lite`) |
| --- | --- | --- |
| Metadata (users, jobs, settings) | PostgreSQL 16, volume `app-db-data` | SQLite, `data/lite/metadata.db` |
| Event logs (Parquet) and module results | `data/users/<user id>/` | `data/lite/users/<user id>/` |
| Keycloak users and realm | PostgreSQL 16, volume `kc-data` | not used |
| Module dependencies | `modules/<id>/.venv/` | `modules/<id>/.venv/` |

- `./data/` and `./modules/` are mounted into the containers. A complete backup of the Docker stack consists of `./data/` and a dump of the `app-db` database. Administrators can download such a dump under **Admin → Export** (a `pg_dump` file on PostgreSQL, a copy of the database file on SQLite).
- Modules uploaded through the user interface are stored in `data/uploaded_modules/<id>/`, separate from the bundled modules in `modules/`. Each user receives the bundled modules at first sign-in; **Restore defaults** on the Modules page adds removed modules again.
- Event logs and results are stored per user, keyed by the Keycloak user ID; the API delivers jobs and live updates only to their owner.

## Quality checks

```bash
make install       # once
make test          # Python test suite
make typecheck     # TypeScript type check of the web frontend
make validate      # validation of all modules
```

`make` without arguments lists all available targets.

## Repository layout

```
MATE/
├── apps/
│   ├── api/                 # FastAPI backend (Python 3.12)
│   └── web/                 # Next.js 15 frontend
├── modules/                 # Bundled analysis modules, one folder each
├── packages/
│   ├── module-sdk-py/       # Python SDK for module authors
│   ├── module-sdk-ts/       # TypeScript SDK for module frontends
│   ├── module-sdk-jvm/      # SDK for JVM modules
│   └── shared-types/        # TypeScript types generated from the OpenAPI schema
├── infra/                   # Keycloak realm and theme, Caddy, setup scripts
├── landing/                 # Website and user manual (GitHub Pages)
├── docs/                    # Design specification, deployment, MCP reference
├── scripts/                 # Helper scripts used by the Makefile
├── docker-compose.yml       # Base stack
├── compose.dev.yml          # Development overlay with hot reload (make up DEV=1)
└── docker-compose.prod.yml  # Production overlay (Caddy, TLS)
```

For work on the platform itself, `make up DEV=1` runs the Docker stack with hot reload, and `make dev` runs the API and the web frontend natively with the configuration in `.env`.

## Documentation

| Document | Contents |
| --- | --- |
| [User manual](https://process-science-community.github.io/MATE/docs/site/introduction.html) | Working with MATE: import, analyses, dashboards. Source in [`landing/docs/`](./landing/docs/), rebuilt with `make docs` |
| [`docs/README.md`](./docs/README.md) | Index of all documentation in this repository |
| [`docs/INSTRUCTIONS.md`](./docs/INSTRUCTIONS.md) | Design specification and architectural rationale |
| [`docs/DEV_LITE.md`](./docs/DEV_LITE.md) | Module development with the lite stack |
| [`docs/DEPLOY.md`](./docs/DEPLOY.md) | Production deployment runbook |
| [`docs/MCP.md`](./docs/MCP.md) | MCP server for AI clients |
| [`modules/README.md`](./modules/README.md) | Module authoring contract |

## Crediting methods

Each module cites the publications it implements in the `source` field of its manifest, and MATE displays these citations in the module's information popover. When publishing results obtained with a module, please cite the publications listed there.

## License

[MIT](./LICENSE) © 2026 European Research Center for Information Systems (ERCIS), University of Münster.
