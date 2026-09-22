<!-- Interfaces — the HTTP and MCP surfaces. -->

<!-- group: Interfaces -->

# REST API

Everything the app can do is available over HTTP. This chapter is the contract; the exhaustive endpoint list is the schema itself.

| Aspect | Detail |
| --- | --- |
| Base path | `/api/v1` |
| Auth | `Authorization: Bearer <access token>` — session JWT or personal access token |
| Schema | `/openapi.json`, browsable at `/docs` (Swagger) and `/redoc` |
| Health | `GET /health`, outside the version prefix |
| Content | JSON, except multipart uploads and stream responses |

## Conventions

| Convention | Behaviour |
| --- | --- |
| Long operations | Answer immediately with `{ "job_id": … }`; poll `GET /jobs/{id}` or stream `GET /jobs/{id}/stream`. |
| Streaming | `text/event-stream` for job progress, platform events, and AI chat. |
| Errors | JSON with a `detail` string. |
| Ownership | Another user's resource is indistinguishable from a missing one (`404`). |
| Idempotency | `GET`, `PUT`, `DELETE` are safe to retry; `POST` creates. |
| Pagination | `limit`/`offset` on list endpoints, `cursor` where offered (pass it back verbatim). |
| Soft deletes | Deleting a log, folder, watched folder, or team marks it deleted and removes the data. |

## Endpoint groups

| Group | Representative endpoints |
| --- | --- |
| Import | `POST /event-logs/stage` (upload + probe) · `POST /event-logs` (create from the staged token) · `POST /event-logs/from-url` |
| Logs | `GET /event-logs` · `GET|PATCH|DELETE /event-logs/{id}` · `POST /event-logs/{id}/reimport` · `/remap` · `/duplicate` |
| Log data | `GET /event-logs/{id}/events` · `/variants` · `/variants/{variant_id}` · `/activities` · `/data-quality` · `PUT /event-logs/{id}/active-filter` · `PATCH /event-logs/{id}/events/{row_index}` |
| OCEL | `GET /event-logs/{id}/ocel/overview` · `/object-types` · `/objects` · `/events` · `/relationships` |
| Organisation | `GET|POST /folders` · `PATCH|DELETE /folders/{id}` · `POST /folders/reorder` · `GET|POST /watched-folders` · `POST /watched-folders/{id}/scan` |
| Jobs | `GET /jobs` · `GET /jobs/{id}` · `POST /jobs/{id}/cancel` · `/retry` · `POST /jobs/queue/pause` · `/resume` · `GET /jobs/{id}/stream` |
| Events | `GET /events?topic=…` — the platform SSE stream |
| Modules | `GET /modules` · `GET /modules/{id}/manifest` · `/config-schema` · `GET|PUT /modules/{id}/config` · `GET /modules/cards` · `POST /modules/install` · `/install/git` · `/install/registry` · `POST /modules/restore-defaults` · `DELETE /modules/{id}` |
| Datasets | `GET /datasets/catalog` · `GET /datasets/{module}/{dataset}` · `POST /datasets/{module}/{dataset}/transform` |
| Dashboards | `GET|POST /dashboards` · `GET|PATCH|DELETE /dashboards/{id}` · `GET /dashboards/templates` · `GET /dashboards/{id}/export` · `POST /dashboards/import` · `GET|POST /dashboards/{id}/shares` |
| Sharing | `GET /sharing/shared-with-me` · `GET /sharing/targets` |
| AI | `GET|PUT /ai/config` · `POST /ai/models/{provider}` · `GET /ai/pricing` · `POST /ai/chat` · `POST /ai/route` · `POST /ai/guidance/…` |
| Account | `GET|POST /api-tokens` · `DELETE /api-tokens/{id}` · `GET|PUT /api-tokens/consent` · `GET|PUT /preferences/{key}` · `GET|PUT /onboarding` · `GET|PUT /usage/config` · `GET /usage/summary` · `GET /usage/export` |
| System | `GET /system/storage` · `/diagnostics` · `GET|PUT /system/jobs` · `GET /system/resources` · `GET|PUT /system/mcp` · `GET /system/mcp-metrics` |
| Admin | `/admin/export/…` (metadata and behaviour export) · `/admin/users` · `/admin/teams` · `/admin/jobs` · `/admin/modules` · `/admin/controls` · `/admin/insights/…` |

