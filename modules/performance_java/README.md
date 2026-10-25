# Performance Metrics (Java)

Timing metrics for a case-centric event log - and the platform's **reference
JVM module**: the backend is a single self-contained Java jar, no Python code
at all.

## Using it

The user guide is [`HOW_TO_USE.md`](HOW_TO_USE.md): the KPI tiles, the two
rankings, and how dwell time and hand-off wait are measured. The app shows it
behind the **How to use** button on the module page. This README is the
development and provenance notes for the jar.

## Datasets and routes

| Dataset | Route | Shape | Payload |
| --- | --- | --- | --- |
| `kpis` | `/kpis` | `kpi` | A flat map of numbers (`cases`, `events`, `variants`, `cycle_time_*_seconds`, `throughput_cases_per_day`, ...). `processing_time_avg_seconds` and `waiting_time_share` are present only when the log has `end_timestamp`, `resources` only when it has `resource`. |
| `activity-performance` | `/activities` | `table` | One row per activity, ranked by total dwell. |
| `handoff-performance` | `/transitions` | `table` | One row per directly-follows pair, ranked by total wait. |

## How it runs

- `manifest.yaml` declares `runtime: {kind: jvm, jar: dist/performance-metrics-all.jar}`.
- The platform launches the jar as a worker process (`java -Xmx512m -jar ...`)
  speaking the wire protocol in [`modules/PROTOCOL.md`](../PROTOCOL.md).
- The jar registers a job-backed `log.imported` handler (`compute_on_import`).
  Module precompute is lazy: the platform runs it the first time the module is
  opened for a log, and again when the log's data has changed, and the web app's
  run gate shows its progress with a Cancel button. The job computes all three
  result sets (progress + cooperative cancellation throughout) and caches them
  as JSON.
- The routes serve that cache and compute on a miss, so a log whose job has not
  run yet still answers. The cache is scoped per user, log and module and forked
  per filter variant.
- `panel/index.tsx` is the module's own page: a KPI strip plus a ranked
  activities / hand-offs view. Plain TSX like any other module's panel - the
  frontend never knew the backend is a jar.
- The manifest's `datasets:` entries additionally let the platform's generic
  visualizations render the same three results as dashboard cards, with no
  widget code of our own.

## How it reads the log

Everything goes through `ctx.eventLog().duckdbFetch(sql, params)` - the
platform runs the SQL against the filter-applied `events` view and returns
JSON rows. That is the module system's default data path: the jar needs no
Parquet reader, no DuckDB driver, and never sees a local copy of the log.
Every query aggregates in SQL, so what crosses the socket is one row per
activity or hand-off - never one per event - and the result stays far under
the protocol's 256 MiB frame ceiling on any log size. Row output is capped
(500 activities, 300 hand-offs) and the response flags `truncated`.

The alternative path, `ctx.eventLog().materialize()` (a Parquet file on the
shared filesystem, read with DuckDB JDBC), is for algorithms that genuinely
need every row - a miner, not a set of aggregates.

## The jar (provenance)

`dist/performance-metrics-all.jar` is a **committed build artefact** - the api
image ships only a JRE and `modules/` is bind-mounted, so the platform cannot
build it. Source lives in
[`packages/module-sdk-jvm/examples/performance-metrics`](../../packages/module-sdk-jvm/examples/performance-metrics);
rebuild with

```bash
make sdk-jvm   # needs a JDK 17+ on the dev machine
```

which recompiles the SDK + example and copies the fat jar back here. The
conformance suite (`apps/api/tests/test_worker_conformance.py`) exercises the
same SDK internals, so meaningful drift between source and committed jar fails
tests.
