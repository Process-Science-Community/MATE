<!-- Module development — the author contract, from first module to distribution. -->

<!-- group: Module development -->

# Your first module

A module is a folder: a manifest, a handler class, and optionally a frontend. Copy a bundled one, rename it, make it compute something.

## The folder

```tree title="modules/my_module/"
modules/my_module/
├── manifest.yaml           # required – identity, requirements, dependencies, frontend
├── module.py               # required – one Module subclass, instantiated once per process
├── events.py               # recommended – Pydantic payload shapes you emit
├── tests/                  # pytest
├── panel/index.tsx         # the module page entry
├── widgets/*.tsx           # dashboard cards
├── uv.lock                 # commit it – pins your Python dependencies
├── .venv/ · .dist/ · node_modules/   # created by the platform, gitignored
└── README.md               # rendered on the module detail page
```

Commit the manifest, `module.py`, tests, frontend sources, and `uv.lock`. Everything else is rebuilt.

## Fifteen minutes to a running module

:::steps
1. Copy a small, complete example — `modules/performance_over_time` has a panel and a widget.

   ```bash title="Terminal"
   cp -r modules/performance_over_time modules/my_module
   ```

2. Rewrite the identity in `manifest.yaml`: `id`, `name`, `version`, `description`, and shrink `requirements` to what you need. The `id` is lowercase snake_case and globally unique.
3. Rewrite `module.py`: set `id = "my_module"` on the class (it must match the manifest), delete the handlers you do not need, keep one route.
4. Start the platform with hot reload.

   ```bash title="Terminal"
   make dev
   ```

5. Import a log, open the module grid, click your module.
:::

## A minimal module that does real work

```python title="modules/my_module/module.py"
from mate.sdk import Module, ModuleContext, job, on_event, route


class MyModule(Module):
    id = "my_module"  # must equal manifest.id

    async def _compute(self, ctx: ModuleContext) -> dict:
        async with ctx.event_log as log:
            rows = await log.duckdb_fetch(
                "SELECT activity, count(*) AS events FROM events GROUP BY 1 ORDER BY 2 DESC LIMIT 10"
            )
        return {"top_activities": [{"activity": a, "events": n} for a, n in rows]}

    @route.get("/top-activities")
    async def top_activities(self, ctx: ModuleContext) -> dict:
        cached = await ctx.cache.get("top_activities")
        if cached is not None:
            return cached
        result = await self._compute(ctx)
        await ctx.cache.set("top_activities", result)
        return result

    @on_event("log.imported")
    @job(progress=True, title="My Module - precompute")
    async def precompute(self, ctx: ModuleContext, payload: dict) -> None:
        await ctx.progress.update(0.1, "Loading log")
        await ctx.cache.set("top_activities", await self._compute(ctx))
        await ctx.progress.update(1.0, "Done")
```

```yaml title="modules/my_module/manifest.yaml"
id: my_module
name: My Module
version: 0.1.0
category: attribute
description: Lists the most frequent activities in the log.
license: MIT

requirements:
  event_log:
    log_model: case_centric
    required_columns: [case_id, activity, timestamp]
    min_events: 1

provides: [my_module.top_activities]
consumes: [log.imported]

dependencies:
  python:
    requires-python: ">=3.12"
    inherit: [pandas]
```

| Piece | Effect |
| --- | --- |
| `id` in both files | The loader matches them; a mismatch fails the load with a clear error. |
| `@route.get` | Registered under `/api/v1/modules/my_module/top-activities`, with auth and OpenAPI types handled. |
| `@job` under `@on_event` | The precompute becomes a job: it counts towards the log's readiness and appears in the jobs UI. |
| `ctx.cache` | Results stored per `(log_id, module_id)`, surviving restarts. |
| `requirements.event_log` | The card is only offered on case-centric logs with those columns. |

## What the platform does at startup