Admin routes are gated by `require_admin` server-side, and a personal access token can never carry the `admin` scope.

## Type generation

```bash title="Terminal"
make codegen          # API must be listening on :8000
```

Regenerates `apps/web/lib/api-types.ts` from `/openapi.json`. The web app never hand-writes an API type, so a route change is a breaking change for the frontend until codegen runs.

## Worked example: automate an import

```python title="nightly_import.py"
import os
import time

import httpx

client = httpx.Client(
    base_url=os.environ["MATE_API_URL"].rstrip("/") + "/api/v1",
    headers={"Authorization": f"Bearer {os.environ['MATE_TOKEN']}"},  # mate_pat_…
    timeout=60.0,
)


def wait_for_job(job_id: str) -> dict:
    while True:
        job = client.get(f"/jobs/{job_id}").raise_for_status().json()
        if job["status"] in {"completed", "failed", "cancelled"}:
            return job
        time.sleep(3)


# 1. import — answers with a job id, never blocks
created = client.post(
    "/event-logs/from-url", json={"url": os.environ["MATE_SOURCE_URL"], "name": "nightly"}
).raise_for_status().json()
log_id = created["log_id"]
job = wait_for_job(created["job_id"])
assert job["status"] == "completed", job.get("error")

# 2. wait for the module precompute chain to settle
while client.get(f"/event-logs/{log_id}").json()["status"] != "ready":
    time.sleep(2)

# 3. read a dataset a module publishes
rows = client.get(
    "/datasets/performance/slowest-activities", params={"log_id": log_id, "top_n": 5}
).json()["rows"]
print(rows)
```

Rules that keep such a script honest: retry transport errors and 5xx, never 4xx; poll rather than hammer; treat `processing` as "module results do not exist yet"; and remember that importing the same file twice creates two logs — de-duplicate on your side.

# MCP server

The Model Context Protocol interface gives external AI clients scoped access to the same platform. It is opt-in, and its limits are the interesting part.

| Aspect | Detail |
| --- | --- |
| Transport | Streamable HTTP at `/mcp` |
| Identity | Exactly one authenticated account per call; `user_id` is never a tool argument |
| Default state | **Off** — `MCP_ENABLED=true` mounts it, read at boot |
| Live governance | An admin can disable it, force read-only, or restrict token minting without a redeploy |
| Resources | `mate://docs/usage`, `mate://processes`, `mate://process/{log_id}/module/{module_id}` |
| Discovery | `get_server_info` reports the live toolset and scope set; `mate://docs/usage` is the agent's starting document |

## Enabling it

```bash title=".env on the server"
MCP_ENABLED=true
API_BASE_URL=https://mate.example.org     # required for OAuth discovery
MCP_TOOLSETS=                             # empty = everything except admin
MCP_REQUIRE_EGRESS_CONSENT=true
```

```bash title="Terminal"
docker compose -f docker-compose.yml -f docker-compose.prod.yml up -d   # recreate, not restart
```

The proxy already routes `/mcp` and `/.well-known/oauth-protected-resource` to the API.

## Authentication

| Method | How it works |
| --- | --- |
| Personal access token | A `mate_pat_…` token minted in **Settings → API & MCP**. It acts as its owner, on that owner's data only, and can never carry `admin`. An empty scope grant means all read scopes. |
| OAuth 2.1 | The client discovers Keycloak from the `401` challenge (RFC 9728), runs auth-code with PKCE, then retries with the JWT. The `admin` scope additionally needs the `admin` realm role on the same token. |

Independently of the credential, each user opts in once to *external data access*; without it, tools answer `[consent_required]`. That makes consent a per-user switch rather than a per-token one, so a stolen token cannot silently widen egress.

## Scopes and toolsets

| Scope | Grants |
| --- | --- |
| `processes:read` / `processes:write` | Log metadata and aggregates / import, rename, filter, delete, folders |
| `modules:read` / `modules:write` / `modules:manage` | Module outputs and datasets / per-module configuration / install lifecycle |
| `dashboards:read` / `dashboards:write` | Read boards (own and shared) / create, edit, share, delete |
| `jobs:read` / `jobs:control` | List, get, wait / cancel, retry, queue pause and resume |
| `watched:read` / `watched:write` | Read watched folders / create, update, scan, delete |
| `account:read` / `account:write` | Usage summary and token list / revoke your own tokens |
| `admin` | The admin toolset — OAuth plus the admin realm role only |

