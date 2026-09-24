<!-- Project — contributing to MATE itself. -->

<!-- group: Project -->

# Contributing and development
<!-- slug: contributing -->

Four contribution paths: ship a module, report a bug, improve this manual, or work on the platform.

## Contribution paths

| Path | Start with |
| --- | --- |
| **Ship a module** — the highest-impact contribution | [Your first module](your-first-module.html), then the [author checklist](testing-and-publishing.html#author-checklist). Your citation travels with the manifest, so users always see whose method they are running. |
| **Report a bug or request a feature** | [github.com/Process-Science-Community/MATE/issues](https://github.com/Process-Science-Community/MATE/issues). Attach the diagnostics blob from Settings → About, plus the steps and the expected result. A minimal reproduction beats a long description. |
| **Improve this documentation** | Edit the markdown under `landing/docs/content/`, run `make docs`, open a pull request. The authoring syntax is documented in [`landing/docs/README.md`](https://github.com/Process-Science-Community/MATE/blob/main/landing/docs/README.md). |
| **Work on the platform** | [How MATE works](architecture.html) for the mechanisms, then the setup below. |

## Toolchains

| Tool | Version | Used for |
| --- | --- | --- |
| Python | 3.12 (pinned by `.python-version`) | API, Python SDK, module backends |
| uv | current | Dependency resolution and virtual environments |
| Node.js + pnpm | 20+ / current | Web app, TypeScript SDK, docs build |
| Docker + Compose v2 | current | The full stack |
| JDK 17+ | optional | JVM module SDK and its conformance tests |

```bash title="First run"
git clone https://github.com/Process-Science-Community/MATE.git mate
cd mate
make install       # uv sync --extra dev + pnpm install
make dev           # alembic upgrade head, then uvicorn --reload and next dev
```

`make dev` runs a preflight that frees port `8000` and clears stale processes, applies migrations, then starts both servers. A host API defaults to SQLite under `data/`.

## Development loop

| Task | Command |
| --- | --- |
| Both servers with reload | `make dev` (or `make dev-api`, `make dev-web`) |
| Both servers in containers with reload | `make up-dev` |
| API tests | `make test` |
| One test | `uv run --extra dev pytest apps/api/tests/test_x.py::test_y -v` |
| One module's tests | `uv run pytest modules/<folder>/tests` |
| Python type check | `uv run pyright` (strict) |
| Web type check | `make typecheck` |
| Format Python | `make fmt` |
| Regenerate API types | `make codegen` (API on `:8000`) |
| Rebuild this manual | `make docs` |
| Wipe local state | `make clean` (irrevocable) |

## Migrations

```bash title="Adding a migration"
cd apps/api && uv run alembic revision -m "add watched folder retry counter"
# edit apps/api/alembic/versions/<rev>.py — additive only
# make dev applies it on boot
```

Never edit a migration that has been applied anywhere, keep DDL additive (add nullable columns, backfill, then tighten), and remember that rolling back code does not roll back the schema.

## Testing approach

| Layer | Approach |
| --- | --- |
| Routes | `httpx.ASGITransport` against the app, with a scoped SQLite database per session. |
| Modules | The real loader with fixture module folders, plus Protocol-faithful fakes for unit tests. |
| Isolation | Conformance tests that run the same expectations against Python and JVM workers. |
| AI and MCP | Tests that assert the data wall by attempting to read rows through an AI-scoped context. |
| Storage | Quota, eviction, migration, and snapshot tests against a fake S3 layer. |

Two testing conventions: name each test as an expectation (`test_returns_404_for_another_users_log`), and write the failing test before fixing a bug.

## Enforced conventions

| Convention | Why |
| --- | --- |
| Modules never import platform internals | The SDK is the contract; anything else breaks on upgrade. |
| Bus events carry `user_id` | Server-side fan-out filters on it; omitting it leaks events. |
| Analytics routes avoid trigger words (`/usage`, `/sync`, `/insights`) | Ad blockers break otherwise working pages. |
| Mutations to existing resources are optimistic in the web app | Snapshot, write the expected state, roll back on error, reconcile on settle. |
| Account-scoped flags are server state, not `localStorage` | They must follow the account across browsers. |
| Module frontends import only declared externals | Anything else breaks the esbuild bundle. |
| Code comments cite the design document by section (`INSTRUCTIONS.md §5.4`) | The reason stays one hop from the mechanism. |

## Pull requests

- The suite passes: `make test`, `uv run pyright`, `make typecheck`.
- New behaviour has a test that would fail without it.
- Route or schema changes are followed by `make codegen`, with the regenerated types committed.
- Documentation that is now wrong is fixed in the same pull request.
- The description states the problem first, not the files touched.

## Design documents

The manual explains how to use and extend MATE; these explain why it is built the way it is.

| Document | Contents |
| --- | --- |
| [`docs/INSTRUCTIONS.md`](https://github.com/Process-Science-Community/MATE/blob/main/docs/INSTRUCTIONS.md) | The platform specification: principles, tech stack, storage strategy, the module system (§5), API surface (§6), frontend (§7), jobs and progress (§8). |
| [`docs/DEPLOY.md`](https://github.com/Process-Science-Community/MATE/blob/main/docs/DEPLOY.md) | The production runbook in full: proxy chain, secrets and realm, resource limits, verification, MCP enablement. |
| [`docs/MCP.md`](https://github.com/Process-Science-Community/MATE/blob/main/docs/MCP.md) | The MCP consumer reference in depth. |
| [`docs/S3_OFFLOAD.md`](https://github.com/Process-Science-Community/MATE/blob/main/docs/S3_OFFLOAD.md) | The S3 design: cache and eviction, migration and quota, the still-open multi-node seams. |
| [`modules/PROTOCOL.md`](https://github.com/Process-Science-Community/MATE/blob/main/modules/PROTOCOL.md) · [`modules/SIDECAR_SERVICES.md`](https://github.com/Process-Science-Community/MATE/blob/main/modules/SIDECAR_SERVICES.md) | The worker wire protocol, and the contract for modules with their own server. |
| [`docs/README.md`](https://github.com/Process-Science-Community/MATE/blob/main/docs/README.md) | The index of every document in the repository. |

A decision that a reader would otherwise have to reverse-engineer belongs in a document: record the rationale there, cite it from the code that implements it, and update this manual in the same pull request.

## Specified but unimplemented

Documenting them prevents conflicting implementations: Node and R runtimes for modules (the protocol needs no host change), git-URL and registry module installs (today the only channel is an uploaded archive), multi-node deployments (the S3 layer is the groundwork, and the open seams are named in `S3_OFFLOAD.md`), and additional dataset shapes (the envelope is shape-tagged, so new shapes are additive).

Deliberately out of scope: a multi-tenant SaaS, a plugin marketplace with ranking, anything that requires a managed cloud service, and replacing the analyst — modules produce results and explanations; interpretation stays with people.