:::steps
1. **Discovery** — the folder is found by scanning `modules/*/manifest.yaml`, or from a `mate.modules` entry point.
2. **Validation** — the manifest is parsed and the dependency graph built; cycles or missing hard dependencies stop the startup.
3. **Materialisation** — the dependency block is hashed; if it changed, `uv venv` + `uv pip install` runs in the folder and the frontend is bundled into `.dist/`.
4. **Import and instantiate** — `module.py` is imported under a private namespace with the module's own `site-packages`; your class is constructed exactly once per process.
5. **Binding** — routes mounted, event handlers subscribed, job handlers registered, capabilities added to the registry.
:::

## The development loop

| Task | How |
| --- | --- |
| Change a handler | Save; in `ENV=dev` the watchdog reloads the module in place. |
| Change requirements or dependencies | Save; gating re-evaluates, and a changed dependency block triggers `uv sync` for that module. |
| Change the frontend | The web dev server watches `modules/**` and rebuilds the bundle; hard-reload the page. |
| Debug a load failure | API log for the traceback, then the module detail page for its log tail. |

## Rules

- Do not import from `apps/api/*` or `apps/web/*` — the SDK is the contract.
- Do not construct log paths yourself; the ones from `ctx` are ownership-checked.
- Do not keep state on `self`; the instance is shared and jobs run concurrently.
- Do not call `asyncio.run` inside a handler.

# The manifest

`manifest.yaml` is the registration, the contract, and the documentation of a module. The SDK validates it locally; the loader validates it again at startup.

```yaml title="manifest.yaml"
id: my_module                       # lowercase snake_case, globally unique
name: My Module
version: 0.1.0
category: advanced                  # foundation | attribute | external_input | advanced | comparison | other
description: One line, shown on the module card.
about: >-                           # 2-4 sentences for "About this module"
  What the module does and when it is worth opening.
license: MIT
keywords: [conformance check, playback]   # helps MATE AI route chat to this module
default_enabled: true
isConfidentialSafe: true            # true only if the log never leaves the host

requirements:
  event_log:
    log_model: case_centric         # case_centric (default) | object_centric
    required_columns: [case_id, activity, timestamp]
    optional_columns: [resource, end_timestamp]
    min_events: 100
    min_cases: 5
  modules: [discovery]              # hard dependencies
  optional_modules:
    - id: performance
      reason: Cycle times come from performance when it is installed.

provides: [my_module.metrics]       # capabilities and topics you publish
consumes: [log.imported, discovery.completed]

dependencies:
  python:
    requires-python: ">=3.12"
    packages: ["scikit-learn>=1.5"] # private to this module
    inherit: [pm4py, pandas]        # reuse the platform's copies
    isolation: in_process           # in_process | subprocess
    execution: thread               # thread | worker

runtime:                            # JVM modules instead of dependencies.python
  kind: jvm
  jar: dist/my-module-all.jar
  requires-java: 17
  jvm-args: ["-Xmx1g"]

frontend:
  panel: ./panel/index.tsx
  panel_help: { what: …, read: …, computed: … }
  log_filter: true
  widgets: [...]                    # see the frontend chapter

datasets:
  - id: ranking
    title: Activity ranking
    shape: table                    # table | graph | kpi | tree | blob
    route: /ranking

ai_models:                          # optional generated settings card
  llm: { title: LLM, description: Used to summarise results. }
model_store: { title: Model files, config_key: model }

source:                             # citations, max 20
  - title: A Great Paper
    fullCitation: >-
      J. Doe and J. Smith, "A great paper," in 2024 6th International Conference
      on Process Mining (ICPM), Aachen, Germany, 2024, pp. 1-8
    url: https://doi.org/10.1109/xxxx
artifacts:                          # other links, max 20
  - { name: Reference implementation, url: https://github.com/janedoe/great-miner }

config_schema:                      # renders the per-user settings form
  properties:
    threshold: { type: number, title: Drift threshold, minimum: 0, maximum: 1, default: 0.25 }
```

## Field reference

