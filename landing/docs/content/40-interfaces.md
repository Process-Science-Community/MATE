<!-- Interfaces — the HTTP and MCP surfaces. -->

<!-- group: Interfaces -->

# REST API

Everything the app can do is available over HTTP. This chapter is the contract: the endpoint index below is complete, and `/openapi.json` owns the request and response shapes.

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
| Errors | JSON with a `detail` field — a string for HTTP errors, an array of objects for `422` validation failures. |
| Ownership | Another user's resource is indistinguishable from a missing one (`404`). |
| Idempotency | `GET`, `PUT`, `DELETE` are safe to retry; `POST` creates. |
| Pagination | `limit`/`offset` on the row-level list endpoints (events, variants, activities, edits, OCEL views, admin listings); top-level collections return full lists and `GET /jobs` takes `limit` only. Cursor pagination is MCP-only. |
| Soft deletes | Deleting a log, folder, or watched folder marks it deleted and removes the data; deleting a team is a hard delete that cascades to its memberships. |

## Endpoint groups

Every route the API serves, one endpoint per row. Paths are relative to `/api/v1`. The `Request` and `Response` cells show the shape of a call — a query string as it would be sent, and a formatted JSON body under its status code. A `…` marks elided fields, further array items, or a nested object left unexpanded; `/openapi.json` stays authoritative for the complete schema.

The table is generated from that schema: after changing a route, a request model or a response status, run `uv run python landing/docs/gen-endpoints.py` and rebuild.

<!-- generated: endpoint-table -->

:::wide 12 22 33 33

