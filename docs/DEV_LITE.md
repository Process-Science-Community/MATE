# Lite dev mode

Run the whole platform natively (no Docker), load only the modules you work on,
and debug module code with breakpoints, full tracebacks, live logs and hot reload.
`make lite` is the inner loop for module authors; `make dev` and `make up DEV=1`
still exist for platform work against your own `.env`.

## Quick start

```bash
make install                 # uv sync --extra dev + pnpm install (once)
make new ID=my_module        # optional: scaffold a module to work on
make lite MODULES=my_module  # API + bundler + web; Ctrl-C stops all three
```

Open <http://localhost:3000>. You are signed in as the demo user (with admin),
the first-run wizard is skipped, and a sample event log (`scripts/lite/sample_log.csv`,
an order process with 120 cases and about 1,000 events, enough for every
case-centric module's minimum; object-centric modules need an OCEL upload) is imported on the first start. Leave out
`MODULES=` to load every module.

`make lite` needs no `.env`. It sets everything it needs itself (see
`scripts/lite.sh --print-env`), keeps its data under `data/lite/`, and never
kills other processes. If a port is taken it stops with a message instead.

| Make variable | Default | Meaning |
| --- | --- | --- |
| `MODULES` | empty (all) | Comma-separated module ids to load. Hard dependencies (`requirements.modules`) are added automatically. |
| `PORT` | `8000` | API port |
| `WEB_PORT` | `3000` | Web port |

Three env vars are read directly by the script: `DEBUGPY_PORT` (default `5678`,
`0` turns the API debugger off), `DEV_FORCE_THREAD` (default `true`) and
`DEBUGPY_WORKERS=true` (see [Debugging](#debugging)). A second stack on the same machine needs other ports,
for example `DEBUGPY_PORT=5679 make lite PORT=8001 WEB_PORT=3001`, and its own
checkout or worktree, because `data/lite/` sits inside the checkout.

Other targets:

| Command | What it does |
| --- | --- |
| `make reset` | Delete `data/lite/` (DB, logs, results). Nothing else is touched. |
| `make new ID=x [PANEL=1]` | Scaffold `modules/x/` (`mate-module new`) |
| `make test ID=x` | Run `modules/x/tests` (`mate-module test`) |
| `make modules` | List every module: id, version, name, runtime (isolation/execution or jvm), UI (panel, layout, widgets, datasets), folder (`mate-module list`) |
| `make validate` | Check every module's manifest, `HOW_TO_USE.md` and `layout.json` (`mate-module validate`) |

## Why no Docker

- On macOS, Docker Desktop runs a Linux VM that holds several GB of RAM whatever
  the containers use.
- The compose stack starts Keycloak and two Postgres instances before the API
  even if you only need the demo login.
- File watching across a bind mount is slower and less reliable, and that is the
  hot reload you depend on while writing a module.
- Attaching a debugger to a process inside a container needs port mapping and
  path mappings. Natively it is `localhost:5678` with the real file paths.

The platform already runs without any of that: SQLite for metadata, local
storage for logs and results, and `DEMO_MODE` instead of Keycloak.

## What lite mode sets

`scripts/lite.sh --print-env` prints the resolved environment without starting
anything. The values that matter:

| Variable | Value | Why |
| --- | --- | --- |
| `ENV` | `dev` | Turns on module hot reload and the dev tools (`/api/v1/dev`, Studio) |
| `DEMO_MODE`, `DEMO_ADMIN` | `true` | Demo login with admin rights, no Keycloak |
| `DATA_DIR`, `DATABASE_URL` | `data/lite/`, `data/lite/metadata.db` | Isolated from `make dev` data |
| `STORAGE_MODE`, `MCP_ENABLED` | `local`, `false` | Nothing leaves the machine |
| `MODULES_ONLY` | `$MODULES` | Module allowlist (applies to `modules/` and uploads) |
| `DEV_FORCE_THREAD` | `true` | `execution: worker` jobs run in the API process so breakpoints bind |
| `DEBUGPY_PORT` | `5678` | The API listens for a debugger here; it never waits for one |
| `CORS_ORIGINS`, `AUTH_URL`, `NEXT_PUBLIC_API_URL`, `INTERNAL_API_URL` | derived from the ports | Web and API find each other on non-default ports |
| `AUTH_SECRET` | your value, else a local-only fallback | Auth.js cookie key |

## Memory levers

None of these are measured yet; the order is the expected impact.

1. **`MODULES=`**. Every loaded `in_process` module imports its dependencies into
   the API process, every `isolation: subprocess` module keeps a worker running,
   and the JVM module keeps a JVM. Loading two modules instead of eighteen is the
   biggest saving.
2. **Job and offload slots**: `WORKER_CONCURRENCY=1`, `MODULE_PROCESS_POOL_SIZE=1`,
   `MAX_OFFLOADS_PER_USER=1`. One job and one `ctx.run_in_process` child at a time.
3. **DuckDB**: `DUCKDB_THREADS=2`, `DUCKDB_MEMORY_LIMIT=1GB`.
4. **Event log cache**: `EVENT_LOG_CACHE_ENTRIES=1` keeps one materialised frame.
5. **`MALLOC_ARENA_MAX=2`**: caps glibc malloc arenas on Linux; no effect on macOS.

Precompute is lazy, so the sample log only starts modules that are eager or
that you open.

## Working on several modules

Keep every module you work on under `modules/` and list them:

```bash
make lite MODULES=my_module,other_module
```

- Each module hot-reloads on its own. Saving a file under `modules/<id>/` reloads
  that module only (`ENV=dev`). `layout.json` is ignored by the watcher, so a
  Studio save does not restart a worker.
- A module folder you create while the stack runs is loaded, seeded for the demo
  user and shown in the module list without a restart, as long as it is in
  `MODULES` (or `MODULES` is empty). The panel bundler picks up new folders too.
- Platform code (`apps/api/src`, `packages/module-sdk-py/src`) restarts the API
  through uvicorn `--reload`. That also drops an attached debugger; attach again.
- In dev, the module version used for result staleness includes a fingerprint
  of the module's code. Editing the code marks earlier results stale, so they
  recompute the next time you open them.

## Creating a module: new, run, build UI, test, validate, ship

1. **New.** `make new ID=my_module` writes `modules/my_module/` with a
   `manifest.yaml` (`in_process`, inherits pandas and duckdb, one `summary` table
   dataset with a `top` param), a `module.py` (route `GET /summary?top=` with
   activity counts plus a `POST /recount` `@job` example that caches them), a
   `HOW_TO_USE.md` that passes the guide check, a `layout.json` that shows every
   card kind (an intro text, a `top` slider, a **Recount** button and the
   `summary` table), and `tests/test_my_module.py` (named after the id so several
   modules' tests can run together). `PANEL=1` adds
   `panel/index.tsx` and sets `frontend.panel`. It refuses an existing folder and
   ids that are not snake_case. A new folder under `modules/` also needs a manual
   page: run `make docs` (and commit `landing/docs/site/`) before
   `apps/api/tests/test_module_guides.py` passes again.
2. **Run.** `make lite MODULES=my_module`, open the sample log, then the module.
   The module page renders `layout.json` (with the log filter bar). With a panel
   too, the layout's tabs come first and the panel is the last tab, **Panel**.
3. **Build the UI.** Open **Dev** in the sidebar (`/dev`): it lists the loaded
   modules with load errors, datasets, widgets and debug info. **Studio**
   (`/dev/studio/<id>`) edits the module's page as it will look (see
   [Studio](#studio-building-a-page-without-tsx)). **Save** writes
   `modules/<id>/layout.json`; **Reload module** reloads it like hot reload does.
   Write a `panel/` instead when the page needs custom interaction; the layout is
   the no-code alternative.
4. **Test.** `make test ID=my_module`. Tests use `mate.sdk.testing`
   (`sample_log`, `build_test_context`, `load_module`, see
   [modules/README.md §10](../modules/README.md#10-testing)). To debug a test,
   open a file of the module in VS Code and run **Python: module tests**.
5. **Validate.** `make validate` checks the manifest, the guide and
   `layout.json` (rules under [Validation](#validation)). Exit code 1 on any
   problem.
6. **Ship.** Either commit the folder (it becomes a bundled default for everyone)
   or zip it and upload it under **Modules → Install a module** (`/modules/import`) (see
   [modules/README.md §11](../modules/README.md#11-distribution)).

## Studio: building a page without TSX

`layout.json` is a 12-column grid of cards, the same grid dashboards use. The
Studio draws it inside the real module page (filter bar, tabs, page padding), so
what you edit is what the module page shows.

- **Edit** shows the palette and inspector as overlays. The grid is laid out at
  the module page's width and scaled down to fit, so it stays editable on small
  windows; the toolbar shows the zoom.
- **Preview** (`?mode=preview`) renders the unsaved draft exactly as the module
  page does. The **Edit** pill goes back.
- Clicking an **action** card runs the real route on the selected log.

### Card kinds

Every card has `i`, `kind`, `x`, `y`, `w`, `h`, optional `title`, `config`, `tab`.

| Kind | What it shows | Shape |
|---|---|---|
| `viz` | a generic visualization of a manifest dataset | `"dataset_ref": {"module_id": "m", "dataset_id": "summary"}, "viz_id": "table"` |
| `widget` | one of `frontend.widgets` (TSX) | `"module_id": "m", "widget_id": "kpi"` |
| `text` | markdown; frameless on the page | `"config": {"markdown": "## Heading\nIntro."}` |
| `controls` | a form over page params or the module config | `"module_id": "m", "config": {"source": "params", "keys": ["top"]}` |
| `action` | a button that POSTs a module route | `"module_id": "m", "config": {"route": "/recount", "label": "Recount"}` |

`text`, `controls` and `action` exist only in `layout.json`; dashboards never
offer them. A viz card's chart and field mapping are set in the inspector.

Table viz: click a column header to sort. "Show as duration" in the viz
settings shows seconds as 1d 2h / 3h 4m for table columns and for KPI grid,
number and gauge measures; a KPI with `format: duration` gets this
automatically.

### Tabs

```json
{"version": 1,
 "tabs": [{"id": "overview", "title": "Overview"}, {"id": "details", "title": "Details"}],
 "items": [{"i": "a", "kind": "text", "x": 0, "y": 0, "w": 12, "h": 3,
            "config": {"markdown": "Details"}, "tab": "details"}]}
```

A card without `tab` belongs to the first tab. Without `tabs`, no card may set
`tab`. Tab ids are lowercase slugs. In the Studio, **Page** adds, renames and
deletes tabs; deleting one moves its cards to the first remaining tab.

### Params: controls -> datasets

1. Declare the params on the dataset (same dialect as `config_schema`;
   `ui.widget` picks the control):

   ```yaml
   datasets:
     - id: summary
       route: /summary
       params_schema:
         properties:
           top: {type: integer, title: Top activities, default: 10, minimum: 1, maximum: 50, ui: {widget: slider}}
   ```

2. Accept them as handler arguments: `async def summary(self, ctx, top: int = 10)`.
   An array param (a `multiselect`) must be marked as a query param, or FastAPI
   reads it from the body and the handler gets `None`:
   `activities: Annotated[list[str] | None, Query()] = None`.
3. Add a `controls` card with `source: "params"` (`keys: []` means every param
   of every dataset of the module).

Param values live on the page, shared by all tabs, and are not saved in the URL.
Only values the user changed are sent; otherwise the route's Python default
applies. Each viz card sends only the params its own dataset declares.
`source: "config"` edits the module config (`config_schema`) instead and saves
it with a **Save** button.

### Actions and jobs

An `action` card POSTs `/api/v1/modules/<id><route>?log_id=...&<page params>`.
A `@job` route returns a `job_id`; the card shows its progress and, when it
finishes, refetches the module's cards. A plain POST route refetches right away.
Handler arguments named like a page param receive its value; other params are
ignored.

### Validation

Checked by `mate.sdk.layout` (load, Studio save) and `make validate`:

- `version` is 1; unknown top-level keys are an error. Card ids and tab ids are
  unique; every `tab` names a declared tab.
- `viz` needs `dataset_ref` and `viz_id`; `widget` needs `widget_id`; `text`
  needs `config.markdown`; `controls` and `action` need `module_id`.
- `controls`: `source` is `params` or `config`; each key is declared in a
  dataset's `params_schema` (or `config_schema`), and there is something to
  control at all.
- `action`: `route` starts with `/` and has no `?`. `validate` checks that the
  route string appears in the module's `.py` files; the Studio save (422)
  checks that the loaded module has that POST route.
- Cards stay inside the 12 columns and only point at this module's own data.

Not yet: horizontal bar charts, reference lines on charts, BPMN/Petri net
visualizations and datasets taken from a route outside the manifest.

### E2E check

`make e2e` scaffolds a throwaway `modules/e2e_studio`, starts the lite
stack on ports 8777/3777 with a temporary data dir (your `data/lite` is not
touched), checks with Playwright that the Studio's cards sit exactly where the
module page puts them (1600x1000 and 1280x800), then stops the stack and
deletes the folder and data dir. It downloads no browser itself.
`@playwright/test` is pinned to 1.58.2 (Chromium r1208); if Playwright says no
browser is available on your machine, run once
`pnpm --filter web exec playwright install chromium`.

## Debugging

### Logs and tracebacks

- The terminal running `make lite` shows API logs (structlog), bundler output
  and Next.js output. `LOG_LEVEL=debug make lite` is more verbose.
- `ctx.logger` lines go to the API log, to the job's log, and live to the
  **Logs** tail on the module's detail page (`/modules/<id>`).
- A handler that raises in a worker (`execution: worker`, `isolation: subprocess`)
  logs the full traceback as `modules.worker.handler_error` and also sends it to
  the job log and the module's live log tail (`module.log.error`).
- `print()` from an `in_process` module goes to the API's stdout. From a worker
  it is captured as `modules.subprocess.worker_output` / `modules.job_worker.output`.

### Breakpoints, by execution mode

Install the VS Code **Python Debugger** extension. `.vscode/launch.json` ships
three configs.

| Module runs as | How to break in it |
| --- | --- |
| `in_process`, `execution: thread` (default) | Run **Attach: MATE API (lite)** (`127.0.0.1:5678`). Breakpoints in `modules/<id>/*.py` bind directly. |
| `in_process`, `execution: worker` | Same attach. Lite sets `DEV_FORCE_THREAD=true`, so jobs run in the API process instead of a child. Start with `DEV_FORCE_THREAD=false make lite` to test the real worker path. Each job then runs in a short-lived child on a new port, so the per-port attach in the next row only catches jobs that keep running for several seconds; keep the default for breakpoints. |
| `isolation: subprocess` (and worker jobs with `DEV_FORCE_THREAD=false`) | Start with `DEBUGPY_WORKERS=true make lite`. Each worker logs `MATE_DEBUGPY module=<id> pid=<pid> port=<port>`; run **Attach: module worker (pick port)** and enter the port. If it logs `attached-via-api` instead, the API debugger (`DEBUGPY_PORT`) already hooked that worker in, so use **Attach: MATE API (lite)**. A subprocess worker runs on the module's own venv, so debugpy must be installed there (add `debugpy` to `dependencies.python.packages` while debugging, and remove it before shipping). |
| JVM (`runtime.kind: jvm`) | Not wired up. Untested option: start with `JAVA_TOOL_OPTIONS=-agentlib:jdwp=transport=dt_socket,server=y,suspend=n,address=127.0.0.1:5005` and attach a Java debugger. With several JVM workers the port clashes. |
| Module tests | **Python: module tests** runs pytest on the folder of the open file under the debugger. |

While you sit on a breakpoint inside a job, the job's wall clock keeps running
(`JOB_EXECUTION_TIMEOUT_SECONDS`, default 1800; `0` turns it off), and a cancel
from the UI hard-kills a worker after 3 seconds.

The API calls `debugpy.listen` once at startup and never waits, so the stack
runs the same with or without a debugger attached. If debugpy is missing it logs
a warning and carries on (`make install` installs it with the dev extra).

## Troubleshooting

| Symptom | Fix |
| --- | --- |
| `port 8000 (PORT) is already in use` | Another stack runs. Stop it, or use `make lite PORT=8001 WEB_PORT=3001` with `DEBUGPY_PORT=5679`. Lite never kills it for you. |
| `apps/web/node_modules missing` | `make install` |
| My module is not in the list | Check `MODULES=` (typo, or not listed). Open `/dev` to see load errors. Folders starting with `_` or `.` are skipped. |
| An edit has no effect | Check the terminal for a reload error. Changing a route's query parameters needs an API restart. A change in `pyproject.toml` is not watched; declare dependencies in `manifest.yaml`. |
| Old results after a code change | They should go stale in dev. If not, press **Reload module** in Studio, or `make reset`. |
| The panel shows old code | Refocus the browser window; in dev the panel bundle is checked again. Check the bundler output for build errors. |
| Debugger does not connect | Is the banner showing the debugger port, and is `DEBUGPY_PORT` not `0`? After a platform code edit uvicorn restarts the API: attach again. |
| First start hangs on "waiting for the API" | Modules with their own packages build a venv on first load; that can take minutes. Use `MODULES=` to load fewer. |
| Start from scratch | `make reset` (only `data/lite/`) |
