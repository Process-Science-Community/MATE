# Documentation index

Every markdown doc in the repo, what it covers, and when to read it.

## Root (stay there by convention)

| File | What it is |
| --- | --- |
| [`/README.md`](../README.md) | Entry point: quick start, running modes (`make dev` / `up` / `up DEV=1` / prod overlay), bundled modules, configuration, data layout. Read first. |
| [`/AGENT.md`](../AGENT.md) | Orientation for coding agents: commands, architecture summary, conventions, current direction. |
| [`/CLAUDE.md`](../CLAUDE.md) | Claude Code project memory: imports `AGENT.md` and adds the verification and CI rules. Kept in root because the tool loads it from there. |
| [`/PRODUCT.md`](../PRODUCT.md) | Product definition for design tooling (users, brand personality, design principles, accessibility targets). Read by the `impeccable` design skill's context script — must stay in root. |

`tasks.md` (root) is a gitignored personal scratchpad, not documentation.

## `docs/`

| File | What it is |
| --- | --- |
| [`INSTRUCTIONS.md`](./INSTRUCTIONS.md) | The design spec: tech-stack rationale, storage strategy, repo layout, the full module system (§5), API surface, frontend structure, job/progress architecture. The "why" behind everything; code docstrings cite its section numbers. |
| [`DEPLOY.md`](./DEPLOY.md) | Prod deploy runbook for the uni VM: proxy chain, secrets + realm setup, resource limits, Neo4j sidecar, verify checklist, update procedure, MCP server enablement. |
| [`REVIEWER_ACCESS.md`](./REVIEWER_ACCESS.md) | Anonymous conference-reviewer accounts: login URL, tracking exemption, preloaded sample content, isolation, expiry, VM steps. |
| [`MCP.md`](./MCP.md) | MCP server **consumer** reference: endpoint, auth (PAT/OAuth), scopes, tool catalog, data wall, client setup. Operator-side setup lives in `DEPLOY.md` ("MCP server"). |
| [`ci/README.md`](./ci/README.md) | CI design and verification rules: lanes, the change planner (`scripts/ci/plan.py`), declared test inputs, pyright ratchet, baseline and cost measurements. |
| [`DEV_LITE.md`](./DEV_LITE.md) | Lite dev mode: `make lite` (native, low-memory, `MODULES=`), the module creation flow, debugging recipes, troubleshooting. |
| [`strategy/hub-roadmap.md`](./strategy/hub-roadmap.md) | The "Hugging Face for process science" plan: Hugging Face comparison (publishing vs. sandboxed execution), concept mapping, staged roadmap (module upload off → safe PR flow → sandboxed runner → hub), decisions log, and the open operational items (tracking/privacy, large case-study log). **Start here when continuing that work.** |
| [`strategy/extensibility-strategy.md`](./strategy/extensibility-strategy.md) | Forward plan for polyglot modules, isolation tiers, ProM integration and the MCP server. |
| [`S3_OFFLOAD.md`](./S3_OFFLOAD.md) | S3 storage design doc (all phases implemented): local cache + eviction, bypass-hole closure, migration + quota, hydration performance, and the still-open multi-node seams. Storage code docstrings cite it. |

## The published manual

| File | What it is |
| --- | --- |
| [`landing/docs/content/`](../landing/docs/content/) | The user-facing manual — what this index describes in one line, those chapters explain in full. Eight numbered files, one per sidebar group; run `make docs` after every edit. |
| [`landing/docs/`](../landing/docs/README.md) | The generator (`build.mjs`), the authoring guide with the component reference, and the generated `site/` — never edit `site/` by hand. |

## `modules/`

| File | What it is |
| --- | --- |
| [`modules/README.md`](../modules/README.md) | The module authoring contract: manifest schema, SDK surface, events/capabilities, panels, checklists. Read this to build a module. |
| [`modules/PROTOCOL.md`](../modules/PROTOCOL.md) | Wire protocol between the platform host and non-Python module workers (JVM today; Node/R reserved). |
| [`modules/SIDECAR_SERVICES.md`](../modules/SIDECAR_SERVICES.md) | How a module ships with its own long-running container (compose profiles, e.g. the Neo4j graph sidecar). |

Every module carries a `HOW_TO_USE.md`, its user guide in one fixed format (shown behind the **How to use** button in the app and published in the manual under *Module guides*; the format is in [`modules/README.md`](../modules/README.md) §2). Some modules also keep a `README.md` for developer and provenance notes (e.g. `modules/actor_performance/`).

## Conventions

- Code comments cite docs by bare filename + section (`INSTRUCTIONS.md §5.1`, `DEPLOY.md §4b`, `S3_OFFLOAD.md`) — those files live here in `docs/`, except the `modules/` ones.
- New platform-level docs go in `docs/`; module-authoring docs go in `modules/`; root stays limited to the three convention-pinned files above.
