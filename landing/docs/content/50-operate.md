<!-- Operate — deploying, configuring, backing up, and repairing an installation. -->

<!-- group: Operate -->

# Deployment

Two supported shapes: a local stack on one machine, and a production deployment behind a proxy with TLS. Both are the same compose file plus an overlay.

## Modes

| Command | Compose files | Use |
| --- | --- | --- |
| `make up` | `docker-compose.yml` | Local preview, workshops, single-user installations. |
| `make up-dev` | `+ compose.dev.yml` | Development with hot reload inside containers. |
| `make dev` | none | Host dev servers, fastest reload. |
| Production | `+ docker-compose.prod.yml` | A VM behind an edge proxy, with TLS and resource caps. |

The production overlay adds a Caddy container as the only published port, resets the app ports to unpublished, points every URL at the public origin, serves the landing page from `./landing`, enables server-side sessions, applies memory limits per service, and caps container logs (`10m` × 3 files). A one-shot `keycloak-config` service applies realm hardening on every deploy.

:::stack title="Production topology" caption="One public port. Caddy terminates TLS and routes by path; only the API owns state."
- Internet · TLS certificate on the edge proxy
- Edge proxy · forwards `:80` and `:443` to the VM
- Caddy (container) · TLS termination and path routing
  - `api` · `/api/v1/*`, `/health`, `/mcp`
  - `web` · everything else
  - `keycloak` · `/auth/*`
  - landing page · `/` and `/assets/*`, served from disk
- `api` · the one service that owns state
  - `app-db` · Postgres metadata
  - `data/` · Parquet logs and module results
  - S3 bucket · only with `STORAGE_MODE=s3`
:::

Compression excludes `text/event-stream` on purpose, so streaming responses are never buffered. Security headers (HSTS, `nosniff`, referrer policy) and a report-only CSP are set on the web app.

## Runbook

Each step rules out one layer, so a problem is found where it happens.

:::steps
1. **Smoke-test the pipe** from outside the VM: `curl -sSI https://mate.example.org/health | head -1`. If this fails, nothing else will work.

2. **Clone on the VM** into a stable directory: `git clone … ~/mate`.

3. **Secrets and realm in one step**: `./infra/bootstrap-vm.sh` generates `.env` secrets, rewrites the realm export's redirect origins and client secret, and starts the stack (unless `--no-start`).

4. **Fill `.env`**: `AUTH_SECRET`, `KEYCLOAK_CLIENT_SECRET`, `NEXT_PUBLIC_API_URL`, `AUTH_URL`, `KEYCLOAK_ISSUER`, `API_BASE_URL`, and the tracking policy. Full list: [Configuration](configuration.html).

5. **Bring it up**:

   ```bash title="Terminal"
   docker compose -f docker-compose.yml -f docker-compose.prod.yml up -d --build
   docker compose -f docker-compose.yml -f docker-compose.prod.yml logs -f api
   ```

6. **Verify in order**: `/health` answers · `/login` renders · sign-in works · a small import completes · Discovery opens · a dashboard loads · MATE AI answers (if configured) · `python -m mate.api.storage.migration check` passes (if S3).
:::