| Field | Notes |
| --- | --- |
| `id` | Lowercase snake_case, globally unique, must match the class attribute in `module.py`. |
| `category` | Where the card sits in the grid: `foundation`, `attribute`, `external_input`, `advanced`, `comparison`, `other`. |
| `about` | Longer plain-language text for the info box; falls back to `description`. |
| `requirements.event_log` | The availability gate: log model, columns, minimum events and cases. |
| `requirements.modules` / `optional_modules` | Hard and soft module dependencies. Cycles abort the startup. |
| `provides` / `consumes` | The contract you publish and rely on; validated at boot. |
| `default_enabled` | Whether new accounts get the module enabled. |
| `isConfidentialSafe` | Set `true` only when the log never leaves the host. |
| `source` / `artifacts` | Citations and links, at most 20 each. |
| `config_schema` | JSON-Schema-flavoured; the platform renders the form. |
| `ai_models` / `model_store` | Opt into generated AI-model selectors or a model-file upload card. |

## Rules that bite

- A package cannot be in both `packages` and `inherit`.
- Declaring `author`, `authors`, `paper_url`, or `papers` is a hard error — credit belongs in `source[].fullCitation`, in IEEE style with the DOI omitted (the DOI goes in `url`).
- A foreign `runtime` must not declare `dependencies.python`, and its `jar` must be folder-relative.
- `log_model` is the single switch between the two log worlds: `case_centric` reads `ctx.event_log`, `object_centric` reads `ctx.object_log`, and a module never appears on the wrong kind of log.

```bash title="Terminal"
# validate before the platform does
uv run python -c "from mate.sdk import Manifest; Manifest.load_yaml('modules/my_module/manifest.yaml')"
```

# Handlers and the context

`module.py` holds one `Module` subclass with three kinds of handler, all receiving an injected `ModuleContext`.

## The three handler kinds

| Decorator | Registers | Notes |
| --- | --- | --- |
| `@route.get("/path")` | An HTTP route under `/api/v1/modules/{id}/*` | `route.get/post/put/patch/delete`; FastAPI semantics for path parameters and Pydantic bodies; `name=` sets the OpenAPI operation id, `response_model=` the shape. |
| `@on_event("topic")` | A bus subscription | Dotted topics; wildcards such as `log.*` match. Declare it in `consumes:`. |
| `@job(...)` | Makes the handler a persisted job | Stack it under a route or event handler. A route answers with `{"job_id": …}` immediately. |

| `@job` parameter | Default | Effect |
| --- | --- | --- |
| `progress` | `False` | Enables `ctx.progress.update(...)` streaming. |
| `title` / `subtitle` | derived | Toast and drawer text; a `(ctx, payload) -> str` callable works in-process. |
| `priority` | `0` | Higher is scheduled sooner. |
| `cancellable` | `True` | Shows the cancel affordance. |
| `result_url` | `None` | URL template for the toast's *Open* action. |