| Toolset | Covers |
| --- | --- |
| `meta` (always on) | `get_server_info`, `whoami` |
| `processes` | Log listing and detail, activities, variants, data quality, OCEL views, folders, import, remap, re-import, duplicate, delete |
| `analysis` | Module inventory and availability, curated module outputs, dataset catalogue, cached guidance, module configuration, plus the convenience readers `get_bottlenecks`, `get_conformance`, `get_process_model`, `get_drifts` |
| `dashboards` | Board CRUD, card catalogue, export and import, shares, share targets |
| `jobs` | List, get, wait, cancel, retry, queue control |
| `watched` | Watched-folder CRUD and scan |
| `account` | Usage summary, token list, revoke |
| `admin` | Cross-user jobs, users, teams, module policies, insights, MCP governance, token oversight |

## Conventions

| Convention | Behaviour |
| --- | --- |
| Dry runs | Destructive tools take `confirm: bool`; without it they return a preview and mutate nothing. |
| Long operations | Answer with `{ job_id }`; follow up with `get_job` or block on `wait_for_job`, where a timeout returns the current state with `timed_out: true` rather than an error. |
| Pagination | `cursor` and `limit` (max 200), returning `{ items, next_cursor, total }`. |
| Output cap | About 200 KB per result, truncated with a preview — narrow the query instead. |
| Rate limits | 120 requests/min per user (burst 40), writes 30/min (burst 10), 4 concurrent calls, 30 s per call. |
| Errors | A stable prefix: `[not_found]`, `[forbidden]`, `[conflict]`, `[invalid]`, `[rate_limited]`, `[timeout]`, `[read_only]`, `[consent_required]`, `[scope_missing]`, `[confirm_required]`, `[internal]`. |

## The data wall

> [!WARNING]
> No tool returns raw event rows, distinct column values, OCEL object rows, or file downloads. Tools serve aggregates and precomputed module outputs only, and the restriction is structural: the module context built for an MCP call has its event-log accessors replaced by objects that raise on any access. It is the same wall MATE AI sits behind.

## Client setup

```json title="MCP client configuration (PAT)"
{
  "mcpServers": {
    "mate": {
      "type": "http",
      "url": "https://mate.example.org/mcp",
      "headers": { "Authorization": "Bearer mate_pat_…" }
    }
  }
}
```

```bash title="Claude Code"
claude mcp add --transport http mate https://mate.example.org/mcp \
  --header "Authorization: Bearer $MATE_PAT"
```

For an OAuth-capable client, omit the header and point it at the same URL: the first unauthenticated call answers `401` with the Keycloak authorization server, and the client runs auth-code with PKCE.

## Workflows that work well

| Task | Tools behind it |
| --- | --- |
| Orient | `get_server_info`, `whoami`, `list_processes` |
| Import and wait | `import_process_from_url` → `wait_for_job` |
| Understand a process | `get_process`, `get_process_overview`, then `get_bottlenecks` / `get_drifts` / `get_conformance` |
| Drill into detail | `get_variants` → `get_variant` → `get_variant_cases` |
| Explain a number | `get_cached_guidance`, `get_module_output` (cache-only, never triggers a model call) |
| Build a board | `get_dashboard_card_catalog` → `create_dashboard` → `share_dashboard` |
| Recover a failure | `list_jobs(status="failed")` → `get_job` → `retry_job` |

## When it misbehaves

| Symptom | Cause |
| --- | --- |
| `503` "MCP is disabled" | The boot flag is off, or the live admin kill switch is on. |
| `401` on every call | Token missing, expired, revoked, or wrong OAuth audience. |
| `[consent_required]` | The user has not enabled external data access. |
| `[scope_missing]` | The token lacks the scope; scopes are fixed at mint time. |
| `[read_only]` on a write | Server-wide read-only, from the boot flag or the live toggle. |
| `404` on `/mcp` | The flag never reached the container, or the proxy route is missing. |
| `403` "Origin not allowed" | A browser-based client on a foreign origin — use a native or server-side client. |