| Group | Endpoint | Request | Response |
| --- | --- | --- | --- |
| Import | `POST /event-logs/stage` | `{"file":"…"}` | `200` `{"staging_token":"…","source_format":"…","log_model":"case_centric","needs_mapping":true,…}` |
| Import | `POST /event-logs` | `{"file":"…","staging_token":"…","name":"nightly import","csv_mapping":"…",…}` | `202` `{"log_id":"018f2c9a…","job_id":"018f2c9a…"}` |
| Import | `POST /event-logs/from-url` | `{"url":"https://example.com/log.xes","name":"nightly import","csv_mapping":"…",…}` | `202` `{"log_id":"018f2c9a…","job_id":"018f2c9a…"}` |
| Import | `POST /event-logs/probe-xml` | `{"file":"…"}` | `200` `{"format_hint":"generic","event_element":"…","events_sampled":0,"fields":[{…}],…}` |
| Import | `POST /event-logs/probe-json` | `{"file":"…"}` | `200` `{"format_hint":"generic","event_path":"…","events_sampled":0,"fields":[{…}],…}` |
| Logs | `GET /event-logs` | `?q=…&status=ready` | `200` `[{"id":"018f2c9a…","name":"nightly import","status":"importing","source_format":"…",…}]` |
| Logs | `GET /event-logs/{log_id}` | — | `200` `{"id":"018f2c9a…","name":"nightly import","status":"importing","source_format":"…",…}` |
| Logs | `PATCH /event-logs/{log_id}` | `{"name":"nightly import","description":"…","column_overrides":null,…}` | `200` `{"id":"018f2c9a…","name":"nightly import","status":"importing","source_format":"…",…}` |
| Logs | `DELETE /event-logs/{log_id}` | — | `204` — |
| Log data | `PUT /event-logs/{log_id}/active-filter` | `{"filter":[{"field":"…","op":"…","value":null}]}` | `200` `{"active_filter":[{"field":"…","op":"…","value":null}],"modules_retriggered":false}` |
| Log data | `GET /event-logs/{log_id}/activities` | — | `200` `{"rows":[{"activity":"…","count":25}],"total":25}` |
| Log data | `GET /event-logs/{log_id}/activities/cases` | `?limit=100&offset=0&name=nightly import` | `200` `{"rows":[{"case_id":"018f2c9a…",…}],"total":25,"offset":25,"limit":25}` |
| Log data | `GET /event-logs/{log_id}/activities/detail` | `?name=nightly import` | `200` `{"activity":"…","event_count":25,"event_pct":0.5,"case_count":25,"case_pct":0.5,…}` |
| Log data | `GET /event-logs/{log_id}/columns/{field}/values` | `?limit=500&q=…` | `200` `{"field":"…","values":[{"value":"…","count":25}],"total_distinct":25,"truncated":false}` |
| Log data | `GET /event-logs/{log_id}/data-quality` | — | `200` `{"total_events":25,"columns":[{"column":"…","label":"nightly import",…}]}` |
| Log data | `POST /event-logs/{log_id}/duplicate` | — | `201` `{"id":"018f2c9a…","name":"nightly import","status":"importing","source_format":"…",…}` |
| Log data | `GET /event-logs/{log_id}/edits` | `?limit=50&offset=0` | `200` `{"rows":[{"id":25,"log_id":"018f2c9a…","row_index":25,"field":"…",…}],"total":25,…}` |
| Log data | `GET /event-logs/{log_id}/events` | `?limit=50&offset=0&q=…` | `200` `{"rows":["…"],"total":25,"offset":25,"limit":25,"columns":[{…}],"header":{…}}` |
| Log data | `POST /event-logs/{log_id}/events/bulk-fill` | `{"row_indices":[25],"field":"…","value":null}` | `200` `{"updated":25,"header":{"events_count":25,"cases_count":25,…}}` |
| Log data | `PATCH /event-logs/{log_id}/events/{row_index}` | `{"field":"…","value":null}` | `200` `{"row":null,"row_index":25,"new_row_index":25,"header":{…}}` |
| Log data | `POST /event-logs/{log_id}/reimport` | — | `202` `{"log_id":"018f2c9a…","job_id":"018f2c9a…"}` |
| Log data | `POST /event-logs/{log_id}/remap` | `{"case_id":"018f2c9a…","activity":"…","timestamp":"2026-01-01T00:00:00Z",…}` | `202` `{"log_id":"018f2c9a…","job_id":"018f2c9a…"}` |
| Log data | `GET /event-logs/{log_id}/time-bounds` | — | `200` `{"field":"…","min_ts":"…","max_ts":"…"}` |
| Log data | `GET /event-logs/{log_id}/variants` | `?limit=50&offset=0` | `200` `{"rows":[{"rank":25,"variant_id":"018f2c9a…","activities":["…"],…}],"total":25,…}` |
| Log data | `GET /event-logs/{log_id}/variants/{variant_id}` | — | `200` `{"rank":25,"variant_id":"018f2c9a…","activities":["…"],"case_count":25,"case_pct":0.5,…}` |
| Log data | `GET /event-logs/{log_id}/variants/{variant_id}/cases` | `?limit=100&offset=0` | `200` `{"rows":[{"case_id":"018f2c9a…",…}],"total":25,"offset":25,"limit":25}` |
| OCEL | `GET /event-logs/{log_id}/ocel/events` | `?limit=100&offset=0&q=…` | `200` `{"rows":["…"],"columns":["…"],"total":25,"offset":25,"limit":25}` |
| OCEL | `GET /event-logs/{log_id}/ocel/object-types` | — | `200` `[{"type":"…","count":25}]` |
| OCEL | `GET /event-logs/{log_id}/ocel/objects` | `?limit=100&offset=0&q=…` | `200` `{"rows":["…"],"columns":["…"],"total":25,"offset":25,"limit":25}` |
| OCEL | `GET /event-logs/{log_id}/ocel/overview` | — | `200` `{"events_count":25,"objects_count":25,"object_types_count":25,"relations_count":25,…}` |
| OCEL | `GET /event-logs/{log_id}/ocel/relationships` | `?limit=100&offset=0` | `200` `{"rows":["…"],"columns":["…"],"total":25,"offset":25,"limit":25}` |
| Organisation | `GET /folders` | — | `200` `[{"id":"018f2c9a…","name":"nightly import","parent_id":"018f2c9a…","position":0,…}]` |
| Organisation | `POST /folders` | `{"name":"nightly import","parent_id":"018f2c9a…"}` | `201` `{"id":"018f2c9a…","name":"nightly import","parent_id":"018f2c9a…","position":0,…}` |
| Organisation | `POST /folders/reorder` | `{"items":[{"kind":"folder","id":"018f2c9a…",…}]}` | `204` — |
| Organisation | `PATCH /folders/{folder_id}` | `{"name":"nightly import","parent_id":"018f2c9a…","position":25}` | `200` `{"id":"018f2c9a…","name":"nightly import","parent_id":"018f2c9a…","position":0,…}` |
| Organisation | `DELETE /folders/{folder_id}` | — | `204` — |
| Organisation | `GET /watched-folders` | — | `200` `[{"id":"018f2c9a…","name":"nightly import","source_path":"…","mode":"manual",…}]` |
| Organisation | `POST /watched-folders` | `{"name":"nightly import","source_path":"…","mode":"manual","interval_seconds":25,…}` | `201` `{"id":"018f2c9a…","name":"nightly import","source_path":"…","mode":"manual",…}` |
| Organisation | `GET /watched-folders/{watch_id}` | — | `200` `{"id":"018f2c9a…","name":"nightly import","source_path":"…","mode":"manual",…}` |
| Organisation | `PATCH /watched-folders/{watch_id}` | `{"name":"nightly import","mode":"manual","interval_seconds":25,"status":"active",…}` | `200` `{"id":"018f2c9a…","name":"nightly import","source_path":"…","mode":"manual",…}` |
| Organisation | `DELETE /watched-folders/{watch_id}` | — | `204` — |
| Organisation | `POST /watched-folders/{watch_id}/scan` | — | `200` `{"found":0,"imported":0,"skipped":0,"failed":0}` |
| Jobs | `GET /jobs` | `?limit=100&status=ready` | `200` `[{"id":"018f2c9a…","type":"…","title":"nightly import","subtitle":"…",…}]` |
| Jobs | `POST /jobs/cancel-all` | — | `200` `null` |
| Jobs | `POST /jobs/queue/pause` | — | `204` — |
| Jobs | `POST /jobs/queue/resume` | — | `204` — |
| Jobs | `GET /jobs/{job_id}` | — | `200` `{"id":"018f2c9a…","type":"…","title":"nightly import","subtitle":"…",…}` |
| Jobs | `POST /jobs/{job_id}/cancel` | — | `204` — |
| Jobs | `POST /jobs/{job_id}/retry` | — | `200` `null` |
| Jobs | `GET /jobs/{job_id}/stream` | — | `200` `null` |
| Events | `GET /events` | `?topic=["job.progress"]` | `200` `null` |
| Modules | `POST /modules/install` | `{"file":"…"}` | `202` `{"job_id":"018f2c9a…"}` |
| Modules | `POST /modules/restore-defaults` | — | `200` `{"restored":["…"]}` |
| Modules | `GET /modules` | — | `200` `[{"id":"018f2c9a…","name":"nightly import","version":"…","category":"…",…}]` |
| Modules | `GET /modules/cards` | — | `200` `[{"module_id":"018f2c9a…","module_name":"…","widget_id":"018f2c9a…",…}]` |
| Modules | `GET /modules/readme` | — | `200` `null` |
| Modules | `DELETE /modules/{module_id}` | — | `204` — |
| Modules | `GET /modules/{module_id}/assets/{asset_path}` | — | `200` `null` |
| Modules | `GET /modules/{module_id}/config` | — | `200` `{"config":null,"enabled":true,"controlled_by_admin":false,"controlled_cards":null}` |
| Modules | `PUT /modules/{module_id}/config` | `{"config":null,"enabled":true,"controlled_by_admin":false,"controlled_cards":null}` | `200` `{"config":null,"enabled":true,"controlled_by_admin":false,"controlled_cards":null}` |
| Modules | `GET /modules/{module_id}/config-schema` | — | `200` `null` |
| Modules | `GET /modules/{module_id}/layout` | `?log_id=018f2c9a…` | `200` `{"layout":null}` |
| Modules | `PUT /modules/{module_id}/layout` | `?log_id=018f2c9a…` · `{"layout":null}` | `200` `{"layout":null}` |
| Modules | `GET /modules/{module_id}/manifest` | — | `200` `null` |
| Datasets | `GET /datasets/catalog` | — | `200` `[{"module_id":"018f2c9a…","module_name":"…","dataset_id":"018f2c9a…",…}]` |
| Datasets | `GET /datasets/{module_id}/{dataset_id}` | `?log_id=018f2c9a…` | `200` `{"shape":"table","schema":{"columns":[{…}]},"data":{"columns":[{…}],…},"meta":{…}}` |
| Datasets | `POST /datasets/{module_id}/{dataset_id}/transform` | `{"log_id":"018f2c9a…","transforms":["…"]}` | `200` `{"shape":"table","schema":{"columns":[{…}]},"data":{"columns":[{…}],…},"meta":{…}}` |
| Dashboards | `POST /dashboards/import` | `{"name":"nightly import","description":"…","log_model":"case_centric","items":[{…}],…}` | `201` `{"id":"018f2c9a…","name":"nightly import","description":"…","event_log_id":"018f2c9a…",…}` |
| Dashboards | `GET /dashboards` | — | `200` `[{"id":"018f2c9a…","name":"nightly import","description":"…",…}]` |
| Dashboards | `POST /dashboards` | `{"name":"nightly import","description":"…","event_log_id":"018f2c9a…",…}` | `201` `{"id":"018f2c9a…","name":"nightly import","description":"…","event_log_id":"018f2c9a…",…}` |
| Dashboards | `GET /dashboards/templates` | — | `200` `[{"id":"018f2c9a…","name":"nightly import","description":"…",…}]` |
| Dashboards | `GET /dashboards/{dashboard_id}` | — | `200` `{"id":"018f2c9a…","name":"nightly import","description":"…","event_log_id":"018f2c9a…",…}` |
| Dashboards | `PATCH /dashboards/{dashboard_id}` | `{"name":"nightly import","description":"…","event_log_id":"018f2c9a…","items":[{…}],…}` | `200` `{"id":"018f2c9a…","name":"nightly import","description":"…","event_log_id":"018f2c9a…",…}` |
| Dashboards | `DELETE /dashboards/{dashboard_id}` | — | `204` — |
| Dashboards | `GET /dashboards/{dashboard_id}/export` | — | `200` `{"kind":"…","version":1,"name":"nightly import","description":"…",…}` |
| Dashboards | `GET /dashboards/{dashboard_id}/shares` | — | `200` `[{"id":"018f2c9a…","dashboard_id":"018f2c9a…","kind":"user","target_id":"018f2c9a…",…}]` |
| Dashboards | `POST /dashboards/{dashboard_id}/shares` | `{"target_user_id":"018f2c9a…","target_team_id":"018f2c9a…"}` | `201` `{"id":"018f2c9a…","dashboard_id":"018f2c9a…","kind":"user","target_id":"018f2c9a…",…}` |
| Dashboards | `DELETE /dashboards/{dashboard_id}/shares/{share_id}` | — | `204` — |
| Sharing | `GET /sharing/shared-with-me` | — | `200` `[{"id":"018f2c9a…","name":"nightly import","description":"…",…}]` |
| Sharing | `GET /sharing/targets` | — | `200` `[{"kind":"user","id":"018f2c9a…","label":"nightly import","sublabel":"…"}]` |
| AI | `POST /ai/chat` | `{"messages":[{"role":"user","content":"…"}],"context":{…},"nav_hint":{…}}` | `200` `null` |
| AI | `GET /ai/config` | — | `200` `{"system_prompt":"…","anthropic_base_url":"https://api.openai.com/v1",…}` |
| AI | `PUT /ai/config` | `{"system_prompt":"…","anthropic":{"api_key":"…",…},"openai":{…},"unigpt":{…},"custom":{…},…}` | `200` `{"system_prompt":"…","anthropic_base_url":"https://api.openai.com/v1",…}` |
| AI | `POST /ai/guidance/import/column-mapping` | `{"headers":["…"],"sample_rows":[["…"]]}` | `200` `{"suggestions":null}` |
| AI | `POST /ai/guidance/import/quality/{log_id}` | `{"force":false}` | `200` `{"cached":false,"output_hash":"…","generated_at":1767225600,"model":"gpt-4o-mini",…}` |
| AI | `POST /ai/guidance/module/{module_id}` | `?log_id=018f2c9a…` · `{"force":false}` | `200` `{"cached":false,"output_hash":"…","generated_at":1767225600,"model":"gpt-4o-mini",…}` |
| AI | `DELETE /ai/guidance/module/{module_id}` | `?log_id=018f2c9a…` | `200` `null` |
| AI | `POST /ai/guidance/module/{module_id}/stream` | `?log_id=018f2c9a…` | `200` `null` |
| AI | `POST /ai/guidance/process/{log_id}` | `{"force":false}` | `200` `{"cached":false,"output_hash":"…","generated_at":1767225600,"model":"gpt-4o-mini",…}` |
| AI | `POST /ai/models/{provider}` | — | `200` `{"models":[{"id":"018f2c9a…","display_name":"…","created":25}]}` |
| AI | `GET /ai/pricing` | — | `200` `null` |
| AI | `POST /ai/route` | `{"message":"…","context":{"log_id":"018f2c9a…",…}}` | `200` `{"intent":"chat","confidence":0.5,"targets":[{"id":"018f2c9a…",…}],"actions":[{…}]}` |
| Account | `GET /api-tokens` | — | `200` `[{"id":"018f2c9a…","name":"nightly import","token_prefix":"…","scopes":["…"],…}]` |
| Account | `POST /api-tokens` | `{"name":"nightly import","scopes":["…"],"expires_in_days":25}` | `200` `{"id":"018f2c9a…","name":"nightly import","token_prefix":"…","scopes":["…"],…}` |
| Account | `GET /api-tokens/consent` | — | `200` `{"required":false,"consented":false}` |
| Account | `PUT /api-tokens/consent` | `{"consented":false}` | `200` `{"required":false,"consented":false}` |
| Account | `GET /api-tokens/mcp-info` | — | `200` `{"enabled":false,"url":"https://example.com/log.xes","require_consent":false,…}` |
| Account | `DELETE /api-tokens/{token_id}` | — | `200` `null` |
| Account | `GET /onboarding` | — | `200` `{"completed":false,"experience_level":"beginner","tour_completed":false}` |
| Account | `PUT /onboarding` | `{"completed":false,"experience_level":"beginner","tour_completed":false}` | `200` `{"completed":false,"experience_level":"beginner","tour_completed":false}` |
| Account | `GET /preferences/{key}` | — | `200` `null` |
| Account | `PUT /preferences/{key}` | — | `200` `null` |
| Account | `GET /usage/config` | — | `200` `{"enabled":false,"retention_days":25,"capture_clicks":true,"capture_perf":true,…}` |
| Account | `PUT /usage/config` | `{"enabled":false,"retention_days":25,"capture_clicks":true,"capture_perf":true,…}` | `200` `{"enabled":false,"retention_days":25,"capture_clicks":true,"capture_perf":true,…}` |
| Account | `GET /usage/export` | `?format=ndjson` | `200` `null` |
| Account | `GET /usage/summary` | — | `200` `{"enabled":false,"total_events":25,"total_sessions":25,"sessions_last_30d":25,…}` |
| Account | `POST /usage/sync` | — | `202` `null` |
| Account | `DELETE /usage/sync` | — | `200` `{"deleted_events":25,"deleted_sessions":25,"new_anon_user_id_seed":"…"}` |
| System | `GET /system/diagnostics` | — | `200` `{"platform_version":"…","python":"…","is_admin":false,"system":{…},"versions":{…},…}` |
| System | `GET /system/jobs` | — | `200` `{"worker_concurrency":25,"min":1,"max":8,"is_admin":false}` |
| System | `PUT /system/jobs` | `{"worker_concurrency":25}` | `200` `{"worker_concurrency":25,"min":1,"max":8,"is_admin":false}` |
| System | `GET /system/mcp` | — | `200` `{"boot_enabled":false,"enabled":false,"mint_policy":"…","boot_read_only":false,…}` |
| System | `PUT /system/mcp` | `{"enabled":false,"mint_policy":"all_users","read_only":false}` | `200` `{"boot_enabled":false,"enabled":false,"mint_policy":"…","boot_read_only":false,…}` |
| System | `GET /system/mcp-metrics` | — | `200` `null` |
| System | `GET /system/resources` | — | `200` `{"cpu":{"current_pct":0.5,"max_pct":0.5,"cores_logical":25,…},"memory":{…},…}` |
| System | `GET /system/storage` | — | `200` `null` |
| Admin | `GET /admin/api-tokens` | — | `200` `[{"id":"018f2c9a…","user_id":"018f2c9a…","user_email":"ada@example.com",…}]` |
| Admin | `DELETE /admin/api-tokens/{token_id}` | — | `200` `null` |
| Admin | `GET /admin/controls/ai/config` | — | `200` `{"system_prompt":"…","anthropic_base_url":"https://api.openai.com/v1",…}` |
| Admin | `PUT /admin/controls/ai/config` | `{"system_prompt":"…","anthropic":{"api_key":"…",…},"openai":{…},"unigpt":{…},"custom":{…},…}` | `200` `{"system_prompt":"…","anthropic_base_url":"https://api.openai.com/v1",…}` |
| Admin | `POST /admin/controls/ai/models/{provider}` | — | `200` `{"models":[{"id":"018f2c9a…","display_name":"…","created":25}]}` |
| Admin | `GET /admin/controls/items` | — | `200` `{"items":[{"scope":"setting","key":"…","label":"nightly import",…}]}` |
| Admin | `PUT /admin/controls/items/{scope}/{key}` | `{"control_mode":"user","admin_value":null}` | `200` `{"scope":"setting","key":"…","label":"nightly import","description":"…",…}` |
| Admin | `GET /admin/dashboard-shares` | — | `200` `[{"id":"018f2c9a…","dashboard_id":"018f2c9a…","dashboard_name":"…","owner_label":"…",…}]` |
| Admin | `DELETE /admin/dashboard-shares/{share_id}` | — | `204` — |
| Admin | `GET /admin/export-info` | — | `200` `{"is_admin":false,"user_count":25,"event_count":25,"db_size_bytes":25}` |
| Admin | `GET /admin/export/event-log.xes` | — | `200` `null` |
| Admin | `GET /admin/export/events-ocel2` | — | `200` `null` |
| Admin | `GET /admin/export/events.csv` | — | `200` `null` |
| Admin | `GET /admin/export/events.ndjson` | — | `200` `null` |
| Admin | `GET /admin/export/facets` | — | `200` `{"users":[{"id":"018f2c9a…","email":"ada@example.com",…}],"event_types":["…"],…}` |
| Admin | `GET /admin/export/metadata-db` | — | `200` `null` |
| Admin | `GET /admin/export/preview` | — | `200` `{"matched_events":25,"matched_sessions":25,"distinct_users":25,…}` |
| Admin | `GET /admin/insights/event-logs` | `?limit=50&offset=0&q=…&status=ready` | `200` `{"total":25,"items":[{"id":"018f2c9a…","name":"nightly import",…}]}` |
| Admin | `GET /admin/insights/event-logs/{log_id}/download` | — | `200` `null` |
| Admin | `GET /admin/insights/jobs` | — | `200` `{"days":25,"runtime":{"concurrency":25,"live_workers":25,"queue_depth":25,…},…}` |
| Admin | `GET /admin/insights/overview` | — | `200` `{"days":25,"kpis":{"user_count":25,"log_count":25,"events_ingested":25,…},…}` |
| Admin | `GET /admin/insights/storage` | — | `200` `{"backend_mode":"…","s3_used_bytes":25,"s3_object_count":25,"s3_quota_bytes":25,…}` |
| Admin | `GET /admin/insights/usage` | — | `200` `{"days":25,"installs_by_module":[{"label":"nightly import","count":25}],"modules":[{…}],…}` |
| Admin | `GET /admin/insights/users` | — | `200` `{"days":25,"user_count":25,"active_users_in_range":25,"onboarding_completed":25,…}` |
| Admin | `GET /admin/jobs` | `?limit=100&offset=0&q=…&status=ready` | `200` `{"total":25,"items":[{"id":"018f2c9a…","type":"…",…}],"summary":{…}}` |
| Admin | `POST /admin/jobs/cancel-all` | `{"user_id":"018f2c9a…"}` | `200` `null` |
| Admin | `POST /admin/jobs/queue/pause` | `{"user_id":"018f2c9a…"}` | `204` — |
| Admin | `POST /admin/jobs/queue/resume` | `{"user_id":"018f2c9a…"}` | `204` — |
| Admin | `POST /admin/jobs/{job_id}/cancel` | — | `204` — |
| Admin | `POST /admin/jobs/{job_id}/kill` | — | `204` — |
| Admin | `GET /admin/jobs/{job_id}/logs` | `?limit=500` | `200` `{"job_id":"018f2c9a…","lines":[{"ts":0.5,"level":"…","event":"…",…}],"truncated":false}` |
| Admin | `POST /admin/jobs/{job_id}/retry` | — | `200` `null` |
| Admin | `GET /admin/modules` | — | `200` `[{"id":"018f2c9a…","name":"nightly import","version":"…","category":"…",…}]` |
| Admin | `PUT /admin/modules/{module_id}/default` | `{"is_default":false}` | `200` `{"id":"018f2c9a…","name":"nightly import","version":"…","category":"…",…}` |
| Admin | `POST /admin/modules/{module_id}/installs` | `{"user_id":"018f2c9a…"}` | `200` `{"id":"018f2c9a…","name":"nightly import","version":"…","category":"…",…}` |
| Admin | `DELETE /admin/modules/{module_id}/installs/{user_id}` | — | `204` — |
| Admin | `PUT /admin/modules/{module_id}/withhold` | `{"withheld":false}` | `200` `{"id":"018f2c9a…","name":"nightly import","version":"…","category":"…",…}` |
| Admin | `GET /admin/teams` | — | `200` `[{"id":"018f2c9a…","name":"nightly import","member_count":0,…}]` |
| Admin | `POST /admin/teams` | `{"name":"nightly import"}` | `201` `{"id":"018f2c9a…","name":"nightly import","member_count":0,…}` |
| Admin | `PATCH /admin/teams/{team_id}` | `{"name":"nightly import"}` | `200` `{"id":"018f2c9a…","name":"nightly import","member_count":0,…}` |
| Admin | `DELETE /admin/teams/{team_id}` | — | `204` — |
| Admin | `GET /admin/teams/{team_id}/members` | — | `200` `[{"user_id":"018f2c9a…","role":"…","email":"ada@example.com",…}]` |
| Admin | `POST /admin/teams/{team_id}/members` | `{"user_id":"018f2c9a…","role":"owner"}` | `201` `{"user_id":"018f2c9a…","role":"…","email":"ada@example.com","preferred_username":"…",…}` |
| Admin | `DELETE /admin/teams/{team_id}/members/{user_id}` | — | `204` — |
| Admin | `GET /admin/users` | — | `200` `[{"id":"018f2c9a…","email":"ada@example.com","preferred_username":"…",…}]` |
| Admin | `GET /admin/users/{user_id}` | — | `200` `{"id":"018f2c9a…","email":"ada@example.com","preferred_username":"…",…}` |
| Admin | `DELETE /admin/users/{user_id}` | — | `200` `{"deleted":false,"jobs_cancelled":25,"modules_torn_down":25,"keycloak_deleted":false,…}` |
| Meta | `GET /health` | — | `200` `{"status":"ready","version":"…"}` |