Handlers may be `async def` or plain `def`; sync handlers are wrapped so they cannot block the event loop (routes ride FastAPI's thread pool, event and job handlers run through `asyncio.to_thread`).

## The context

Every member is a Protocol — depend on the contract, not an implementation.

| Member | Purpose |
| --- | --- |
| `ctx.log_id` · `ctx.module_id` · `ctx.user_id` | Scope of this invocation; `log_id` is empty for global routes. |
| `ctx.event_log` · `ctx.object_log` | Lazy views of the log; the object-centric one is bound only for `object_centric` modules. |
| `ctx.open_event_log(log_id, filters)` | A second, ownership-checked log — for comparisons. |
| `ctx.cache` | Per-`(log_id, module_id)` results: `get`, `set`, `exists`, `delete`. |
| `ctx.config` | The user's configuration, validated against your schema; read it defensively (`ctx.config.get(key, default)`). |
| `ctx.progress` | `update(fraction, stage)`, `update(current=…, total=…, stage=…)`, or a running counter. |
| `ctx.bus` · `ctx.registry` | Emit topics; call other modules' capabilities. |
| `ctx.logger` | Structured logging bound with module and log ids. `print()` is dropped. |
| `ctx.workdir` | Scratch space, removed after the invocation. |
| `ctx.run_in_process(fn, …)` | Offload CPU-bound work to the platform's process pool. |
| `ctx.is_cancelled()` · `await ctx.check_cancelled()` | Cooperative cancellation. |

```python title="Cancellation and progress in a long loop"
for i, chunk in enumerate(chunks):
    await ctx.check_cancelled()          # raises mate.sdk.Cancelled
    process(chunk)
    await ctx.progress.update(current=i + 1, total=len(chunks), stage="slices")
```

`Cancelled` derives from `BaseException` on purpose, so a broad `except Exception` cannot swallow a cancellation request. Progress is optional but a job that runs for minutes should emit something — the UI flags a silent job as stalled after three minutes.

## Configuration

`config_schema` is JSON-Schema-flavoured with a `ui` hint the form renderer understands: `enum` becomes a select, bounded numbers a slider, booleans a switch, arrays repeating inputs, and `ui: { widget: textarea }` a text area. Values are per user and per module, so a fresh account has none — always pass defaults to `ctx.config.get`. Configuration changes do not invalidate caches automatically: delete the keys whose values they affect.

```python title="Invalidating on a configuration change"
@route.put("/config-hook")
async def on_config_changed(self, ctx: ModuleContext) -> dict:
    await ctx.cache.delete("ranking")
    return {"invalidated": ["ranking"]}
```

# Logs, jobs and results

How to read the log, how long work is declared, and where results live.

## Reading the log

| View | Returns | Cost | Use it for |
| --- | --- | --- | --- |
| `await log.duckdb_fetch(sql, params)` | `list[tuple]` | lowest | Aggregations, joins, window functions — the default. |
| `await log.pandas()` · `.polars()` | DataFrame | medium | Library algorithms that expect one. |
| `await log.pm4py()` | pm4py event log | highest | Only when an algorithm insists on it. |

```python title="Read through the context manager"
async with ctx.event_log as log:
    rows = await log.duckdb_fetch(
        "SELECT activity, count(*) FROM events GROUP BY 1 ORDER BY 2 DESC LIMIT 20"
    )
    df = await log.pandas()
    path = log.events_path       # the Parquet file itself
    filters = log.active_filter  # what the user committed
```

| Relation | Contents |
| --- | --- |
| `events` | The event table **with the committed filter applied** — what the user sees. |
| `events_src` | The unfiltered table, for comparisons against the whole log. |
| `cases` | Per-case aggregates: event count, start, end, duration. |

Columns are the mapped roles plus every other column of the source file, with types coerced at import. `log.column_specs()` and `log.data_quality()` let a module adapt to what is actually there instead of assuming.

> [!TIP]
> Iterating a DataFrame row by row is the most common performance mistake in module code. Aggregate in DuckDB; if you need per-row work, push the function through `ctx.run_in_process(...)`.

For object-centric logs, `ctx.object_log` exposes `events_pandas()`, `objects_pandas()`, `relations_pandas()`, `o2o_pandas()`, and `ocel()`, backed by the relations `ocel_events`, `ocel_objects`, `ocel_relations`, `ocel_o2o`.

## Precompute, jobs and progress

```python title="The canonical precompute handler"
@on_event("log.imported")
@job(progress=True, title="My Module - precompute")
async def precompute(self, ctx: ModuleContext, payload: dict) -> None:
    await ctx.progress.update(0.05, "Loading log")
    async with ctx.event_log as log:
        df = await log.pandas()
    await ctx.progress.update(0.35, "Computing")
    result = await ctx.run_in_process(self._heavy, df)
    await ctx.progress.update(0.9, "Caching")
    await ctx.cache.set("result", result)
    await ctx.progress.update(1.0, "Done")
```

- Only job-backed handlers participate in the readiness gate. A handler without `@job` is fire-and-forget: no row, no gating, no plan entry.
- Precompute jobs of one import run in parallel up to the worker limit, so keep memory bounded and never assume you are alone.
- To run after another module, subscribe to `<module_id>.completed` — see [Module communication](module-communication.html).

## Results

| Output | Where |
| --- | --- |
| Structured result (KPIs, rankings, summaries) | `ctx.cache` as JSON — readable by routes, datasets, and MCP tools. |
| Tabular result | `ctx.cache` as a DataFrame (stored as Parquet). |
| Large binary or model artifact | `ctx.cache` as bytes; `ctx.workdir` only for scratch. |
| Transient per-request computation | Nothing — compute and return. |

```python title="A cache key that encodes what changes the answer"
key = f"ranking:top_n={top_n}:threshold={threshold:.3f}"
if await ctx.cache.exists(key):
    return await ctx.cache.get(key)
```

Invalidate on the events that change the answer: a configuration write, a re-import, a completed upstream module.

# Module communication

Modules never import each other. They talk over two declared mechanisms, and they order themselves through one reserved topic.

## Declare first

```yaml title="manifest.yaml"
provides: [my_module.metrics, my_module.analysis.completed]
consumes: [log.imported, discovery.completed]
optional_modules:
  - id: performance
    reason: Uses cycle times when performance is installed.
```

The platform validates these at startup, uses them to compute availability, and builds the precompute closure from them.

## Event bus — fire-and-forget

```python title="Emitting and reacting"
await ctx.bus.emit(
    "my_module.analysis.completed",
    {"user_id": ctx.user_id, "log_id": ctx.log_id, "summary": summary},
)


@on_event("my_module.analysis.completed")
async def react(self, ctx: ModuleContext, payload: dict) -> None:
    ctx.logger.info("upstream_finished", summary=payload["summary"])
```

Include `user_id` on every user-scoped event; keep payloads small and JSON-serialisable; never rely on ordering between topics.

## Capability registry — request/response

```python title="Calling a capability, optionally"
if ctx.registry.has("performance.kpis"):
    kpis = await ctx.registry.call("performance.kpis", log_id=ctx.log_id)
else:
    ctx.logger.warning("performance_missing", fallback="computing locally")
    kpis = await self._fallback_kpis(ctx)
```

Capabilities you publish go in `provides`; capabilities you call must be in `consumes` (hard) or `optional_modules` (soft). The platform refuses to mount a module that calls an undeclared capability. Guard optional calls with `has()`, and declare the dependency so the card reads *Limited* instead of failing.

## Ordering

```python title="Running after another module"
@on_event("discovery.completed")
@job(progress=True, title="My overlay")
async def overlay(self, ctx: ModuleContext, payload: dict) -> None:
    # discovery's precompute succeeded, so its cache entries are present
    ...
```

- The platform emits `<module_id>.completed` when a module's precompute job **succeeds**; a failure or cancellation emits nothing.
- Dependents are skipped rather than stranded, so the log always reaches `ready`.
- Steps are ordered topologically, then alphabetically by module id within a layer — deterministic enough to describe in progress copy.

| Need | Mechanism |
| --- | --- |
| "Something happened, react if you care" | Bus topic. |
| "I need a value from another module" | Capability call. |
| "Run after module X" | `@on_event("X.completed")` + `@job`. |
| "Reuse another module's widget" | `useWidget(moduleId, widgetId)` in the frontend. |

# Frontend: panels, widgets and datasets

Frontends are TypeScript regardless of the backend language. The platform bundles them with esbuild into `.dist/` at startup; the Next.js build never sees your sources, and a panel may only import the packages listed in `apps/web/lib/runtime-externals.json`.

## The panel

```tsx title="modules/my_module/panel/index.tsx"
import type { ModulePanelProps } from "@mate/module-sdk-ts";
import { CardShell, KpiGrid, KpiTile, seriesColor } from "@mate/module-sdk-ts";

export default function Panel({ logId, moduleId }: ModulePanelProps) {
  const { data, isLoading, isError } = useModuleData(moduleId, logId);
  return (
    <CardShell loading={isLoading} error={isError} empty={!data}>
      <KpiGrid>
        {data?.kpis.map((kpi, i) => (
          <KpiTile key={kpi.id} title={kpi.title} value={kpi.value} accent={seriesColor(i)} />
        ))}
      </KpiGrid>
    </CardShell>
  );
}
```

| Import | Use it for |
| --- | --- |
| `CardShell`, `CardSection`, `CardEmpty`, `CardError`, `InfoHint` | The frame: loading, empty, and error states, plus scrolling. Never add your own scroll container inside it. |
| `KpiTile`, `KpiGrid` | Headline figures with consistent typography. |
| `seriesColor(i)`, `sequentialScale(t)`, `divergingScale`, `statusColor(role)`, `CHART_CHROME` | Colour by job, not by look. |
| `variantHref(logId, id)`, `activityHref(logId, name)`, `useDrillParams()`, `DRILL_PARAMS` | Links into the process page, encoded once. |
| `api`, `ApiError`, `subscribeBus`, `subscribeJob` | Fetching with the session attached, and live updates. |
| `useWidget(moduleId, widgetId)` | Embed another module's widget, lazily. |

```tsx title="Links and live updates"
<Link href={variantHref(logId, variant.id)}>{variant.label}</Link>

const kpis = await api.get(`/api/v1/modules/${moduleId}/kpis?log_id=${logId}`);
subscribeBus(["my_module.analysis.completed"], () => refetch());
```

## Widgets

```yaml title="A widget declaration"
frontend:
  widgets:
    - id: bottlenecks
      entry: ./widgets/Bottlenecks.tsx
      title: Bottlenecks
      description: Slowest activities by median duration.   # palette blurb
      icon: Timer
      default_w: 4        # grid units on the 12-column board
      default_h: 9
      min_w: 3
      min_h: 6
      min_px_w: 320       # pixel floors – the real minimums
      min_px_h: 240
      resizable: true     # false locks the card to default_w/default_h
      log_models: [case_centric]
      help: { what: …, read: …, computed: … }
      config_schema: { properties: { top_n: { type: integer, default: 8 } } }
      kpis: [{ id: median, title: Median duration }]
      views: [{ id: bars, title: Bar chart, exposes: [top_n] }]
      drill: { module_id: performance, params: { view: bottlenecks } }
```

Sizing is the field to get right. Grid units keep the layout tidy but are relative to the board's width; the pixel floors are what actually keep a card readable. Measure them in the browser at exactly that size.

```tsx title="A card body"
export default function Bottlenecks({ logId, config, onDrill }: WidgetProps) {
  const scope = useCardScope();          // board filters and time range
  const { data, isLoading, isError } = useBottlenecks(logId, {
    topN: config?.top_n ?? 8,
    scope,
  });

  return (
    <CardShell loading={isLoading} error={isError} empty={!data?.items.length}>
      <BarChart
        data={data?.items ?? []}
        fill={seriesColor(0)}
        onClick={(bar) => onDrill?.({ params: { activity: bar.activity } })}
      />
    </CardShell>
  );
}
```

`onDrill` is `undefined` when a panel embeds the widget, so always call it optionally. Colour rules: one series means one colour, never shade a bar by its own length, fold a long tail into "Other", gridlines are solid hairlines, and never build a dual-axis chart.

## Canvases

Graphs, maps, and diagrams render through `CanvasShell`, which provides the dotted grid, the minimap, the initial fit, the drag guard, and pseudo-fullscreen. Every control belongs in its settings popover (built from the `CanvasSetting*` primitives), expensive settings commit on release (`onCommit`), and a busy canvas keeps its last result instead of unmounting to show a spinner.

## Datasets

When the output is data rather than pixels, declare a dataset and skip React entirely:

```yaml title="A dataset declaration"
datasets:
  - id: ranking
    title: Activity ranking
    shape: table            # table | graph | kpi | tree | blob
    route: /ranking         # one of your @route paths
    params_schema: { properties: { top_n: { type: integer, default: 10 } } }
```

```python title="Serving it"
@route.get("/ranking")
async def ranking(self, ctx: ModuleContext, top_n: int = 10) -> dict:
    payload = {
        "shape": "table",
        "columns": [
            {"key": "activity", "title": "Activity", "type": "string"},
            {"key": "events", "title": "Events", "type": "number"},
        ],
        "rows": [...],
    }
    await ctx.cache.set(f"ranking:{top_n}", payload)
    return payload
```

| Use a dataset when | Use a widget when |
| --- | --- |
| The result is tabular, a graph, or figures | The visual is the point |
| You want the platform's table, pagination, and export | You need bespoke layout or interaction |
| The same data should be readable by MCP tools | You are embedding another module's widget |

# Dependencies, isolation and runtimes

Where your code runs, and which libraries it can see.

## Declaring dependencies

```yaml title="dependencies block"
dependencies:
  python:
    requires-python: ">=3.12"
    packages: ["scikit-learn>=1.5"]   # private to your module, installed into .venv
    inherit: [pm4py, pandas, numpy]   # reuse the platform's installed copies
    isolation: in_process             # in_process (default) | subprocess
    execution: thread                 # thread (default) | worker
  npm: ["d3-sankey@^0.12"]            # bundled with your frontend
```

| Field | Meaning |
| --- | --- |
| `packages` | Installed into `modules/<folder>/.venv`; other modules cannot see them. |
| `inherit` | Names the platform already ships — avoids reinstalling hundreds of megabytes per module. A package may not appear in both lists. |
| `requires-python` | A validation gate for `in_process`, an interpreter selector for `subprocess`. |
| `execution` | In-process only: `thread` (fast, cooperative cancel) or `worker` (killable child process). |

Each environment is hashed: unchanged dependencies are skipped on boot, and for `in_process` modules the platform's `major.minor` Python version is part of the hash, so an interpreter upgrade rebuilds automatically. A private import namespace puts your `.venv/site-packages` first, so your versions win and other modules' packages stay invisible.

> [!NOTE]
> In-process modules are ABI-locked to the platform's interpreter (currently 3.12). Never pin an upper bound to dodge an ABI mismatch — use `subprocess` when you genuinely need another interpreter.

## Choosing a mode

| Situation | Mode |
| --- | --- |
| Pure Python, finishes quickly | `in_process` + `thread` (default). |
| Long native call that ignores cancellation | `in_process` + `execution: worker`. |
| Another Python version, or a native-library conflict | `isolation: subprocess`. |
| A Java library | `runtime: { kind: jvm }`. |
| A method that needs its own server | A sidecar service. |

**Subprocess** modules run in a long-lived worker on their own interpreter, with every `ctx` call proxied over a Unix-socket JSON-RPC connection and DataFrames handed over as Parquet. Each context touch costs roughly 1–50 ms, so batch: one aggregated `duckdb_fetch` beats a thousand calls. Cancellation is a three-second soft window followed by `SIGKILL` of the process group; a crashed worker respawns with exponential backoff, and a crash loop becomes a terminal error rather than a hot loop.

**JVM** modules ship as one self-contained fat jar with a `Main-Class` and speak the same protocol, so the SDK surface is equivalent:

```java title="A JVM module entry point"
public static void main(String[] argv) {
    MateModule.builder("my_jvm_module")
        .onEventJob("precompute", "log.imported",
            JobSpec.of().progress(true).cancellable(true).title("My JVM analysis"),
            MyImpl::precompute)
        .route("get_ranking", RouteSpec.get("/ranking"), MyImpl::getRanking)
        .build()
        .run(argv);   // connects, handshakes, serves - never returns
}
```

The context offers `eventLog()` (with `duckdbFetch` running host-side and `materialize()` returning a Parquet path), `cache()`, `bus()`, `registry()`, `progress()`, `logger()`, `config()`, `workdir()`, and `checkCancelled()`. Build with `make sdk-jvm`; `modules/performance_java` is the reference. There is no server-side Maven or Gradle resolution — the jar must bundle everything but the JRE.

**Sidecar services** (a graph database, a search engine, a solver) follow a fixed contract: declared under an optional compose profile the operator starts, bound to loopback, addressed through a config → environment → default chain, probed with a health check that produces an actionable error, wiped before and after each run, and never treated as a store — results go to `ctx.cache`. Full rules: [`modules/SIDECAR_SERVICES.md`](https://github.com/Process-Science-Community/MATE/blob/main/modules/SIDECAR_SERVICES.md).

# Testing and publishing

Module tests are ordinary pytest. The SDK ships no test double on purpose: the context is defined by Protocols, so you fake exactly what a handler touches.

## A unit test

```python title="modules/my_module/tests/test_ranking.py"
import asyncio
import pandas as pd

from modules.my_module.module import MyModule


class FakeEventLog:
    def __init__(self, rows):
        self._rows = rows

    async def __aenter__(self):
        return self

    async def __aexit__(self, *exc):
        return None

    async def duckdb_fetch(self, sql, params=None):
        return self._rows


class FakeCache:
    def __init__(self):
        self.values = {}

    async def get(self, key):
        return self.values.get(key)

    async def set(self, key, value):
        self.values[key] = value


class FakeContext:
    log_id, module_id, user_id = "log-1", "my_module", "user-1"

    def __init__(self, rows):
        self.event_log = FakeEventLog(rows)
        self.cache = FakeCache()


def test_ranking_is_sorted_descending():
    ctx = FakeContext([("a", 10), ("b", 4)])
    out = asyncio.run(MyModule().ranking(ctx))
    assert [row["activity"] for row in out["rows"]] == ["a", "b"]
```

Mirror the Protocol signatures from `mate.sdk.context` so a fake cannot drift from the real contract, and name tests as expectations.

| Layer | Test with |
| --- | --- |
| Pure computation | Plain functions — fast, and where the subtle bugs live. |
| Handler wiring | Fakes for the two or three context members it uses. |
| Dataset payloads | Shape assertions against the declared `shape`. |
| Frontend | A manual pass: loading, empty, and error states; live updates; dark mode and compact density; the card at exactly `min_px_w` × `min_px_h`. |

```bash title="Terminal"
uv run pytest modules/my_module/tests -v         # your module
make test                                        # the platform suite, incl. module fixtures
uv run pyright                                   # strict types
```

```yaml title="CI for a module"
- uses: astral-sh/setup-uv@v5
- run: uv sync --extra dev
- run: uv run python -c "from mate.sdk import Manifest; Manifest.load_yaml('modules/my_module/manifest.yaml')"
- run: uv run pytest modules/my_module/tests
```

Manifest validation first is deliberate: it is the cheapest check and the most common failure.

## Distribution

| Channel | Input | Best for |
| --- | --- | --- |
| Upload | A `.zip`/`.tar.gz` of the folder | Private modules, one-off installs, air-gapped hosts. |
| Git URL | `{ url, ref? }` | Modules under active development. |
| Registry | `{ source: "pypi", id, version? }` | Published modules; nothing lands in `modules/`. |

All three run as jobs and roll back cleanly on failure. Package an archive with the manifest, `module.py`, tests, and frontend sources — exclude `.venv/`, `.dist/`, `node_modules/`, and large model files (those belong in `model_store`).

## Versioning

Bump `version:` whenever results change, not only when code changes. A results-affecting change should invalidate caches — version your cache keys or delete them on first run. Renaming a `config_schema` property orphans stored values, so add the new name and read both for one release.

## Author checklist

- [ ] `Manifest.load_yaml(...)` passes, and `id` matches `module.py`.
- [ ] `log_model` matches the log the module can actually handle.
- [ ] Every emitted topic is in `provides:`; every subscription and capability call is in `consumes:` or `optional_modules:`.
- [ ] Precompute ordering uses `<module_id>.completed`, not hope.
- [ ] Long jobs use `@job(progress=True)` and check cancellation at loop boundaries.
- [ ] Results live in `ctx.cache` under keys that encode the parameters that change them.
- [ ] No imports from `apps/api/*` or `apps/web/*`.
- [ ] Widgets use the shared kit and declare `min_px_w`/`min_px_h` measured in a browser.
- [ ] `.venv/`, `.dist/`, `node_modules/` are gitignored; `uv.lock` is committed.
- [ ] The README states what the module computes and any external dependency.

Symptoms and fixes for everything that still goes wrong are in [Troubleshooting](troubleshooting.html).
