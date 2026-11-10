# Authoring a Mate Module

This is the practical guide for building a module. For the platform-level rationale (why Parquet, why DuckDB, why per-module venvs) read [`/docs/INSTRUCTIONS.md` §5](../docs/INSTRUCTIONS.md). This document is the contract: what to put on disk, what the platform calls, what your module is allowed to call back.

---

## 1. What a module is

A module is a self-contained folder under `modules/<folder>/`. The platform discovers it on startup, installs its declared dependencies into the folder itself, registers its routes / event handlers / jobs, and renders its frontend panel on the per-process page (`/processes/{logId}/modules/{moduleId}`).

A module is **not**:

- A patch to the platform – you never edit `apps/api` or `apps/web` to ship a module.
- A long-running service – the platform owns the FastAPI process, the asyncio event loop, the job queue, and the WebSocket fan-out. Your code runs inside them.
- A privileged citizen – the same SDK is used by core, third-party, and user modules. There are no internal hooks reserved for first-party code.

To remove a module: delete the folder. Everything it added (its venv, its bundled JS, its lockfile) lives inside the folder.

---

## 2. Folder layout

```
modules/<folder>/
├── manifest.yaml           # required – registration, requirements, deps, frontend
├── module.py               # required – entry point: subclass of Module
├── HOW_TO_USE.md           # required – the user guide (see "The how-to-use guide" below)
├── README.md               # optional – developer notes: provenance, method spec, setup
├── pyproject.toml          # optional – synthesised from manifest if absent
├── package.json            # optional – synthesised from manifest if absent
├── events.py               # recommended – Pydantic schemas for emitted events
├── tests/                  # pytest tests for your handlers
├── panel/
│   └── index.tsx           # frontend module page entry (see manifest.frontend.panel)
├── widgets/
│   └── *.tsx               # reusable widgets advertised in manifest.frontend.widgets
├── .venv/                  # auto-created; gitignored
├── .dist/                  # auto-bundled JS; gitignored
├── node_modules/           # gitignored
└── uv.lock                 # committed – pins your Python deps
```

`<folder>` is arbitrary on disk; the manifest's `id` is authoritative.

### The how-to-use guide

`HOW_TO_USE.md` is **required**. It is the module's user guide, written for an analyst using the app rather than for a developer, and one file serves two places: the **How to use** button on the module page (a panel that stays beside the page) and the *Module guides* section of the published manual. `README.md` stays free for developer and provenance notes. Copy [`HOW_TO_USE.template.md`](HOW_TO_USE.template.md) and fill it in; [`temporal_mapper/HOW_TO_USE.md`](temporal_mapper/HOW_TO_USE.md) is a complete example.

```text
# How to use <manifest name>

<Lead: one paragraph, 1-3 sentences – the question the module answers.>

## When to use it
## Before you start
## Settings
## Reading the results
## Tips and limits
## Method
```

| Part | What it holds |
| --- | --- |
| Title and lead | `# How to use` plus the manifest `name` exactly (this also catches a copied guide that was never edited), then one paragraph of at most 600 characters. The manual uses it as the page lead and summary. |
| When to use it | The situations the module fits, and when another module is the better tool. |
| Before you start | The log model, required columns and minimum size (`requirements.event_log`), other modules it needs (`requirements.modules`), and anything external: an uploaded model, a second log, an API key, a sidecar. |
| Settings | Every control with its on-screen label, default and effect, as `Setting \| Default \| What it changes` tables (`### In the panel` and `### On the module page` are fine), or the sentence `This module has no settings.` |
| Reading the results | One `###` per view, chart, table or tab, in the order the panel shows them; end with `### Dashboard cards` when the manifest declares widgets. |
| Tips and limits | Sizes, run time, pitfalls, and what the module does not do. |
| Method | How the result is computed. Name the method and its authors in prose; there is no reference list, because formal citations belong in `source:` and show in the module's info popover. |

The checker enforces the shape, and a portable subset of markdown, because react-markdown (the app) and the manual's builder render differently outside it:

- Exactly the six `##` sections above, in that order, none empty. `###` and `####` are allowed inside; nothing deeper.
- Links are absolute `https://`, `http://` or `mailto:` only.
- No images, raw HTML or HTML comments (put `<module_id>` in backticks), nested lists, horizontal rules, task lists, footnotes, reference-style links, strikethrough or `_underscore_` emphasis (use `*asterisks*`).
- UTF-8, at most 64 KiB.

What the checker cannot see is whether the guide is *true*: write each label, default and limit from the panel and the code, not from memory.

```bash
uv run python -m mate.sdk.guide modules/<folder>   # one module; lists every problem, exits 1 if any
uv run python -m mate.sdk.guide modules            # every module under a root
```

Where it is enforced:

- **Upload.** `POST /api/v1/modules/install` checks the guide before anything is written. A missing or invalid guide fails the install job, and its error lists every problem.
- **Bundled modules.** `apps/api/tests/test_module_guides.py` checks each `modules/*/` folder and that the manual has its page. After editing a guide run `make docs` and commit `landing/docs/site/`.
- **Modules already installed** without a guide keep loading: a `modules.guide.invalid` warning is logged and the module shows no **How to use** button.

---

## 3. `manifest.yaml`

Every field is validated by the SDK ([`packages/module-sdk-py/src/mate/sdk/manifest.py`](../packages/module-sdk-py/src/mate/sdk/manifest.py)). Manifest errors fail loud at startup.

```yaml
id: my_module                       # lowercase snake_case, globally unique
name: My Module                     # human-readable
version: 0.1.0                      # SemVer 2.0 – bump it with every change (§11)
category: foundation                # foundation | attribute | external_input | advanced | other
description: One-line summary shown on the module card.
source: []                          # optional – cited works; also links: (see below)
license: MIT
keywords: [my topic, synonym, domain term]   # optional – helps MATE AI route chat
                                             # messages to this module. Omit and the
                                             # platform derives them from name/description.

requirements:
  event_log:                        # checked against the log's detected schema
    log_model: case_centric         # case_centric (default) | object_centric – see below
    required_columns: [case_id, activity, timestamp]
    optional_columns: [resource, end_timestamp]
    min_events: 100
    min_cases: 5
  modules: []                       # hard deps – must be loaded; an entry may pin a version
                                    # range: ["discovery>=1,<2"] (a bare id takes any version)
  optional_modules:                 # soft deps – used if present
    - id: discovery
      reason: Activity labels are taken from discovery if available.

provides:                           # capabilities you publish on the registry / bus
  - my_module.compute_something

consumes:                           # bus topics / capabilities you depend on
  - log.imported

dependencies:
  python:
    requires-python: ">=3.12"       # validation gate for in_process (see §8); selector for subprocess
    packages:                       # private to this module – installed into .venv
      - "scikit-learn>=1.5"
    inherit:                        # reuse the platform's already-installed copy
      - pm4py
      - pandas
      - duckdb
    isolation: in_process           # in_process (default, ABI-locked to platform Python) | subprocess
  npm:
    - "d3-sankey@^0.12"

frontend:
  panel: ./panel/index.tsx          # or no panel and a `board:` – see §7 Board
  widgets:
    - id: kpi-card
      entry: ./widgets/KpiCard.tsx
      min_px_w: 320               # see §7 Widgets – declare real pixel floors
      min_px_h: 220
      help:
        what: Headline cycle-time figures for the filtered log.

permissions:
  - read:event_log
  - write:module_results
```

Rules the manifest validator enforces:

- `id` is lowercase snake_case and globally unique across all modules.
- A package cannot appear in both `dependencies.python.packages` and `dependencies.python.inherit` – pick one.
- `version` is SemVer 2.0 (`MAJOR.MINOR.PATCH`, optional `-prerelease` / `+build`), at most 64 characters. It is what the platform shows and stores with every result; see §11 for when to bump which part.
- `version` and the `requirements.modules` specifiers are enforced on upload and by `mate-module validate`. A module already on disk from before (non-SemVer version, unparseable entry) still loads at startup with a warning in the API log (`modules.manifest.version_not_semver` / `requirement_invalid`); an unparseable entry counts as the bare id in front of its specifier.
- A `requirements.modules` entry is a module id, optionally followed by a [PEP 440 specifier](https://peps.python.org/pep-0440/#version-specifiers) on that module's version (`discovery>=1,<2`, `cv4cdd~=1.2`). A module whose hard dep is missing, or loaded at a version the specifier excludes, is skipped at startup with an error in the API log (`modules.discovery.requirement_missing` / `requirement_version_unmet`) – and so is everything that requires it; the rest of the platform loads. `mate-module validate` checks the specifiers against the modules in the sibling folders.
- Modules in a `requirements.modules` cycle are skipped at startup.
- Two modules declaring the same `id` is a startup error.

`inherit` exists so process-mining modules don't reinstall pandas/numpy/pm4py per module – those weigh hundreds of MB. Anything not inherited is fully isolated to your `.venv`.

### Citing sources and adding links

Credit goes in two lists. `source` cites the works the module implements – the citation string carries the author names, which is why **the manifest has no author fields**. `links` points at whatever else belongs to the module: the reference implementation's repo, a dataset, a demo, a released model.

```yaml
source:                              # max 20 – cited works
  - title: A Great Paper             # required – short label, this is what links out
    fullCitation: >-                 # required – IEEE style, DOI omitted (see below)
      J. Doe and J. Smith, "A great paper," in 2024 6th International Conference
      on Process Mining (ICPM), Aachen, Germany, 2024, pp. 1-8
    url: https://doi.org/10.1109/xxxx  # optional – the DOI link belongs here
  - title: Follow-up Work
    fullCitation: >-
      J. Doe, "Follow-up work," Information Systems, vol. 1, no. 2, pp. 10-25,
      2025
    url: https://doi.org/10.1016/yyyy
links:                               # max 20 – optional named links
  - name: Reference implementation   # required – this is the label the UI shows
    url: https://github.com/janedoe/great-miner   # required
  - name: Benchmark dataset (Zenodo)
    url: https://doi.org/10.5281/zenodo.xxxxxxx
```

**`fullCitation` house style: IEEE, minus the DOI.** IEEE is what the venues this platform cites (ICPM, BPM, TKDE, Elsevier CS journals) print and what their "cite this" buttons emit, so it is the cheapest style to copy correctly. Drop the trailing `, doi: 10.xxxx/yyyy.` – the DOI lives in `url`, and repeating it puts the same link twice in one popover row. End the string after the year (or the page range) with **no** final period:

```
Journal:    A. Author, B. Author, and C. Author, "Title in sentence case,"
            Abbrev. J. Name, vol. X, no. Y, pp. A-B, YEAR
Conference: A. Author et al., "Title in sentence case," in Proc. ICPM, YEAR, pp. A-B
Chapter:    A. Author and B. Author, "Title in sentence case," in Proc. BPM,
            Springer, YEAR
Thesis:     A. Author, "Title in sentence case," master's thesis, University of X, YEAR
```

- Keep it short: abbreviated IEEE journal names (`Inf. Sci.`, `Inf. Syst.`, `IEEE Trans. Knowl. Data Eng.`, `Fundam. Inform.`, `Softw. Impacts`) and `in Proc. <ACRONYM>, YEAR` instead of the full proceedings title. The row is ~380px wide in the About popover; a spelled-out conference name wraps to four lines and says nothing extra.
- Authors as `F. M. Surname`, initials first, `and` before the last one; 6+ authors may collapse to `A. Author et al.`. Since the manifest has no author fields, this string is the **only** place authors are credited – get the names right and don't truncate to the first one.
- Title inside the quotes verbatim as published, sentence case. `title:` above it repeats it (it's the UI label and the popover link text).
- Cite the work(s) the module **implements**, not every measure it happens to compute. Per-metric attributions belong in the panel's hover hints (see `complexity/panel/metric-info.tsx`), not in twelve `source` entries.
- Mixing styles (APA, ACM, plain prose) across manifests is the thing to avoid – every module's About box shows citations from a different manifest side by side.

Other rules:

- `source[]` entries are `{ title (required), fullCitation (required), url? }`; `links[]` entries are `{ name (required), url (required) }`. Each list caps at 20; more fails loud at startup.
- `links` was called `artifacts` before; that key is still read as a deprecated alias. Write `links:` – *artifact* now means what a module produces or consumes.
- `author`, `author_url`, `authors`, `paper_url` and `papers` are **removed**. A manifest still declaring any of them is rejected at startup with a migration hint – put the names in `fullCitation` instead.
- Both lists render as rows in the platform's "About this module" box: a source shows its title (linked when `url` is set) with the full citation underneath, a link shows its `name` pointing at its `url`. Omit a list and nothing renders.
- `fullCitation` is the YAML spelling (the SDK field is `full_citation`, and the API serialises it back as `fullCitation`).

Live examples: [`pcomp/manifest.yaml`](./pcomp/manifest.yaml) (two sources + two links), [`actor_performance/manifest.yaml`](./actor_performance/manifest.yaml). Schema: `Source`, `Link`, `MAX_SOURCES`/`MAX_LINKS` in [`manifest.py`](../packages/module-sdk-py/src/mate/sdk/manifest.py).

### `log_model` – case-centric vs object-centric (declare it!)

Every module declares which **log model** it operates on via `requirements.event_log.log_model`:

| Value | Log shape | Reads from |
|---|---|---|
| `case_centric` (default) | Classic event log – one `case_id` per trace, flat `activity`/`timestamp` events. | `ctx.event_log` |
| `object_centric` | OCEL – events relate to many objects of different types; no single case notion. | `ctx.object_log` |

This is the **single isolation switch** between the two worlds. The platform makes a module available *only* on logs whose model matches: a `case_centric` module never appears for an OCEL log, and an `object_centric` module never appears for a case-centric log ([`availability.py`](../apps/api/src/mate/api/modules/availability.py) checks this first and short-circuits, so the column/count checks below only run when the model already matches). Because of that gate, the loader binds exactly one of `ctx.event_log` / `ctx.object_log` – reach for the one your declared model implies.

The field **defaults to `case_centric`**, so a classic process-mining module works without declaring it – but declare it explicitly anyway (every bundled module does) so the model a module targets is visible on its manifest at a glance. `required_columns`, `optional_columns`, `min_events`, and `min_cases` apply to the case-centric event table; for `object_centric` modules gate on `min_events` (and the OCEL counts surfaced on the log) rather than case-centric columns.

---

## 4. `module.py` – the entry point

```python
from mate.sdk import Module, ModuleContext, on_event, route, job


class MyModule(Module):
    id = "my_module"                           # must match manifest.id

    @route.get("/kpis")
    async def get_kpis(self, ctx: ModuleContext) -> dict:
        cached = await ctx.cache.get("kpis")
        if cached is not None:
            return cached
        kpis = await self._compute(ctx)
        await ctx.cache.set("kpis", kpis)
        return kpis

    @on_event("log.imported")
    @job(progress=True, title="My Module – precompute")
    async def precompute(self, ctx: ModuleContext, payload: dict) -> None:
        await ctx.cache.set("kpis", await self._compute(ctx))

    @route.post("/recompute")
    @job(progress=True, title="My Module – recompute")
    async def recompute(self, ctx: ModuleContext) -> dict:
        await ctx.progress.update(0.0, "Loading log")
        async with ctx.event_log as log:
            df = await log.pandas()
        await ctx.progress.update(0.5, "Computing")
        kpis = self._reduce(df)
        await ctx.cache.set("kpis", kpis)
        await ctx.progress.update(1.0, "Done")
        return kpis
```

Rules:

- One subclass of `Module` per module file. The loader instantiates it exactly once per process – never instantiate it yourself.
- The class attribute `id` must equal `manifest.yaml::id`.
- Decorators only attach metadata. There is no `register(...)` call – the manifest is the registration.
- Handlers may be `async def` or plain `def`. Sync handlers are auto-wrapped so they cannot block the event loop:
  - `@route.*` rides FastAPI's built-in `run_in_threadpool`.
  - `@on_event` and `@job` are wrapped by the SDK with `asyncio.to_thread` ([`decorators.py`](../packages/module-sdk-py/src/mate/sdk/decorators.py)).
- For anything expected to run more than a few seconds, add `@job` so the user sees a toast / dock entry / progress bar instead of a hung request.

### `@route.*`

Mounts at `/api/v1/modules/{id}/<path>`. The HTTP method comes from the decorator (`route.get`, `route.post`, `route.put`, `route.patch`, `route.delete`).

```python
@route.get("/things/{thing_id}")
async def get_thing(self, ctx: ModuleContext, thing_id: str) -> Thing: ...
```

Path parameters and request bodies use FastAPI semantics. Pydantic models for request/response are first-class.

### `@on_event(topic)`

Subscribes to a bus topic. Topics are dotted strings; wildcards are not supported on the subscriber side – subscribe to the exact topic you care about.

Built-in platform topics include `log.imported`, `log.deleted`, and `job.queued|started|progress|completed|failed|cancelled`. Module-emitted topics are namespaced by module id (`my_module.something`).

### `@job(...)`

Stack `@job` on top of a `@route` or `@on_event` handler to make it asynchronous and observable.

| Param | Default | Effect |
|---|---|---|
| `progress` | `False` | Enables `ctx.progress.update(...)` streaming. |
| `title` | derived | Toast + drawer headline. May be a `(ctx, payload) -> str` callable. |
| `subtitle` | derived | Drawer subtitle. |
| `priority` | `0` | Higher = scheduled sooner. |
| `cancellable` | `True` | Whether the *Cancel* button is enabled. |
| `result_url` | `None` | URL template for the toast's *Open* action on success. |

When `@job` wraps a route, the route returns `{ "job_id": "..." }` immediately and the work runs on the platform's queue; the frontend handles the response generically.

**Shared dashboards.** A dashboard can be shared read-only. Its recipients call your routes with the owner's `log_id`, and `ctx.read_only` is `True`. The platform answers every non-GET route called that way with `403 Read-only: this log is shared with you`, before your handler runs or a `@job` is queued – and checks again, against the route as currently declared, when a queued job starts. Mark a non-GET route `viewer_safe=True` (`@route.post("/dfg/layout", viewer_safe=True)`; JVM: `RouteSpec.post("/x").viewerSafe()`) only when it reads, or caches a result derived deterministically from the owner's data and the request, keyed on its full input. Never mark one that changes what the owner sees: uploads, compute runs that replace your result, chat history. GET routes always run for viewers, so keep owner-visible writes out of them.

---

## 5. `ModuleContext` – what every handler receives

Defined in [`packages/module-sdk-py/src/mate/sdk/context.py`](../packages/module-sdk-py/src/mate/sdk/context.py). All fields are typed Protocols – depend on the Protocol, not the implementation.

```python
@dataclass
class ModuleContext:
    log_id: str                     # the log this invocation is scoped to ("" for global routes)
    module_id: str
    event_log: EventLogAccessProtocol
    bus: EventBusProtocol
    registry: ModuleRegistryProtocol
    cache: ResultCacheProtocol
    config: ModuleConfigProtocol
    progress: ProgressReporterProtocol
    logger: structlog.BoundLogger
    workdir: Path                   # scratch space, auto-cleaned on completion
    inputs: InputsProtocol          # artifacts bound to your manifest `inputs:` ports
    read_only: bool                 # True for a shared dashboard's viewer (not the log's owner)
```

### `ctx.event_log`

Lazy access to the log. Always use the async-context-manager form so the platform can manage file handles and DuckDB connections:

```python
async with ctx.event_log as log:
    rows = await log.duckdb_fetch(
        "SELECT activity, count(*) FROM events GROUP BY 1 ORDER BY 2 DESC"
    )
    df = await log.pandas()         # or .polars(), .pm4py()
```

Prefer DuckDB for aggregations (millions of rows in milliseconds). Use pandas/polars when you need DataFrame semantics. Use `pm4py` only when an algorithm needs the pm4py event log object – it's the heaviest.

### `ctx.open_event_log(log_id, filters=None)` – reading a *second* log

`ctx.event_log` is bound to the one log the invocation is scoped to. When a module genuinely needs another log too (comparison, benchmarking), `ctx.open_event_log(other_log_id)` returns a second `EventLogAccess` with the **same** interface:

```python
async with ctx.event_log as base, await ctx.open_event_log(other_id) as other:
    base_df = await base.pandas()
    other_df = await other.pandas()
```

It is the only sanctioned cross-log accessor and it **enforces tenant isolation**: the target must be a case-centric log owned by the same user, else it raises (a log belonging to another user is reported as "not found" – never confirmed). In a viewer's read-only context (`ctx.read_only`) it opens only the shared log itself; any other log – the viewer's own or another of the owner's – is refused the same way (`PermissionError`, which the comparison modules answer with 404). Don't reach into `mate.api.*` internals to open a log yourself – that bypasses the ownership check.

`filters` decides which rows that view serves (same `{field, op, value}` entries as `active_filter`):

| `filters` | view serves |
| --- | --- |
| `None` (default) | the target log's own committed Events-tab filter, exactly like the primary view |
| a list | **replaces** that committed filter, for this view only – nothing is persisted |
| `[]` | the raw log, committed filter ignored |

That's what makes *the same log twice* a meaningful pair – the filter, not the log id, is what identifies a view:

```python
# Two cohorts of one log, compared in a single request.
north = [{"field": "region", "op": "equals", "value": "north"}]
south = [{"field": "region", "op": "equals", "value": "south"}]
async with await ctx.open_event_log(ctx.log_id, north) as a, \
           await ctx.open_event_log(ctx.log_id, south) as b:
    ...
```

Cache accordingly: if you memoise a cross-log result, hash **every** view's `(log_id, filter)` into the key, or two differently filtered comparisons of one log collide on one entry (`modules/process_comparison` is the reference). In-process only – the subprocess/JVM bridge does not proxy `open_event_log`.

### `ctx.cache`

Per-`(log_id, module_id)` result cache. Use it to memoise expensive computations across requests:

```python
if not await ctx.cache.exists("kpis"):
    await ctx.cache.set("kpis", await self._compute(ctx))
return await ctx.cache.get("kpis")
```

What the platform clears for you: when the log's data changes (reimport/remap, a committed filter, a cell edit) it empties this module's cache **before re-running its precompute** – but only for modules that have a precompute and were already computed on the log (see *Precompute* below). Of the module config it watches only properties tagged `ui.affects: precompute` (see `ctx.config`), and a module that computes inline on a cache miss gets no clearing at all, so put everything else your result depends on (config values, filter, granularity…) into the cache *key*. Keys starting with `__` survive that clearing (and are persisted with your results); the platform uses them for its own entries (`__ai_guidance`, a variant's `__meta.json`), so a module only ever writes durable state such as an uploaded file under `__<module_id>_<name>` – interop's `__interop_imported_bpmn` is the example.

### `ctx.read_only`

`True` when the requesting user is not the log's owner – the log was reached through a shared dashboard. Reads (`ctx.event_log`, `ctx.cache.get`, `ctx.inputs`) then serve the owner's data, and compute-and-cache on a miss keeps working (see `ctx.config` for which settings it computes with). A share never lets the viewer change the owner's state, though: besides the platform's method gate (§4 *Shared dashboards*), a route that writes durable owner state – an upload, a stored model, a file beside the log, the reserved `__<module_id>_*` cache entries – checks `ctx.read_only` first and refuses with `HTTPException(403)`, before it reads the upload. Behind that check the platform cache raises `mate.sdk.errors.ReadOnlyContextError` (a `PermissionError`) for `ctx.cache.set`/`delete` of any `__*` key and for `clear()` in a read-only context: a route answers 403, a job fails with the reason. Test the guard with `build_test_context(..., read_only=True)`. Subprocess and JVM workers get the flag as `read_only` in the context snapshot (`modules/PROTOCOL.md` §6; JVM `ctx.readOnly()`).

**Run variants.** Besides the log's default run, the platform can run your precompute again with a *run spec* – other values for your `ui.scope: dataset` properties, other input artifacts, another event filter (`POST /api/v1/event-logs/{log_id}/module-runs/{module_id}/variants`, the building block of flows and the artifact workspace). Your handler does not change: `ctx.config`, `ctx.inputs` and `ctx.event_log` already carry the variant's values, and `ctx.cache` is the variant's own namespace (`_r_<key>/` beside the default result, kept when the default run recomputes). So a precompute that reads only from `ctx.*` and writes only to `ctx.cache` supports variants for free – never reach into another module's cache directory or key results on anything outside `ctx`. A variant run emits no `<module_id>.completed`, so chained modules don't wake for it.

### `ctx.inputs`

Your manifest's `inputs:` ports, bound to artifacts other modules produced. `await ctx.inputs.get("reference_model")` returns the artifact (a dict, DataFrame or bytes) or `None` while it is unbound or not produced yet; `ctx.inputs.ref(port)` is its address, `{log_id}/{module_id}[@{variant}]/{key}`, and `ctx.inputs.ports` lists the port ids. By default a port reads its `from:` producer's default result for this log. A run variant may bind a port to any artifact of the port's type instead – another run variant's (`{log_id}/{module_id}@{variant}/{key}`), one from another log of the same user, or one the user uploaded to their artifact workspace (`{log_id or "_"}/_workspace/{id}`, so `_workspace` is a reserved module id). Nothing outside the requesting user's own modules, logs and uploads ever binds. Every typed, cached output of a finished run (default or variant) shows up in the user's **Artifacts** workspace (`/api/v1/artifacts`), with the refs its run had bound as lineage – nothing to do on your side beyond declaring `artifact_type` + `cache_key`. Subprocess/JVM workers get the same as file paths (`modules/PROTOCOL.md` §6, `inputs`).

**Flows.** The same declarations make a module a node of the flow builder (`/flows`): any module with a precompute can be dropped in, its typed, cached datasets (`artifact_type` + `cache_key`) become output handles and its `inputs:` ports input handles. A node with no params and no input other than what its ports read unconnected uses your module's default run; one with `ui.scope: dataset` params or other inputs runs as a run variant with them (as does a plain node whose default run is older than an input it reads). A dataset named `<anything>_from_<port id>` (as in `modules/interop`) marks your module as a converter from that port's type to the dataset's – the editor offers it as "Insert converter" between mismatched ports.

### `ctx.config`

User-set configuration, validated against your `config_schema`. Read with `ctx.config.value` (full dict) or `ctx.config.get(key, default)`. It is the *effective* config for the log the handler runs on, and schema defaults are **not** filled in – pass your own default to `get`.

Two independent flags under a property's `ui` object say where its value comes from and what a change invalidates:

```yaml
config_schema:
  properties:
    min_support:
      type: number
      default: 0.5
      ui:
        scope: dataset        # module (default) | dataset
        affects: precompute   # precompute | compute | view
```

- `scope: dataset` – the user may override the value per event log (**Parameters for this log** on the log's module page); the value on the module's settings page is the default every log inherits. `scope: module` (the default) is one value for all logs.
- `affects: precompute` – your precompute job reads it. When its effective value for a log changes (a per-log override, the module-wide value, an admin lock), that log's stored run turns stale and recomputes from an emptied cache. The selected `model_store` model counts the same way. Numbers compare by value (`1` = `1.0`).
- `affects: compute` – a route recomputes from it on demand (key your cache by it); `view` – display only. Neither stales a stored run.

`ctx.config` is merged in this order: the user's module-wide config → the log owner's per-log overrides (`scope: dataset` keys only) → a run variant's spec params (same keys) → admin-locked settings cards, last. A viewer of a shared dashboard computes into the *owner's* result cache, so its base is its own module-wide config with the owner's value (unset there = your default) on top for every key that determines results: `ui.affects: compute`/`precompute`, `ui.scope: dataset`, and the `model_store` selection. Everything else stays the viewer's own – connection settings (a server URL, a username), `ai`, undeclared keys, and any property that is or nests a secret (`ui.widget: password`/`secret`, `format: password`, `writeOnly`, `secret: true`). So tag every property a GET or `viewer_safe` route's result depends on with `ui.affects` (or `scope: dataset`) – an untagged one is read from the viewer's settings and its numbers land in the owner's cache – and never tag a connection setting (host, URL, username) with `affects`, or a viewer's credential is sent to a server the owner chose. If the config cannot be read, a viewer's request fails instead of computing with defaults.

### `ctx.progress`

Inside a `@job(progress=True)` handler, emit progress to the dock + drawer + WebSocket stream. Pick whichever style fits – all three render a live bar, none requires you to know the total up front:

```python
# 1. Fraction (0.0–1.0, no total) → determinate percentage bar.
await ctx.progress.update(0.42, "Computing fitness")

# 2. Absolute counts (current + total) → determinate bar *and* an ETA.
await ctx.progress.update(current=4200, total=10000, stage="replay")

# 3. Unknown scope → pass an integer running counter so the row reads
#    "{n} processed" instead of sitting on a dead "Estimating…" pulse.
await ctx.progress.update(current=processed_so_far)
```

A `float` `current` in `[0, 1]` with no `total` is read as a fraction (mapped to 0–100); an `int` `current` is always a running counter (`update(current=1)` is "1 processed", not "100%"). Progress is **optional and never enforced** — omit it and the job still runs, just with an indeterminate "Working…" bar (and a stall hint if it stays silent past ~3 min). But for long jobs you *should* **emit *something* live** – never leave the bar silent for minutes.

### Precompute – when your module runs

Modules compute **lazily**. A log is `ready` the moment it is parsed, and your module's precompute – an `@on_event("log.imported")` (or `ocel.imported`) handler stacked with `@job` – runs for it only when something asks: the user opens the module page, a dashboard card shows it, or an MCP tool reads it. The platform starts the job, shows live progress with a Cancel, and mounts your panel only once it has finished, so a panel can assume its cache is filled; opening it again is instant.

Say whether it should *also* run in the background after every import with the manifest field `precompute`:

```yaml
precompute: lazy    # default – computes the first time a log opens it
precompute: eager   # also warm it right after every import
```

`eager` costs CPU on every import of every user, so reserve it for the one module whose output people open first (Discovery is the only bundled one). It is a recommendation: an admin can override it per module in **Admin → Modules**. Neither setting changes what your handler does.

After the log's data changes (committed filter, reimport/remap, cell edits) the platform re-runs only the modules *already computed* on that log, with their cache emptied first; modules nobody opened stay lazy. So a precompute handler must:

- rebuild everything from `ctx.event_log` – the payload only carries `log_id` and counts, and has **no `import_job_id`** when the run is on demand;
- be idempotent: it runs on demand, again after data changes, and again after an API restart that interrupted it;
- write its results to `ctx.cache` under keys derived from its inputs.

**Ordering.** To run *after* another module, subscribe to its reserved `<module_id>.completed` event (the platform auto-emits it when that module's precompute succeeds, so the producer does nothing special) and/or list it under `requirements.modules`:

```python
@on_event("discovery.completed")   # runs only after `discovery` has computed for the log
@job(progress=True, title="My overlay")
async def precompute(self, ctx, payload): ...
```

Declare the edge so it validates: `consumes: [discovery.completed]` (+ `optional_modules: [discovery]`). Opening your module first runs the producer, then your job; if the producer fails or is cancelled, your module shows that error with a Retry instead of waiting forever. A module with **no precompute of its own but a hard `requirements.modules` dependency that has one** (Concept Drift Explainer → CV4CDD) is gated on that dependency the same way. A `@on_event` *without* `@job` (e.g. a cheap cache refresh) is fire-and-forget: never tracked, never shown, never part of any of this.

When a run is part of an import (an eager module), the jobs UI shows it in the import's step list in **execution order** (topological; alphabetical by module id within a layer) – rely on that order when writing progress copy.

### `ctx.workdir`

A temporary directory unique to this invocation. Cleaned automatically on completion.

### `ctx.logger`

A `structlog.BoundLogger` already bound with `module_id` and `log_id`. Use it; `print()` is dropped.

---

## 6. Communicating with other modules

Two patterns. Both are typed.

### (a) Event bus – fire-and-forget, fan-out

```python
# emitter
await ctx.bus.emit("my_module.kpi.computed", {"log_id": ctx.log_id, "kpis": kpis})

# subscriber (in another module)
@on_event("my_module.kpi.computed")
async def react(self, ctx: ModuleContext, payload: dict) -> None: ...
```

Topics you emit must be listed in your manifest's `provides:` (or be one of the platform's built-in topics). Topics you subscribe to must be listed in your `consumes:`. The platform validates this at startup, so missing-dep bugs surface at boot – not at runtime.

Define payload shapes as Pydantic models in your `events.py` and use them on both sides. The bus rejects untyped emits.

### (b) Capability registry – typed RPC for synchronous queries

When you need a result back from another module, use the registry instead of round-tripping through the bus:

```python
if ctx.registry.has("conformance"):
    fitness = await ctx.registry.call("conformance.compute_fitness", log_id=ctx.log_id)
else:
    ctx.logger.warning("conformance not installed; skipping fitness annotation")
```

Capabilities you publish go in `provides:`. Capabilities you call must be in `consumes:` (hard) or `optional_modules:` (soft). The platform refuses to mount a module that calls undeclared capabilities.

Rule of thumb:
- Use the **bus** for "this happened, anyone interested can react." It is one-way.
- Use the **registry** for "I need a value." It is request/response.

---

## 7. Frontend

The platform loads each module's frontend bundle from `modules/<folder>/.dist/` – your panel and widgets are bundled with esbuild at platform startup, not as part of the Next.js build. You don't import anything from `apps/web`.

### Panel

`manifest.frontend.panel` is the entry rendered on `/processes/{logId}/modules/{moduleId}`. Minimum shape:

```tsx
// modules/<folder>/panel/index.tsx
import type { ModulePanelProps } from "@mate/module-sdk-ts";

export default function Panel({ logId, moduleId, config }: ModulePanelProps) {
  // render whatever you want; common building blocks (process visualiser,
  // KPI card, ECharts wrapper, time-window picker) come from the TS SDK
  return <div>...</div>;
}
```

Use the shadcn-themed building blocks in `@mate/module-sdk-ts` rather than re-implementing tables, KPI cards, charts. They consume the same CSS variables as the host app, so light/dark and density switches just work.

**Linking entities.** Wherever your UI renders a variant or an activity, make it clickable: build the href with `variantHref(logId, variantId)` / `activityHref(logId, rawActivityName)` from the SDK (or `@/lib/dashboards/drill`) and render it with `next/link` (a runtime external) — the canonical entity views live at those URLs, and a real `<Link>` keeps route skeletons and cmd-click working. Both helpers take **raw** ids/names and encode once — never pre-encode. Filter-style jumps (e.g. "show variants with this activity") stay explicit buttons using the `DRILL_PARAMS` vocabulary; widgets emitting `onDrill({ params: { activity } })` (or `variant`) land on the entity view automatically unless they target a specific module.

### Widgets

A widget is your module's card on a dashboard. Declare it in `manifest.frontend.widgets`.

```yaml
widgets:
  - id: bottlenecks
    entry: ./widgets/Bottlenecks.tsx
    title: Bottlenecks
    description: Slowest activities by median duration.   # one-line palette blurb
    icon: Timer                                           # Lucide name

    # Sizing. default_*/min_* are units on the fixed 12-column grid.
    default_w: 4
    default_h: 9
    min_w: 3
    min_h: 6
    # ...but declare the PIXEL floors too. A grid unit is only a real size once
    # the board's width is known, so these are what actually stop the card
    # being shrunk below what it can render. Measure them in the browser.
    min_px_w: 320
    min_px_h: 240

    # The ⓘ. `description` is the blurb; this is the real explanation.
    help:
      what: Which activities take longest, ranked by median duration.
      read: Longer bars are slower. The number is the median, not the mean.
      computed: Median over completed cases only; in-flight cases are excluded.
      docs_url: https://…                                 # optional

    # Per-card options. Same JSON-Schema dialect as a module's config_schema.
    config_schema:
      properties:
        top_n:
          type: integer
          title: Activities shown
          minimum: 3
          maximum: 25
          default: 8
          ui: { widget: slider }

    # If the card shows several figures, declare them: the user can then pick a
    # subset per placement, and the chosen ids arrive as `config.kpis`.
    kpis:
      - { id: median, title: Median duration, info: Half of cases are faster. }
      - { id: p95,    title: P95 duration,    default: false }

    # If the card can render more than one of your module's views, declare them
    # and which config keys apply to each — the card's settings panel then shows
    # the knobs that actually apply to the selected view.
    views:
      - { id: bars,  title: Bar chart, exposes: [top_n] }
      - { id: table, title: Table,     exposes: [top_n, show_counts] }

    # Where clicking a number in the card navigates. Optional — without it the
    # platform still offers "open in module" for the declaring module.
    drill:
      module_id: performance          # defaults to this module
      params: { view: bottlenecks }   # merged into every drill from this card

    # Only when your controls can't be expressed as JSON Schema (a canvas with
    # layout modes, live thresholds, rendering toggles). The card's settings
    # panel mounts this instead of generating a form. Prefer config_schema.
    settings_entry: ./widgets/BottlenecksSettings.tsx
```

**Sizing is the one to get right.** A card whose minimum is too small renders unreadably; one whose minimum is too large can't share a row. Set `min_px_w`/`min_px_h` to the size at which the widget still communicates, then check it on a board at exactly that size.

#### Build the card from the shared kit

Don't hand-roll a card shell or pick your own chart colours — a board shows cards from several modules at once, and it has to read as one product.

```tsx
import {
  CardShell, KpiTile, KpiGrid, InfoHint,   // structure
  seriesColor, sequentialScale, statusColor, CHART_CHROME,   // colour
} from "@mate/module-sdk-ts";

export default function Bottlenecks({ logId, config, onDrill }) {
  const { data, isLoading, isError } = useBottlenecks(logId);
  return (
    <CardShell loading={isLoading} error={isError} empty={!data?.items.length}>
      <Bar dataKey="avg" fill={seriesColor(0)}
           onClick={(b) => onDrill?.({ params: { activity: b.activity } })} />
    </CardShell>
  );
}
```

- **`CardShell` owns scrolling.** The card frame is `overflow-hidden`; if your widget adds its own scroll container the board gets a scrollbar inside a scrollbar. Pass `scroll` only for genuinely tabular content.
- **Colour by job, not by looks.** `seriesColor(i)` for distinct things (fixed order, never cycled — colour follows the entity, not its rank, so filtering must not repaint the survivors); `sequentialScale(t)` for magnitude; `divergingScale(t)` for above/below a baseline; `statusColor()` only for good/warning/serious/critical, and always with an icon + label. Past 8 series, fold the tail into "Other" — a 9th hue is indistinguishable under colourblindness.
- **One series → one colour.** Shading each bar by its own length double-encodes the value and spends the only free channel on information the bar already shows.
- **Gridlines are solid hairlines** (`CHART_CHROME.grid`). Dashed reads as "threshold".
- **Never a dual-axis chart.** Two measures of different scale → two cards, or index both to a common base.

#### Drill-down

`onDrill` is passed in by the dashboard (and is `undefined` when a panel embeds your widget, so always call it optionally). It turns a number into a way in:

```tsx
onDrill?.({ params: { activity } });                    // this module, filtered
onDrill?.({ moduleId: "performance", params: { activity } });   // another module
onDrill?.();                                            // just open this module
```

Use the standard parameter names from `DRILL_PARAMS` (`activity`, `from`/`to`, `case`, `variant`, `object_type`, `ts_from`/`ts_to`, `view`, `metric`) — cross-module links only work because both sides agree on them. On the receiving side read them with `useDrillParams()` rather than parsing the query string by hand.

**Shared boards are read-only.** On a dashboard opened through a share, every non-GET module route returns 403 for the viewer (only `viewer_safe=True` routes are exempt). A widget that offers a write action (run, save, upload) reads `useCardReadOnly()` from `@/components/dashboards/kit` and hides that action when it returns `true`, showing a short owner-only hint instead (agentsimulator's cards are the example). Outside a dashboard card (a panel embedding the widget) it returns `false`.

Widgets can also be embedded by *other* modules:

```tsx
import { useWidget } from "@mate/module-sdk-ts";

const ThroughputChart = useWidget("performance", "throughput-chart");
return <ThroughputChart logId={logId} config={{}} />;
```

`useWidget` lazy-loads, renders a `Skeleton` while loading, and a placeholder card if the source module is missing.

**Removed:** `frontend.page_layout` and `frontend.side_rail` no longer exist — nothing ever rendered them. Manifests still declaring them keep loading; the keys are ignored.

### Board – a module page without TSX

Instead of a panel, a module can declare `manifest.frontend.board`: a default layout of the platform's generic visualizations over the module's own `datasets:`. The module page then renders the shared dashboard editor, bound to the log. A module with no `frontend.panel` (and no widgets) ships **no TSX at all** – no `panel/`, no `.dist/` for `mate-module pack` to demand. [`modules/interop`](interop/manifest.yaml) is the reference.

```yaml
datasets:
  - id: imported_bpmn
    artifact_type: bpmn
    cache_key: imported_bpmn          # the card reads this entry; other modules' inputs: too
    # route: /models/imported_bpmn    # optional – a route serves the card instead (filterable)

frontend:                             # no panel:
  board:
    items:
      - id: imported-bpmn             # unique within the board
        viz: bpmn-view                # a viz id from the web registry (below)
        dataset: imported_bpmn        # one of this module's datasets:
        title: Imported BPMN model    # optional card title
        mapping: {}                   # optional – viz field -> column, e.g. { x: activity, y: count } for bar
        options: {}                   # optional – the card's viz options, passed as-is
        x: 6                          # position and size in units of the 12-column grid
        y: 12
        w: 6
        h: 12
```

Rules the manifest validator enforces (`mate-module validate`, boot, upload):

- `x + w <= 12`, with `x >= 0` and `1 <= w <= 12`; `0 <= y <= 400` and `1 <= h <= 48` – the dashboard grid's own bounds, so a board never fails when it is seeded.
- Item `id`s are unique within the board.
- `dataset` names one of this module's `datasets:`. A dataset with a `route` renders through it (live, honouring a dashboard filter); a `cache_key`-only one renders what your precompute cached, read by artifact ref (`GET /api/v1/artifacts/content?ref={log_id}/{module_id}/{cache_key}`) – so a precompute that writes typed outputs needs no route at all.
- `viz` is **not** checked (the registry is TypeScript): an unknown id renders as a placeholder card. Open the page once.

**Viz ids** come from [`apps/web/lib/visualizations/registry.ts`](../apps/web/lib/visualizations/registry.ts); the viz must accept the dataset's `shape` – artifact viewers match on its `artifact_type` instead. Chart vizs read their columns from `mapping`, keyed by the registry's `fields` (`bar` needs `x` and `y`).

| Dataset | Viz ids |
| --- | --- |
| `shape: table` | `table`, `bar`, `line`, `area`, `scatter`, `pie`, `histogram`, `heatmap`, `radar`, `funnel`, `treemap`, `bubble` |
| `shape: kpi` | `kpi-grid`, `number-tile`, `gauge`, `radial-bar` |
| `shape: graph` | `process-map` |
| `shape: tree` | `tree` |
| `artifact_type: dfg` / `petri_net` / `process_tree` / `bpmn` | `dfg-view` / `petri-net-view` / `process-tree-view` / `bpmn-view` |

**Panel or board?** Choose a board when the module's results are tables, KPIs or models the generic visualizations already draw, and users gain from rearranging them. Choose a panel for anything a viz can't do: a canvas with its own controls, forms that trigger work, state shared across views. A module declaring both gets its panel; the board is only used when there is no `frontend.panel`.

**Persistence.** Each user gets one board per (module, log), created from the manifest the first time the page opens (`GET /api/v1/event-logs/{log_id}/modules/{module_id}/board`) and stored as a dashboard tagged with `module_id`. Edits autosave like any dashboard, but the board stays bound to its log and out of the dashboard list, sharing and export. Changing `frontend.board` later does not touch boards users already have: in edit mode, **Reset to module default** (`POST …/board/reset`) puts the manifest's items back and keeps the board's settings.

**Authoring the layout.** Arrange the board in the UI instead of guessing coordinates: in local dev (`next dev`) or on the demo stack, edit mode shows **Copy layout as manifest**, which copies the current cards as a `frontend.board` YAML snippet to paste into `manifest.yaml`. Cards that aren't a viz of this module's own datasets are skipped, with a count in the toast.

### Canvases (graphs / diagrams) – the shared contract

Every graph, map or diagram view in the platform is **one canvas with one control cluster**. `modules/discovery` → DFG is the reference; new modules inherit it by using the shell instead of building chrome.

Non-negotiables:

- **Render through `CanvasShell`** (`@/components/visualizations/canvases/shared/canvas-shell`). It brings the dotted grid, idle-fading minimap, exact initial fit, drag-threshold guard (a click never persists as a drag) and pseudo-fullscreen. Never mount `<ReactFlow>` yourself and never hand-roll a zoom/fit/fullscreen pill. A non-React-Flow viewer (bpmn-js etc.) renders `CanvasControlCluster` from `…/canvas-toolbar` directly – same pill, same order.
- **Every control goes in the cluster's settings popover** – `settings={…}` on the canvas, built from the `CanvasSetting*` primitives in `…/canvas-toolbar` (`CanvasSettings`, `CanvasSettingsSection`, `CanvasSettingsSelect`, `CanvasSettingsSwitch`, `CanvasSettingsSegmented`, `CanvasSettingsSlider`, `CanvasSettingsSearch`, `CanvasSettingsActions`). Don't hand-roll rows and **don't put a filter bar above the canvas** – the graph runs full-bleed, and the controls stay reachable in fullscreen. Model/algorithm pickers that drive a fetch belong in there too, in a leading `CanvasSettingsSection title="Model"`.
- **`onReset`** wires the cluster's reset button (discard dragged positions / re-run the layout). Omit it only when the layout can't be dragged.
- **Expensive settings commit on release** – `onCommit` on a slider, not `onChange`, so one drag doesn't queue one re-mine per pixel.
- **Never unmount the canvas to show a spinner.** A refetch triggered from the popover would close the popover mid-interaction. Keep the last result on screen and pass `busy` (the shell renders a "Working…" chip); the discovery panel's `useSticky` helper is the pattern.
- **Canvases are read-only unless you opt in.** An editor that lets the user draw edges passes `connectable` plus `onConnect` (and optionally `isValidConnection` to reject a drop); the nodes need React Flow `<Handle>`s. Omit all three and the shell stays non-connectable.

```tsx
<CanvasShell
  nodes={nodes}
  edges={edges}
  nodeTypes={nodeTypes}
  busy={query.isFetching}
  settings={
    <CanvasSettings>
      <CanvasSettingsSelect label="Edge label" value={mode} onChange={setMode} options={MODES} />
      <CanvasSettingsSwitch label="Hide self-loops" checked={hide} onChange={setHide} />
    </CanvasSettings>
  }
  onReset={() => resetPositions()}
  onNodesChange={onNodesChange}
  onNodeDragStop={onNodeDragStop}
/>
```

Both shared files are runtime externals (`apps/web/lib/runtime-externals.json`), so importing them adds nothing to your bundle.

**Artifact viewers** (also runtime externals): render a `petri_net` / `process_tree` / `dfg` / `bpmn` payload with `PetriNetView` / `ProcessTreeView` / `DfgView` / `BpmnView` from `@/components/visualizations/artifacts/{petri-net,process-tree,dfg,bpmn}` instead of building your own; `…/artifacts/layout` (ELK layered + tidy-tree layout, `runAfterPaint`, `handlePositions`) and `…/artifacts/edges` (`ElkEdge`, edge geometry) back custom graphs. `BpmnView` takes the bpmn-js libraries your panel bundles as `engine` (pattern: `BPMN_ENGINE` in `modules/discovery/panel/bpmn-engine.ts` – keep it in its own file so a widget never bundles bpmn-js) and your markers via `decorate`.

### Talking to your backend

Use the platform fetch helper – it injects the auth/session correctly and respects the API base URL:

```tsx
import { api } from "@mate/module-sdk-ts";
const kpis = await api.get(`/api/v1/modules/${moduleId}/kpis?log_id=${logId}`);
```

For real-time updates, subscribe to the `WS /events` stream filtered by your topic:

```tsx
import { useEvents } from "@mate/module-sdk-ts";
useEvents(["my_module.kpi.computed"], (env) => { ... });
```

---

## 8. Dependencies & isolation

### Python

The platform creates and owns `modules/<folder>/.venv`:

- On every boot, the platform hashes your `dependencies` block. If unchanged, it skips reinstalling – boots are near-instant. (For `in_process` modules the hash also folds in the platform's Python version, so a venv built under a different Python – e.g. a host-mode 3.13 venv bind-mounted into a 3.12 container – rebuilds automatically instead of crashing on import.)
- Your code resolves imports against `.venv/site-packages` first, then stdlib, then the platform's `inherit` set, then the SDK. Other modules' dependencies are not visible.

**Python version (important).** `in_process` modules (the default) are imported into the *platform's own interpreter*, so their venv is always built on **exactly that Python** – currently 3.12. Your manifest's `requires-python` is a **validation gate**, not an interpreter selector: if the platform's Python doesn't satisfy it, the module fails to load with a clear, actionable message. You therefore **never hand-pin `<3.13`** to dodge ABI mismatches – the platform pins the interpreter for you. Declare `requires-python` only when your dependencies genuinely can't run on a newer Python (e.g. a C-extension with no wheels for it); the gate then turns that into a fast, clear failure instead of a cryptic wheel-build error.

If you genuinely need a **different Python version** than the platform, set `isolation: subprocess`. The platform spawns a long-lived worker from your venv on its own interpreter (selected by `requires-python`; uv picks or auto-downloads it) and proxies handler calls over a Unix-socket JSON-RPC. The `ModuleContext` interface is unchanged – `@route`, `@job`, and `@on_event` all work, and `event_log.pandas()/polars()/pm4py()` stream the log to your process via a Parquet handoff. Caveats: each call adds 5–50 ms; `inherit` names are installed into your own venv (no shared interpreter to inherit from across a process boundary); job cancellation is best-effort; dynamic `@job(title=...)` callables fall back to a static label. Use subprocess only when you actually need a different Python or have a hard native-lib conflict (e.g. `numpy 1.x` while the platform ships `numpy 2.x`).

### Other languages

A module's backend doesn't have to be Python at all – see [§13](#13-writing-a-module-in-another-language-jvm) for the JVM runtime (and the reserved Node/R designs). Frontend panels/widgets are TypeScript regardless of the backend language.

### Needs a whole server next to the platform?

If your dependency is not a library but a **server process of its own** (a graph
database, a search engine, …), it ships as an optional compose-profile sidecar – the
module only connects to a configured address and must treat the shared instance as
transient scratch. Full contract + checklist: [`SIDECAR_SERVICES.md`](./SIDECAR_SERVICES.md)
(reference implementation: `modules/actor_performance` with Neo4j).

### npm

`pnpm install --dir modules/<folder>` runs at startup. Bundles land in `.dist/`. Same isolation story – your widgets bundle against your own `node_modules`.

### Don't touch

- `apps/api/pyproject.toml` and `apps/web/package.json` are off-limits to module authors. If a module modifies them, that's a bug. Your dependencies belong in your manifest.

### `.gitignore` per module

```
.venv/
.dist/
node_modules/
__pycache__/
```

Commit `manifest.yaml`, `module.py`, your tests, your frontend sources, and `uv.lock`.

---

## 9. Lifecycle a module goes through

1. **Discovery.** On boot the platform scans `modules/*/manifest.yaml` (one level deep) and any installed Python entry points exposing `mate.modules`.
2. **Validation.** Manifests parsed; dep graph built. A module whose manifest doesn't validate, whose hard dep is missing or loaded at a version its specifier excludes, or that sits in a hard-dep cycle is skipped (and logged) – the rest loads.
3. **Materialise dependencies.** `uv sync` per module if its dep hash changed; same for the JS bundle.
4. **Topological load.** Hard-dep order. Your module's `Module` subclass is instantiated once.
5. **Mount.** Routes registered at `/api/v1/modules/{id}/*`, `@on_event` handlers subscribed, `@job` handlers registered with the queue.
6. **Per-log gating.** When a log is opened, the platform re-evaluates `requirements.event_log` against that log's model and schema. The `log_model` gate runs first – a module whose declared model doesn't match the log is **unavailable** and hidden from the grid (case-centric and object-centric modules never cross). If the model matches, the column / `min_events` / `min_cases` checks decide **available** vs **unavailable**, with a tooltip explaining what's missing.
7. **Hot reload (dev).** Watchdog on `modules/` re-loads changed files without a platform restart. Manifest dep changes trigger an in-place `uv sync` for that module only.

If a module fails to load (manifest error, install error, import error), the failure is reported on the per-module card and in `/settings/modules/{moduleId}` – the rest of the platform stays up.

---

## 10. Developing & testing locally

### Develop a module locally

The SDK installs a `mate-module` command (run it from the repo root with `uv run`):

```
uv run mate-module new my_module --dir modules   # scaffold manifest.yaml, module.py, HOW_TO_USE.md, tests/test_my_module.py
uv run mate-module validate modules/my_module     # manifest + HOW_TO_USE.md, same checks as upload
uv run mate-module run precompute modules/my_module [--log events.csv] [--param KEY=VALUE ...]
uv run mate-module pack modules/my_module [-o out.zip]
```

- **`new`** writes a minimal `in_process` module: a `log.imported` precompute job that caches a summary and a `GET /summary` route that serves it, plus a passing test. Fill in the `TODO`s.
- **`validate`** prints `ok` / `FAIL` with the problems. It does not import `module.py` – `run` or the tests do that.
- **`run <handler>`** calls one `@route` / `@on_event` / `@job` method against the fake context below (the bundled sample log, or `--log` – a `.csv`/`.parquet` with the canonical columns) and prints `{"result", "cache", "logs"}` as JSON. `--param KEY=VALUE` is repeatable; `VALUE` is parsed as JSON, else kept as a string. An `@on_event` handler gets the params merged into its `{"log_id", "user_id"}` payload; any other handler gets them as keyword arguments, and parameters you don't pass take their defaults.
- `run` exits 2 with an `error:` line when a required parameter (no default, or `Query(...)` / `Body(...)`) has no `--param`, when a `--param` names an argument a route or job doesn't take (unless it accepts `**kwargs`), or when `--log` is missing, not `.csv`/`.parquet`, or unreadable.
- **`pack`** refuses a module that fails `validate`, then zips it **including `.dist/`**: the platform serves an uploaded module's frontend from the bundles in the archive and never builds them itself. If the manifest declares a panel, a widget or a widget `settings_entry` and its bundle (`.dist/panel.js`, `widget-<id>.js`, `widget-<id>-settings.js`) is missing, `pack` exits 1 and prints the command that builds them – from a Mate checkout after `pnpm install`: `FF_MODULES_DIR=<module's parent folder> node apps/web/scripts/bundle-modules.mjs <id>`.
- `pack` leaves out `.venv`, `node_modules`, `__pycache__`, `.git`, `.gradle`, `.installed-hash`, `.pytest_cache`, `.ruff_cache`, `.mypy_cache`, `.DS_Store` and every `*.zip` at the top of the module folder (zips in subfolders are packed). The default output is `<id>-<version>.zip` in the current folder; `-o` picks another path.

To see the module in the real app, run it alone on a demo-mode stack:

```
make module-dev MODULE=modules/my_module   # PORT=8000 WEB_PORT=3000 by default
```

- No Docker, Keycloak or `.env` needed – it forces `DEMO_MODE`, signs you in as the demo user and keeps every byte (SQLite DB, Parquet, results) under `data/.module-dev/`. The staged modules load as bundled defaults, so the demo user owns them without admin rights. Delete that folder to start over; `make clean` does not touch it.
- Only your module and its hard deps (`requirements.modules`, transitively, looked up by id under `modules/`) are loaded, each staged as a symlink named by its manifest id. A missing dep, an id declared by two folders, or an unreadable `manifest.yaml` in `MODULE` stops the script with a `module-dev:` message. A staged module that fails to load only prints a warning; the API log has the error.
- It seeds the smallest bundled sample log that meets the highest `min_events` / `min_cases` among the staged modules with your module's `log_model`: `apps/api/tests/fixtures/sample.xes` (9 events, 3 cases), else `simulated_data/input/autonomous/test_preprocessed.csv` (3,989 events, 814 cases); object-centric modules get `apps/api/tests/fixtures/sample.jsonocel`. The log is named `module-dev sample (<file>)` and imported only if it isn't there yet, then the module page URL is printed. If no sample is big enough, it says so and prints no module URL – upload a log in the web UI.
- Editing anything in a staged module (`module.py`, `manifest.yaml`, …) reloads just that module through the platform's hot reload, which watches the folders behind the symlinks; uvicorn restarts only for platform code. A panel edit rebuilds the bundle.
- `make module-dev` first checks that `MODULE` has a `manifest.yaml` (exit 2 with a usage line), then, like `make dev`, frees `PORT` and kills any running `uvicorn mate.api.main`. It refuses to start when `WEB_PORT` is taken.

### Tests

Put pytest tests in `modules/<folder>/tests/`, named after the module (`tests/test_<id>.py`, as `new` does) so two modules' test files never clash when run together. `mate.sdk.testing` builds a real `ModuleContext` over in-memory stand-ins:

```python
# modules/my_module/tests/test_my_module.py
import asyncio
from pathlib import Path

from mate.sdk.testing import build_test_context, load_module, sample_log

MODULE_DIR = Path(__file__).resolve().parents[1]


def test_get_kpis() -> None:
    async def check() -> None:
        module = load_module(MODULE_DIR)
        log = sample_log(rows=1000)
        ctx = await build_test_context(log, module_id=module.id)
        out = await module.get_kpis(ctx)
        assert "throughput" in out

    asyncio.run(check())
```

- **`sample_log(rows=None)`** → `SampleLog(id, frame)`: the bundled log (3 cases, 9 events, `case_id/activity/timestamp/resource`); `rows=N` tiles it to exactly N events. **`load_log(path)`** reads your own `.csv` / `.parquet` with the canonical columns (no XES). Both normalise the frame like the importer: string `case_id` / `activity`, tz-naive UTC timestamps, rows sorted by case, then time.
- **`await build_test_context(log=None, *, log_id=None, module_id="test_module", user_id="test-user", config=None, workdir=None)`** – pass the log or its `log_id`; default is `sample_log()`. `config` feeds `ctx.config`, `workdir` defaults to a fresh temp dir.
- **`ctx.event_log`** serves `pandas()` / `polars()` / `pm4py()` and `duckdb_fetch` over a view named `events`; `events_path` is a real `events.parquet`. `ctx.open_event_log(id)` opens any other test log, but raises `NotImplementedError` when given filters.
- **Recorders:** `ctx.cache` stores values in `.data` the way the platform cache stores them – DataFrames through Parquet, bytes as-is, everything else through JSON – and raises `ValueError` for a key with `/`, `..` or a trailing `.`. `ctx.progress.calls` and `ctx.bus.events` keep every call. `ctx.registry` knows no capabilities (`call` raises `ModuleError`), `ctx.object_log` is `None`, `ctx.run_in_process` runs in a thread (nothing checks that args pickle). Assert on log lines with `structlog.testing.capture_logs()`.
- **`load_module(folder)`** imports `module.py` the way the loader does (same `mate_mod_<id>` namespace, relative imports work), checks the class `id` against the manifest and returns the instance.

`build_test_context` is async; wrap the test body in `asyncio.run` as above, so no pytest plugin is needed. pandas, pyarrow and duckdb come from the platform's `inherit` set, not from `mate-sdk`, so run tests inside the workspace:

```
uv run pytest modules/my_module/tests
```

---

## 11. Distribution

One channel exists: **archive upload** – *Settings → Modules → Import*, i.e. `POST /api/v1/modules/install`, takes a `.zip`, `.tar`, `.tar.gz` or `.tgz` of the module folder (`mate-module pack` builds one, §10; a single wrapper folder inside the archive is fine).

The platform checks `manifest.yaml` and `HOW_TO_USE.md` before writing anything (an upload without a valid guide is rejected, see §2), refuses an id that belongs to a bundled default, unpacks into `data/uploaded_modules/<id>/`, installs the dependencies and mounts the module without a restart. It does not bundle the frontend: a module with a panel or widgets must ship its built `.dist/` in the archive (`mate-module pack` refuses to pack without it). Uploading a new version of an id you alone own replaces it, staged so a broken version never takes the module down:

1. The archive is built and checked in `data/uploaded_modules/.staging/<id>/` – dependencies installed, `module.py` imported in a separate interpreter.
2. Only then is it swapped in; the version it replaces is kept in `data/uploaded_modules/.prev/<id>/`.
3. If the new version fails to load, the previous one is restored and loaded again automatically, and the install job fails with the reason.
4. Each successful install is recorded in `data/uploaded_modules/.history/<id>.json` (`version`, `content_hash`, `installed_at`, `user_id`).

A version that a loaded module's `requirements.modules` specifier excludes is refused before anything is built – the install job fails, or the rollback answers `409`, naming each dependent and its constraint; update the dependents first. The same goes the other way: a version whose own `requirements.modules` specifier excludes the loaded version of the module it needs is refused, naming that requirement and the loaded version – install a matching version of the dependency first. Re-uploading the same code is staged the same way and keeps the older kept version.

If the API dies in the middle of the swap, the next startup repairs it before any module is discovered: a live version left aside in `.staging/<id>.live` goes back to `<id>/` when that is missing (if both exist, it is kept and logged), a previous version a rollback had taken out goes back to `.prev/<id>/`, and everything else in `.staging/` is removed.

The module page's **Versions** card (`GET /api/v1/modules/{id}/versions`) shows the loaded version, that history and the module's `CHANGELOG.md` if it ships one (first 64 KiB, rendered as Markdown). An admin can **Roll back** to the kept previous version there (`POST /api/v1/admin/modules/{id}/rollback`): it is installed the same staged way, and the version it replaces becomes the previous one, so rolling back again goes forward. Rollback also works when the current version failed to load at startup; the versions endpoint then reports the version on disk. Only one previous version is kept, the dot-folders stay out of module discovery, and the last owner's uninstall – or, in S3 mode, the startup cleanup of a module nobody owns – removes them. Bundled modules (`modules/`) are updated with the platform, never by upload or rollback. A new version does not migrate anything: results computed by the old one turn stale and recompute on the next open, and the artifact workspace marks them **older version** with a Re-run.

**Versioning.** One version of a module id is loaded at a time, for everyone – there are no side-by-side versions. Bump `version` with every change to the module folder, by what the change does to the module's contract:

| Bump | When |
| --- | --- |
| major | Something other modules, dashboards or stored results rely on goes away or changes type: a `@route` (method + path), a dataset or its `artifact_type` / `cache_key`, an input port or its type, a `provides` / `consumes` topic, a `config_schema` property or its type, `runtime.kind` / `isolation` – or the module demands more: a new or tighter hard dep, a new required column, a higher `min_events` / `min_cases`, another `log_model`, a new or newly required input port. In `0.x`, a minor bump. |
| minor | Any of those added (a route, dataset, optional input, config property, topic), or a demand relaxed. |
| patch | Everything else – code, docs, tests. |

`make check-module-versions` (`scripts/check-module-versions.py`) computes that for every module folder changed against `origin/main` (`BASE=<ref>` for another) by diffing a contract snapshot of both versions – the manifest, plus the route decorators parsed statically from `module.py` – and fails when the bump is too small or files changed without one. Only `MAJOR.MINOR.PATCH` counts: `1.0.0` → `1.0.0-rc.1` or `1.0.0+b2` is no bump. Defaults are filled in before comparing, so writing one out (`isolation: in_process`, `runtime.kind: python`) changes nothing. A breaking change you'd rather not force on dependents ships as a new module id instead (as `complexity_v2` did), with the old one kept until nobody needs it. Keep a `CHANGELOG.md` in the module folder to tell users what changed.

**Not implemented:** installing from a git URL, or from PyPI / npm through the Import flow. The loader does pick up an already-installed Python package that exposes a `mate.modules` entry point (§9, step 1), but nothing installs one for you.

---

## 12. Author checklist

Before submitting a module:

- [ ] `manifest.yaml` validates (run `uv run python -c "from mate.sdk import Manifest; Manifest.load_yaml('modules/<folder>/manifest.yaml')"`).
- [ ] `version` is bumped for this change as §11 says, and `make check-module-versions` passes; `CHANGELOG.md` says what changed.
- [ ] A hard dep you rely on a specific major of carries a specifier (`discovery>=1,<2`).
- [ ] `HOW_TO_USE.md` exists and passes `uv run python -m mate.sdk.guide modules/<folder>`; every label, default and limit in it was checked against the panel and the code (§2).
- [ ] `README.md`, if you keep one, holds developer notes only; what users need to know lives in the guide.
- [ ] `requirements.event_log.log_model` is set to the model the module targets (`case_centric` or `object_centric`).
- [ ] `id` matches between `manifest.yaml` and `module.py`.
- [ ] Every emitted bus topic is in `provides:`; every subscribed topic is in `consumes:`.
- [ ] Every `ctx.registry.call(...)` target is in `consumes:` or `optional_modules:`.
- [ ] No imports from `apps/api/*` or `apps/web/*` – only `mate.sdk` and `@mate/module-sdk-ts`.
- [ ] Long operations use `@job(progress=True)` (progress is optional but recommended for long jobs).
- [ ] Precompute that must run after another module subscribes to its `<module_id>.completed` event (not a phantom topic nobody emits) and/or lists it in `requirements.modules`; the handler is job-backed (`@on_event` + `@job`) – a plain `@on_event` is never tracked.
- [ ] A precompute handler is idempotent and rebuilds from `ctx.event_log` alone (it also runs on demand, after data changes, and again after an interrupted run is restarted); `precompute: eager` is set only if the module is the first thing people open.
- [ ] Every `config_schema` property the precompute reads is tagged `ui.affects: precompute`; properties that differ per dataset are `ui.scope: dataset`.
- [ ] Sync `def` handlers are fine – don't reach for `asyncio.run` or `loop.run_until_complete`. The SDK auto-wraps.
- [ ] Tests run green against the platform's `inherit` versions.
- [ ] Every graph/diagram view goes through `CanvasShell` (or `CanvasControlCluster` for a non-React-Flow viewer), with all its controls in the `settings` popover and no filter bar above the canvas.
- [ ] Every widget declares `min_px_w`/`min_px_h` measured in the browser, and still reads correctly on a board at exactly that size.
- [ ] Widgets build on the shared kit (`CardShell`/`KpiTile`/`seriesColor`) — no private `_kit.tsx`, no hardcoded chart hex.
- [ ] Clickable marks call `onDrill` with a standard `DRILL_PARAMS` name.
- [ ] Every widget declares `help.what` — a card with no explanation is a card nobody trusts.
- [ ] A widget showing more than one figure declares `kpis:` so a placement can show a subset.
- [ ] A widget exposing knobs its module's panel also has declares `views:`/`config_schema` for them, so the card isn't a stripped-down version of the panel.
- [ ] No platform-level files modified (`apps/api/pyproject.toml`, `apps/web/package.json`, etc.).
- [ ] `.venv/`, `.dist/`, `node_modules/` gitignored.

If all of those hold, dropping the folder into `modules/` and restarting is enough – the module is live.

---

## 13. Writing a module in another language (JVM)

The platform's worker bridge is language-agnostic: any process that speaks the
wire protocol in [`PROTOCOL.md`](./PROTOCOL.md) (newline-delimited JSON-RPC
over a Unix socket + Parquet data handoff) can be a module backend. The first
supported foreign runtime is the **JVM** – the process-mining research
ecosystem (ProM et al.) is Java, and wrapping an existing Java algorithm as a
Mate module is exactly the intended use.

### How it works

- Your manifest declares a `runtime:` block instead of Python deps:

  ```yaml
  runtime:
    kind: jvm
    jar: dist/my-module-all.jar   # folder-relative, self-contained fat jar
    requires-java: 17             # minimum Java feature version (17 = floor)
    jvm-args: ["-Xmx1g"]          # optional JVM flags
  ```

- The module ships as a **self-contained fat jar** (a runnable jar with a
  `Main-Class` bundling every dependency). There is no server-side
  Maven/Gradle resolution – the platform only needs a JRE (bundled in the api
  Docker image; on a bare dev host install Temurin 17+, e.g.
  `brew install --cask temurin@21`). Without a JRE the module is skipped at
  boot with a clear warning and uploads fail with an actionable message.
- Foreign-runtime modules are always process-isolated (the bridge). All the
  [§8 subprocess caveats](#8-dependencies--isolation) apply: ~1–50 ms per
  `ctx.*` call, no typed request bodies on routes (module config is the input
  channel), no `user_id`/`open_event_log`/OCEL on the proxied context, static
  job titles only.
- Frontend panels/widgets/`datasets:` work exactly as for Python modules –
  they never knew what language the backend was.

### The Java SDK

`packages/module-sdk-jvm` (Gradle; build with `make sdk-jvm`, needs JDK 17+)
ships `mate-sdk-jvm-<v>-all.jar` – the only compile-time dependency you need
(Jackson is bundled + relocated, so your own Jackson can't conflict).

```java
public static void main(String[] argv) {
    MateModule.builder("performance_java")
        .onEventJob("compute_on_import", "log.imported",
            JobSpec.of().progress(true).cancellable(true).title("Performance metrics"),
            MyImpl::compute)
        .route("get_kpis", RouteSpec.get("/kpis"), MyImpl::getKpis)
        .build()
        .run(argv);   // connects, handshakes, serves - never returns
}

static Object mine(ModuleContext ctx, CallArgs args) {
    List<List<Object>> rows = ctx.eventLog().duckdbFetch(
        "SELECT case_id, activity FROM events ORDER BY case_id, timestamp",
        List.of());
    ctx.progress().update(0.5, "mining");
    ctx.checkCancelled();                      // throws CancelledException
    Map<String, Object> model = compute(rows);
    ctx.cache().setJson("model", model);       // JSON cache values only
    return model;                              // JSON-representable result
}
```

Context surface: `eventLog()` (`duckdbFetch` runs host-side with zero local
deps; `materialize()` hands you a Parquet path – DuckDB JDBC's
`read_parquet` is the recommended reader, deliberately not an SDK
dependency), `cache()` (JSON envelopes; reading a Python-pickled entry throws
`UnsupportedCacheValueException`), `bus()`, `progress()`, `logger()`,
`registry()`, `config()`, `workdir()`, `checkCancelled()`. Restricted
invocations omit raw-data paths – touching them throws `DataWallException`
(never guess paths).

Cancellation: platform cancel makes every `ctx.*` call throw
`CancelledException` (an `Error`, so a broad `catch (Exception)` can't
swallow it); poll `checkCancelled()` in tight compute loops – after ~3 s of
grace the platform SIGKILLs the worker instead.

The reference module is [`modules/performance_java`](./performance_java/)
(source under `packages/module-sdk-jvm/examples/performance-metrics`); the
cross-runtime conformance suite lives at
`apps/api/tests/test_worker_conformance.py`.

### Author checklist (JVM)

- [ ] Fat jar builds reproducibly (`shadowJar` with `Main-Class`), committed
      or shipped inside the upload archive at the manifest's `runtime.jar`
      path.
- [ ] No `dependencies.python` block (the validator rejects it).
- [ ] Results/cache values are pure JSON; big artefacts go as files under
      `cache().dir()` with JSON metadata.
- [ ] Long jobs tick `progress()` and poll `checkCancelled()`.
- [ ] `duckdbFetch` results stay well under the 256 MiB frame limit –
      aggregate in SQL or use `materialize()`.

### Reserved: Node and R (design, not yet accepted)

The manifest kinds below are **rejected today**; the designs are recorded so
the runtimes can land without protocol changes:

- **Node** – `runtime: {kind: node, entry: dist/worker.mjs, requires-node: ">=20"}`.
  Distribution mirrors the fat-jar philosophy: a single esbuild-bundled,
  self-contained `worker.mjs` (no server-side `npm ci`). Node speaks
  `AF_UNIX` natively (`net.createConnection({path})`); the SDK would be a new
  `packages/module-worker-node` (the existing `module-sdk-ts` stays
  frontend-only). Adds a Node binary to the api image when implemented.
- **R** – `runtime: {kind: r, entry: worker.R, requires-r: ">=4.3", packages: [...]}`
  with `Rscript` installing into a module-local `.rlib` (hash-skipped like
  venvs). Base R has no `AF_UNIX` client, so the R SDK needs `nanonext`/
  `processx` – or protocol v1.1 extends the socket argv with a scheme prefix
  (`unix:` | `tcp:127.0.0.1:<port>`, opt-in localhost listener). Reserved
  extension point; protocol v1 stays Unix-socket-only.