:::

<!-- /generated -->

Admin routes are gated by `require_admin` server-side, and a personal access token can never carry the `admin` scope. A module's own `@route` handlers are mounted at `/api/v1/modules/{module_id}/*` by the loader, so they appear in `/openapi.json` rather than here.

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

# 3. read a dataset a module publishes — rows live under `data` in the envelope
rows = client.get(
    "/datasets/performance_java/activity-performance", params={"log_id": log_id}
).json()["data"]["rows"]
print(rows)
```

Rules that keep such a script correct: retry transport errors and 5xx, never 4xx; poll instead of a tight loop; treat `processing` as "module results do not exist yet"; the dataset endpoint takes only `log_id` (a dataset's declared parameters are set on its card, not as query parameters); and remember that importing the same file twice creates two logs — de-duplicate on your side.

# MCP server

The Model Context Protocol interface gives external AI clients scoped access to the same platform. It is opt-in, and its limits are enforced structurally rather than by prompt instructions.

| Aspect | Detail |
| --- | --- |
| Transport | Streamable HTTP at `/mcp` |
| Identity | Exactly one authenticated account per call; `user_id` is never a tool argument |
| Default state | **Off** — `MCP_ENABLED=true` mounts it, read at boot |
| Live governance | An admin can disable it, force read-only, or restrict token minting without a redeploy |
| Resources | `mate://docs/usage`, `mate://processes`, `mate://process/{log_id}/module/{module_id}` |
| Discovery | `get_server_info` reports the live toolset and scope set; `mate://docs/usage` is the agent's starting document |