Optional pieces: the CV4CDD model files (uploaded on the module's settings page), the Neo4j sidecar (compose profile `graph`), and S3 storage.

## Identity setup

The realm JSON imports **only into an empty Keycloak database**; everything after that is a console change.

| Task | How |
| --- | --- |
| Create accounts | Keycloak console → Users → Add user, then set a temporary password. The platform creates its own `users` row on first contact. |
| Grant admin | Realm roles → `admin` → Assign role. The seeded account deliberately has no admin role. |
| Broker an institutional IdP | `./infra/keycloak/configure-university-idp.sh`, then set `KEYCLOAK_IDP_HINT` so users skip Keycloak's form. Keep the local form as break-glass. |
| Admin REST client (user deletion) | `./infra/keycloak/configure-admin-client.sh` |
| MCP OAuth client | `./infra/keycloak/configure-mcp-client.sh` |

Deleting a user from *Admin → Users* purges their logs, results, jobs, dashboards, tokens, and settings, removes the Keycloak identity, and deletes their S3 prefix. It is intentionally irreversible.

## Updates

```bash title="From a laptop inside the VPN"
make deploy      # pushes the branch, resets the server clone, rebuilds, health-checks
```

`make deploy` reads `scripts/deploy.env` (`DEPLOY_HOST`, `DEPLOY_USER`, optional `DEPLOY_PORT`, `DEPLOY_DIR`, `DEPLOY_BRANCH`, `DEPLOY_PUBLIC_URL`), pushes the current branch, resets the server's clone to it, rebuilds with the production overlay, prunes dangling images, and polls `/health` until the new version answers.

| Change | Needs |
| --- | --- |
| Application code | `up -d --build` |
| A `NEXT_PUBLIC_*` variable | `--build`: the value is inlined into the client bundle |
| Any other environment variable | `up -d` — a **recreate**, not a restart |
| Caddyfile | Restart of the `proxy` container |
| A realm setting | A change in the Keycloak console, not the JSON |
| A module's dependency block | Nothing: the loader re-materialises on boot |

Migrations run on API boot (`alembic upgrade head`), so a deploy migrates itself. There is no rolling deployment — a restart interrupts running jobs, which are marked failed at the next boot while logs left mid-import are reconciled.

```bash title="Rolling back"
git log --oneline -5 && git reset --hard <sha>
docker compose -f docker-compose.yml -f docker-compose.prod.yml up -d --build
```

Rolling back the code does not roll back migrations. If the release included a destructive schema change, restore the metadata snapshot first.

# Configuration

Every knob a running platform reads. The canonical list with inline commentary is [`.env.example`](https://github.com/Process-Science-Community/MATE/blob/main/.env.example).

## URLs and identity

| Variable | Default | Notes |
| --- | --- | --- |
| `NEXT_PUBLIC_API_URL` | `http://localhost:8000` | The API URL the *browser* uses; inlined at build time, so a change needs a rebuild. |
| `AUTH_URL` | `http://localhost:3000` | The web origin Auth.js believes in; decides secure-cookie behaviour. |
| `AUTH_SECRET` | – | Encrypts the session cookie. `openssl rand -base64 32`. |
| `CORS_ORIGINS` | `["http://localhost:3000"]` | Extend when the web origin changes. |
| `KEYCLOAK_ISSUER` | dev realm URL | The `iss` claim the API validates. |
| `KEYCLOAK_JWKS_URL` | internal DNS | Where signing keys are fetched. |
| `KEYCLOAK_AUDIENCE` | `flows-funds-api` | Expected audience on access tokens. |
| `KEYCLOAK_CLIENT_ID` / `_SECRET` | seeded dev values | Must match the realm; rotate before any shared deployment. |
| `KEYCLOAK_ADMIN` / `_PASSWORD` | `admin` / `admin` | The Keycloak console login. |
| `KEYCLOAK_IDP_HINT` | empty | Jump straight to a brokered identity provider. |
| `SESSION_STORE_DIR` | unset | Server-side sessions; sidesteps the 4 KB cookie limit. |
| `DEMO_MODE` / `DEMO_ADMIN` | `false` | Local-only login bypass. Never on a shared deployment. |

## Storage

| Variable | Default | Notes |
| --- | --- | --- |
| `STORAGE_MODE` | `local` | `local`, or `s3` for a bucket-authoritative deployment. |
| `STORAGE_S3_ENDPOINT` | – | Endpoint URL with scheme; empty means AWS S3 proper. |
| `STORAGE_S3_BUCKET` / `_REGION` | – | Region is required by AWS; R2 wants `auto`. |
| `STORAGE_S3_ACCESS_KEY` / `_SECRET_KEY` | – | Credentials. |
| `STORAGE_S3_PATH_STYLE` | `true` | Needed by MinIO and Ceph; set `false` on AWS. |
| `STORAGE_S3_USE_SSL` / `_VERIFY` | `true` / system CAs | TLS toggle; `false` or a CA-bundle path for an internal CA. |
| `STORAGE_S3_PREFIX` | – | Key prefix, so deployments can share a bucket. |
| `STORAGE_S3_QUOTA_BYTES` | `0` | Ceiling for the prefix; imports answer `507` once reached. |
| `LOCAL_CACHE_MAX_BYTES` | `0` | Cache budget for the eviction reaper; `0` never evicts. |
| `CACHE_EVICT_DRY_RUN` | `true` | Log candidates without deleting — soak first. |
| `JOB_RETENTION_DAYS` | `0` | Prune terminal jobs after N days. |

## Jobs and compute

| Variable | Default | Notes |
| --- | --- | --- |
| `WORKER_CONCURRENCY` | `2` | Parallel job slots (1–8); also changeable live by an admin. |
| `JOB_EXECUTION_TIMEOUT_SECONDS` | `1800` | Wall-clock backstop per job; `0` disables. |
| `MODULE_PROCESS_POOL_SIZE` | `min(cores, 8)` | Size of the CPU offload pool. |
| `MAX_OFFLOADS_PER_USER` | `0` (= pool size) | Per-user share of that pool; set it on shared hosts. |
| `DUCKDB_THREADS` / `DUCKDB_MEMORY_LIMIT` | `0` / unset | Per-query caps. |
| `EVENT_LOG_CACHE_ENTRIES` | `3` | Materialised DataFrame cache for repeated reads. |
| `ENV` | `prod` | `dev` enables the module hot-reload watchdog. |
| `LOG_LEVEL` | `info` | `debug`, `info`, `warning`, `error`. |

## MCP and tracking

| Variable | Default | Notes |
| --- | --- | --- |
| `MCP_ENABLED` | `false` | Mounts the MCP server at `/mcp`; read at boot. |
| `API_BASE_URL` | unset | Public origin for OAuth discovery. |
| `MCP_TOOLSETS` | empty | Empty means all except `admin`; `all` includes it. |
| `MCP_READ_ONLY` | `false` | Unregisters write tools at boot. |
| `MCP_REQUIRE_EGRESS_CONSENT` | `true` | Each user opts in before tools return their data. |
| `MCP_OAUTH_CLIENT_ID` | unset | Pre-registered public client; empty means PAT-only. |
| `MCP_RATE_LIMIT_PER_MINUTE` / `_BURST` | `120` / `40` | Per-user request budget. |
| `MCP_WRITE_RATE_LIMIT_PER_MINUTE` / `_BURST` | `30` / `10` | Tighter bucket for mutating tools. |
| `MCP_TOOL_TIMEOUT_SECONDS` | `30` | Per-call timeout. |
| `USER_TRACKING_ONBOARDING` | `force` | `force`, `on`, or `off` — see [Security and privacy](security-and-privacy.html#usage-analytics). |

> [!NOTE]
> Environment changes need a container **recreate** (`docker compose up -d`), not a restart: `restart` keeps the old environment.

# Backup, storage and limits
<!-- slug: backup-and-storage -->

Two things hold state — the metadata database and the filesystem (or the bucket) — plus the Keycloak volume.

## What to back up

| Asset | Where | Why it matters |
| --- | --- | --- |
| Metadata | Postgres (`app-db`) | Users, logs, jobs, settings, dashboards, shares, tokens. |
| Event data and results | `data/` or the S3 bucket | Parquet logs, module results, retained originals. |
| Keycloak | the `kc-data` volume | Accounts, realm settings, roles, IdP configuration. |
| Module sources | `modules/`, `data/uploaded_modules/` | Bundled and user-installed modules. |
| Environment | `.env`, `scripts/deploy.env` | Secrets and deployment configuration — keep them in a secret manager. |

```bash title="Backup, in four commands"
cd ~/mate
docker compose exec -T app-db pg_dump -U mate -Fc mate > mate-db-$(date +%F).dump
tar czf mate-data-$(date +%F).tgz data
docker run --rm -v kc-data:/v -v "$PWD":/b alpine tar czf /b/kc-data-$(date +%F).tgz -C /v .
cp .env mate-env-$(date +%F).bak        # encrypt this
```

In S3 mode the bucket already holds the authoritative event data; the platform also writes an hourly metadata snapshot to `_system/metadata.dump` (or `metadata.db` on SQLite deployments) and archives uploaded module sources to `_system/modules/`.

## Restoring

:::steps
1. **Stop the stack** so nothing writes during the restore: `docker compose down`.
2. **Restore the files**: `tar xzf mate-data-<date>.tgz` into the repository root.
3. **Restore identities**: unpack the `kc-data` archive into the volume.
4. **Restore metadata into an empty database, before the API starts**:

   ```bash title="Terminal"
   docker compose up -d app-db
   docker compose run --rm api python -m mate.api.storage.db_backup restore
   ```

5. **Start the stack**, then verify: sign in, open a process, run one module, check the storage gauge.
:::

`db_backup restore` refuses to write into a database that already has tables — deliberately, because restoring over a live schema produces a half-broken platform that is harder to diagnose than an empty one. From S3, the same command restores on a fresh VM *before* the first boot, after which logs hydrate on demand as users open them.

## S3 mode

| Aspect | Behaviour |
| --- | --- |
| Authoritative store | The bucket holds event logs, module results, the metadata snapshot, and uploaded module sources. |
| Local disk | A working cache. `LOCAL_CACHE_MAX_BYTES` bounds it; the reaper trims least-recently-used directories to 90 % of the budget. |
| Safety | A directory is deleted locally only after its bucket prefix is confirmed non-empty, and never while a job holds a lease on it. |
| Never synced | Staging, module virtual environments and bundles, cached interpreters — all reproducible. |

```bash title="Switching an existing deployment to S3"
# 1. set STORAGE_MODE=s3 plus the STORAGE_S3_* variables, then recreate the API
docker compose exec api python -m mate.api.storage.migration check     # probe + usage report
docker compose exec api python -m mate.api.storage.migration to_s3     # copy-only, re-runnable

# 2. set LOCAL_CACHE_MAX_BYTES, soak with CACHE_EVICT_DRY_RUN=true, then enable deletes
# to go back: run `to_local` while the mode still says s3, then flip the mode
```

Both migration directions are copy-only: they never delete the source.

## Fairness on a shared host

By default the platform behaves as if it owned the machine. On a shared VM, bound it:

| Setting | Suggested |
| --- | --- |
| `WORKER_CONCURRENCY` | 2–3 |
| `MODULE_PROCESS_POOL_SIZE` | 2–4 |
| `MAX_OFFLOADS_PER_USER` | At least one below the pool size, so no account can hold every slot |
| `DUCKDB_THREADS` / `DUCKDB_MEMORY_LIMIT` | Half the cores, half the RAM |
| Container memory limits | The shipped overlay: `api` 8 GB, `web` 768 MB, Keycloak 1 GB, databases 512 MB, proxy 128 MB |

Capacity rules of thumb: RAM = 2 GB baseline + the largest expected module working set + 1 GB headroom; disk = total log size × 3 for caches and working copies, plus 20 %; and keep `MAX_OFFLOADS_PER_USER` × expected users below `MODULE_PROCESS_POOL_SIZE`, or the cap never bites.

# Troubleshooting

Symptoms in the order people hit them, with the cause that usually explains them.

## Sign-in and identity

| Symptom | Likely cause |
| --- | --- |
| Every API call returns `401` after login | Issuer or JWKS mismatch — compare `KEYCLOAK_ISSUER` with the realm, and confirm the API can reach `KEYCLOAK_JWKS_URL`. |
| Login redirects forever | The realm's valid redirect URIs do not match the deployment. |
| Safari bounces between app and login | The session cookie exceeds 4 KB — set `SESSION_STORE_DIR`. |
| `/admin` returns `403` | The account lacks the `admin` realm role. |
| A role change has no effect | Roles come from the access token; wait for the next refresh or sign in again. |

## Imports

| Symptom | Likely cause |
| --- | --- |
| Import fails immediately | Unsupported or corrupt file — read the job's error and re-export the source. |
| Modules report unavailable after a successful import | Column roles are wrong — remap them in the log settings. |
| Time bounds empty, trends flat | The timestamp column was misidentified or misparsed. |
| One giant case in a CSV | The case id column was mapped to a near-constant column. |
| Staged upload vanished | Staging is swept after two hours — re-upload. |
| First import on a fresh host takes very long | Module environments are being materialised; later imports are fast. |

## Jobs and modules

| Symptom | Likely cause |
| --- | --- |
| Progress bar stalls with a "no progress" hint | The module reports nothing; cancel and retry, or fix the module. |
| Job fails with a timeout | It exceeds `JOB_EXECUTION_TIMEOUT_SECONDS`. |
| A module fails to load | Manifest, install, or import error — API log, then the module detail page's log tail. |
| Cards stay *Limited* | An optional dependency is missing. |
| Installed module does not appear | The install job failed and rolled back. |
| Subprocess module restarts repeatedly | A crash at worker startup; the bridge logs the exit code and attempt count. |

## Deployment

| Symptom | Likely cause |
| --- | --- |
| Proxy fails to start with TLS errors | Only `live/` was mounted, so the certificate symlinks into `archive/` break — mount all of `/etc/letsencrypt`. |
| An `.env` change has no effect | The container was restarted, not recreated. |
| A realm change has no effect | The JSON imports only into an empty database. |
| Disk grows without bound with S3 configured | No cache budget, or the reaper is still in dry-run. |
| Uploads fail with `507` | The bucket quota is reached. |
| Everything 500s after a deploy | A migration failed on boot — check `docker compose logs api` around the alembic step. |
| Job updates never arrive, the page works otherwise | Something in the proxy chain buffers `text/event-stream`. |

## MCP

| Symptom | Likely cause |
| --- | --- |
| `503` "MCP is disabled" | Boot flag off, or the live admin kill switch is on. |
| `401` on every call | Token missing, expired, revoked, or wrong OAuth audience. |
| `[consent_required]` | The user has not enabled external data access. |
| `[scope_missing]` | The token lacks the scope; mint a new one. |
| `[read_only]` on a write tool | Server-wide read-only. |
| `[conflict]` "no precomputed results yet" | The module has not run for this log. |
| `404` on `/mcp` | The flag never reached the container, or the proxy route is missing. |
| Output truncated | The 200 KB per-result cap — narrow the query or paginate. |

## Performance

| Symptom | Likely cause |
| --- | --- |
| Dashboards slow to paint | Many cards, each querying DuckDB — reduce cards, cap `DUCKDB_THREADS`. |
| One module slow only on large logs | Row-by-row Python instead of SQL. |
| API restarts under load | Memory pressure from a module — isolate it, or bound its allocations. |
| Everyone waits for one user's jobs | The offload pool is saturated — set `MAX_OFFLOADS_PER_USER`. |

## When you cannot tell

:::steps
1. Reproduce with a synthetic log of 200 cases — most module bugs show up immediately.
2. Collect **Settings → About → Copy diagnostics** and the failing job's log tail.
3. Read the module's raw manifest: `GET /api/v1/modules/{id}/manifest` shows what the platform actually parsed.
4. Search the API log for the log id — ingest and precompute log both.
5. Still stuck? Open an issue with the diagnostics blob, the steps, and the version: [github.com/Process-Science-Community/MATE/issues](https://github.com/Process-Science-Community/MATE/issues).
:::
