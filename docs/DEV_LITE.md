# Lite dev mode

Run the whole platform natively (no Docker), load only the modules you work on,
and debug module code with breakpoints, full tracebacks, live logs and hot reload.
`make lite` is the inner loop for module authors; `make dev` and `make up-dev`
still exist for platform work against your own `.env`.

## Quick start

```bash
make install                     # uv sync --extra dev + pnpm install (once)
make module-new ID=my_module     # optional: scaffold a module to work on
make lite MODULES=my_module      # API + bundler + web; Ctrl-C stops all three
```

Open <http://localhost:3000>. You are signed in as the demo user (with admin),
and a small sample event log is imported on the first start. Leave out
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
| `make lite-reset` | Delete `data/lite/` (DB, logs, results). Nothing else is touched. |
| `make module-new ID=x [PANEL=1]` | Scaffold `modules/x/` (`mate-module new`) |
| `make module-test ID=x` | Run `modules/x/tests` (`mate-module test`) |
| `make module-validate` | Check every module's manifest, `HOW_TO_USE.md` and `layout.json` (`mate-module validate`) |

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

1. **New.** `make module-new ID=my_module` writes `modules/my_module/` with a
   `manifest.yaml` (`in_process`, inherits pandas and duckdb, one `summary` table
   dataset), a `module.py` (route `GET /summary` with activity counts plus one
   `@job`), a `HOW_TO_USE.md` that passes the guide check, a `layout.json` with
   one table bound to `summary`, and `tests/test_my_module.py` (named after the
   id so several modules' tests can run together). `PANEL=1` adds
   `panel/index.tsx` and sets `frontend.panel`. It refuses an existing folder and
   ids that are not snake_case. A new folder under `modules/` also needs a manual
   page: run `make docs` (and commit `landing/docs/site/`) before
   `apps/api/tests/test_module_guides.py` passes again.
2. **Run.** `make lite MODULES=my_module`, open the sample log, then the module.
   Without a panel, the module page renders `layout.json` read-only. With both,
   you get **Panel** and **Layout** tabs.
3. **Build the UI.** Open **Dev** in the sidebar (`/dev`): it lists the loaded
   modules with load errors, datasets, widgets and debug info. **Studio**
   (`/dev/studio/<id>`) edits the module's layout on the dashboard canvas, with a
   palette limited to that module's datasets and widgets. **Save** writes
   `modules/<id>/layout.json`; **Reload module** reloads it like hot reload does.
   Write a `panel/` instead when the page needs custom interaction; the layout is
   the no-code alternative.
4. **Test.** `make module-test ID=my_module`. Tests use `mate.sdk.testing`
   (`sample_log`, `build_test_context`, `load_module`, see
   [modules/README.md §10](../modules/README.md#10-testing)). To debug a test,
   open a file of the module in VS Code and run **Python: module tests**.
5. **Validate.** `make module-validate` checks the manifest, the guide, and that
   every card in `layout.json` points at a dataset or widget the manifest declares.
   Exit code 1 on any problem.
6. **Ship.** Either commit the folder (it becomes a bundled default for everyone)
   or zip it and upload it under **Modules → Install a module** (`/modules/import`) (see
   [modules/README.md §11](../modules/README.md#11-distribution)).

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
| Old results after a code change | They should go stale in dev. If not, press **Reload module** in Studio, or `make lite-reset`. |
| The panel shows old code | Refocus the browser window; in dev the panel bundle is checked again. Check the bundler output for build errors. |
| Debugger does not connect | Is the banner showing the debugger port, and is `DEBUGPY_PORT` not `0`? After a platform code edit uvicorn restarts the API: attach again. |
| First start hangs on "waiting for the API" | Modules with their own packages build a venv on first load; that can take minutes. Use `MODULES=` to load fewer. |
| Start from scratch | `make lite-reset` (only `data/lite/`) |