## Enabling the server

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
| `admin` | Cross-user jobs, users, teams, insights, MCP governance, token oversight — module policy stays REST-only |

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

## Common workflows

| Task | Tools behind it |
| --- | --- |
| Orient | `get_server_info`, `whoami`, `list_processes` |
| Import and wait | `import_process_from_url` → `wait_for_job` |
| Understand a process | `get_process`, `get_process_overview`, then `get_bottlenecks` / `get_drifts` / `get_conformance` |
| Drill into detail | `get_variants` → `get_variant` → `get_variant_cases` |
| Explain a number | `get_cached_guidance`, `get_module_output` (cache-only, never triggers a model call) |
| Build a board | `get_dashboard_card_catalog` → `create_dashboard` → `share_dashboard` |
| Recover a failure | `list_jobs(status="failed")` → `get_job` → `retry_job` |

## Failure modes

| Symptom | Cause |
| --- | --- |
| `503` "MCP is disabled" | The boot flag is off, or the live admin kill switch is on. |
| `401` on every call | Token missing, expired, revoked, or wrong OAuth audience. |
| `[consent_required]` | The user has not enabled external data access. |
| `[scope_missing]` | The token lacks the scope; scopes are fixed at mint time. |
| `[read_only]` on a write | Server-wide read-only, from the boot flag or the live toggle. |
| `404` on `/mcp` | The flag never reached the container, or the proxy route is missing. |
| `403` "Origin not allowed" | A browser-based client on a foreign origin — use a native or server-side client. |
