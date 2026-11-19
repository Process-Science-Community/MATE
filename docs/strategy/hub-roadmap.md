# MATE as the hub for process science: roadmap

**Status:** working plan, started 2026-11-18. Update it as items land.
**Pitch:** MATE is the Hugging Face for process science: one hub where the
community finds event-log datasets, modules (algorithms and methods), dashboards
and the research behind them. MATE is the hub, not the publisher: research and
datasets are indexed and linked to the original publisher, not re-published.

Related: [`extensibility-strategy.md`](./extensibility-strategy.md) (isolation
tiers T0/T1, polyglot wire protocol, MCP) and
[`../design/polyglot-module-architecture.md`](../design/polyglot-module-architecture.md).
Session handover with operational follow-ups: [§7](#7-open-operational-items-from-the-2026-11-18-session).

---

## 1. The core problem

Today a module's code runs **inside the API process** by default
(`isolation: in_process`), and even `isolation: subprocess` passes the API's full
environment (database URL, secrets) to the worker
(`apps/api/src/mate/api/modules/subprocess_host.py:293`,
`apps/api/src/mate/api/modules/job_worker.py:149`). There is no uid drop, no
rlimit and no filesystem or network restriction. A module can read every
account's data. Until 2026-11-18 any non-`reviewer` account could upload one
(`POST /api/v1/modules/install`), which made upload a remote-code-execution path
for every user. That is now off by default (§5, stage 0).

Other confirmed gaps, independent of upload:

| Gap | Where | Note |
| --- | --- | --- |
| Module routes check authentication, not module ownership | `modules/loader.py:1236-1244` (`_bind_route`) | Any signed-in user can call any loaded module by URL. Also named in `extensibility-strategy.md`. |
| Module asset/bundle route has no ownership check | `routes/modules.py` (`/{module_id}/assets/{asset_path:path}`) | |
| `events.py` is always imported in-process | `modules/loader.py:_register_module_events`, called from `_instantiate` | Even for `isolation: subprocess` modules. |
| Install runs `uv pip install` of manifest-declared packages | `modules/installer.py:250` (`install_module`) | Install time is code execution. |
| Module frontend bundles run via `new Function` in the browser | `apps/web/lib/module-panels.tsx:89`, `module-widgets.tsx:95` | Same origin as the app. |
| Module config (may hold AI API keys) is sent to workers | `modules/ctx_rpc.py:106-112` | See invariant in `extensibility-strategy.md`. |
| Entry-point packages (`mate.modules`) are a second code-loading path | `modules/discovery.py:126` | Operator-only (needs a pip install on the server). |
| Archive extraction has no size or zip-bomb cap | `modules/install_jobs.py:131-140` | Path traversal is handled; size is not. |

## 2. What Hugging Face does (researched 2026-11-18)

Main lesson: **publishing is open, execution is separated**. The Hub stores and
scans what people publish; community code runs only in isolated containers or
on the user's own machine, not in the Hub's own server process.

- **Publishing.** Every model, dataset and Space is its own git repo under a
  user or org namespace. Each push is scanned: ClamAV, a pickle import scan, and
  TruffleHog for secrets. HF calls the scan "not 100% foolproof"; there is no
  review before publishing. Moderation happens afterwards: public report button,
  content policy, a ladder from warning to suspension.
  [security-malware](https://huggingface.co/docs/hub/security-malware),
  [security-pickle](https://huggingface.co/docs/hub/security-pickle),
  [security-secrets](https://huggingface.co/docs/hub/security-secrets),
  [content-policy](https://huggingface.co/content-policy).
- **Spaces (apps).** Each Space is built into its own container; per the docs, a
  new VM is provisioned for each new build. It runs as a non-root user (UID 1000),
  has its own secrets (not copied when duplicated), default limits of 2 vCPU,
  16 GB RAM and 50 GB ephemeral disk, outbound traffic only on ports 80/443/8080,
  and reaches Hub data through the API with a token or read-only mounts, never a
  database. The exact sandbox technology is not documented.
  [spaces-overview](https://huggingface.co/docs/hub/spaces-overview),
  [spaces-dev-mode](https://huggingface.co/docs/hub/spaces-dev-mode),
  [spaces-sdks-docker](https://huggingface.co/docs/hub/spaces-sdks-docker).
- **Compute is gated.** Creating Gradio or Docker Spaces now needs a paid plan;
  free accounts get up to 2 ZeroGPU Gradio Spaces, and static Spaces are free.
  Free Spaces sleep after 48 h of inactivity.
- **Remote code runs locally by opt-in.** `trust_remote_code=True` downloads a
  repo's Python and runs it on the caller's machine; HF recommends pinning a
  commit (`revision=<sha>`). Inference Endpoints run repo code (`handler.py`) in a
  dedicated container the deployer chooses and pays for.
  [transformers models](https://huggingface.co/docs/transformers/main/en/models),
  [endpoints toolkit](https://huggingface.co/docs/inference-endpoints/en/engines/toolkit).
- **Datasets are data only.** `datasets` 4.0 removed dataset loading scripts.
  The dataset viewer converts datasets to Parquet in background workers and
  serves only precomputed results.
  [datasets 4.0.0](https://github.com/huggingface/datasets/releases/tag/4.0.0),
  [dataset-viewer](https://github.com/huggingface/dataset-viewer).
- **It still went wrong.** In 2024 a malicious pickle model got a shell inside
  HF's inference infrastructure and could move across tenants via node
  credentials; a Spaces Dockerfile build step reached a shared registry
  ([Wiz](https://www.wiz.io/blog/wiz-and-hugging-face-address-risks-to-ai-infrastructure)).
  Separately, Spaces secrets were accessed without authorisation
  ([HF](https://huggingface.co/blog/space-secrets-disclosure)). HF responded with
  segmentation, KMS for secrets and fine-grained tokens.
- **Open core, not open source.** The client libraries and formats are open
  (`huggingface_hub`, `datasets`, `transformers`, `gradio`, `safetensors`, all
  Apache-2.0). The Hub itself is closed: the dataset-viewer README says its
  frontend "is not open-source, as the rest of the Hub". On-prem Private Hub was
  discontinued ([blog](https://huggingface.co/blog/introducing-private-hub)).
  Self-hostable clones exist: KohakuHub (AGPL), CSGHub (Apache-2.0, Gitea-based).
  **MATE being fully open and self-hostable is a differentiator.**
- **Papers: indexer, not publisher.** Paper pages are keyed by arXiv ID, link out
  to arXiv for abstract and PDF, auto-link repos whose README cites the paper,
  and let authors claim papers (verified badge). Only arXiv is supported, so this
  does not fit process science, which mostly publishes with DOIs (BPM, ICPM,
  LNBIP, IEEE). [paper-pages](https://huggingface.co/docs/hub/paper-pages).

## 3. Concept mapping

| Hugging Face | MATE | Notes |
| --- | --- | --- |
| Dataset | Event log (XES, OCEL, CSV) | Data only, parsed by MATE's own code. Needs a dataset card (licence, DOI, source such as 4TU, citation). Mirror and link, do not re-publish. |
| Model | Process model (Petri net, BPMN, PNML) | The output of an algorithm. Possible new artifact type. |
| Space | Module | Code plus UI running on data. Therefore modules need Space-style isolation. |
| (none) | Dashboard | A no-code composition: configuration only, no code, safe to share. HF has no equivalent. |
| Collection | Case study | Paper + log + modules + dashboard as one bundle. |
| Paper page | Research index keyed by DOI (arXiv when present) | Metadata from Crossref, OpenAlex, DBLP; link to the publisher; MATE AI can search it. |

## 4. Principles we take from this

1. **Separate publishing from execution.** Anyone may eventually publish; only
   sandboxed or reviewed code runs on a hosted instance.
2. **Never run community code in the API process.** Module runs get their own
   container: non-root, no secrets, no database credentials, the event log
   mounted read-only, no outbound network by default, CPU/memory/time limits,
   and a short-lived scoped token for the `ctx` RPC (`modules/ctx_rpc.py` is the
   starting point). HF only restricts ports; a deny-by-default egress policy
   would be stricter than HF.
3. **Unreviewed code runs locally.** That is HF's `trust_remote_code` model and
   MATE's lite mode: `make lite`, put any module in `modules/`, import your own
   XES or CSV.
4. **Pin versions.** A run references a module by id and exact commit or image
   digest, so code cannot change after review.
5. **One shared monorepo does not scale to a community hub.** The pull-request
   flow into `modules/` is the right **official / verified tier**. A community
   tier needs modules in their authors' own repositories (or Zenodo with a DOI),
   indexed by MATE, with pinned commits.
6. **Compute costs money.** Per-user quotas (CPU, memory, runtime, concurrent
   runs) before any community code runs on a university-hosted instance.
7. **Cards everywhere.** Module and dataset cards: licence, citation/DOI,
   authors (ORCID), inputs and outputs (for example event log → Petri net),
   tags. Validated on publish. Paper links derived from cited DOIs.

## 5. Staged plan

### Stage 0 – done (2026-11-18)

- [x] `MODULE_UPLOAD_ENABLED` setting, default `false`; upload refused with 403
      and the import page explains the pull-request and lite-mode routes. Lite
      mode turns it on for itself. While it is off, modules uploaded earlier are
      not loaded either (`ModuleLoader.load_all` skips `data/uploaded_modules/`
      and logs `modules.loader.uploads_skipped`; S3 restore of uploads is
      skipped). Folders and install rows stay, so switching it on restores them.
- [x] Watched folders on a custom path are admin-only (REST and MCP).
- [x] DuckDB `DUCKDB_THREADS` / `DUCKDB_MEMORY_LIMIT` applied to every
      connection (`duckdb/pool.py:apply_resource_limits`).
- [x] Security audit and fixes, see [§8](#8-security-audit-2026-11-18).

### Stage 1 – make the pull-request flow safe and usable

- [ ] CI on pull requests: `make validate`, `mate-module test` for changed
      modules, `make test`, `pnpm typecheck`. Only `.github/workflows/pages.yml`
      exists today.
- [ ] PR template and `CODEOWNERS` for `modules/`; a review checklist for module
      code (network, filesystem, subprocess, dependency list).
- [ ] ~~Ownership check on module routes and assets~~ Superseded: with
      repo-only modules, per-user installs are a display preference, not access
      control, and enforcing them breaks shared dashboards, admin controls and
      first visits. Replaced by the module-model simplification below.
- [ ] Module-model simplification: replace per-user install/uninstall with
      catalog (loaded from `modules/`) + per-user active flag
      (`module_configs.enabled`, default from manifest `default_enabled`);
      remove upload machinery, install tables and the admin
      default/withhold/force options. Staged plan drafted 2026-11-18 (stages:
      defuse S3 upload GC + backup → remove upload → switch owned→loaded/active
      with data migration → drop `module_installs`). Approved 2026-11-18; full
      plan and decisions in [`module-model-plan.md`](./module-model-plan.md).
- [ ] Opt-in modules: `default_enabled: false` in the manifest (part of the
      simplification above).
- [ ] Users can download their own event logs (today only admins can, via
      `GET /admin/event-logs/{log_id}/download` in `routes/admin_insights.py`;
      the `/admin/export/*` routes in `routes/admin.py` export usage-tracking
      data, not logs), so they can test modules locally on real data.
- [ ] Module card and dataset card spec (fields in §4.7), validated by
      `mate-module validate`.
- [ ] Migrate or drop the two production uploads `case_count` and `num_cases`
      (they currently fail to load because of the removed `author:` field), then
      plan deleting the upload code: `modules/install_jobs.py`, the upload route,
      S3 module archive/restore/gc, admin-declared defaults, `/modules/import`.

### Stage 2 – sandboxed module runner (MATE's Spaces)

- [ ] Every module run in its own container, as in principle 2. Builds this on
      the T1 tier in `extensibility-strategy.md` and goes further (container,
      not only rlimits).
- [ ] No secrets in the worker environment; config secrets never cross to an
      untrusted tier.
- [ ] Frontend bundles isolated (sandboxed iframe or a separate origin) instead
      of `new Function` on the app origin.
- [ ] Per-user quotas and per-run timeouts; failing modules suspended.
- [ ] Then: community modules from external repositories, pinned by commit, with
      scanning on publish (gitleaks or TruffleHog, pip-audit, ClamAV) and a report
      button.

### Stage 3 – the hub

- [ ] Dataset pages for event logs: card, preview (precomputed, as HF's dataset
      viewer), licence and gated access for sensitive logs, mirroring of public
      logs (4TU, BPI Challenges) with DOI and link to the source.
- [ ] Shared example logs (stored once, visible to every user, read-only), see
      §7.2; this is also the first step towards dataset pages.
      Both items: proposal in [`dataset-hub-plan.md`](./dataset-hub-plan.md)
      (brought forward for the course case-study log).
- [ ] Research index keyed by DOI (arXiv when present): metadata only, link to
      the publisher, auto-link modules and datasets whose cards cite the DOI,
      author claiming via ORCID.
- [ ] Collections (case studies) bundling paper, log, modules and dashboards.
- [ ] MATE AI over the index (papers, modules, datasets).
- [ ] Federation: self-hosted MATE instances, one public catalog.

## 6. Decisions log

| Date | Decision | Why |
| --- | --- | --- |
| 2026-11-18 | Module upload off by default on shared instances | Uploaded code runs unsandboxed in the API; any user could read all data and secrets. |
| 2026-11-18 | Modules ship by reviewed PR into `modules/` for now | Review is the only control until the sandbox exists. |
| 2026-11-18 | Unpublished modules run locally (lite mode) | Same model as HF `trust_remote_code`. |
| 2026-11-18 | Disable now, delete the upload code later | Disabling closes the hole immediately; deletion touches about 40 places across API, web, tests and docs. |

## 7. Open operational items from the 2026-11-18 session

### 7.1 Usage tracking and privacy (Process Thinking course, 30–60 students)

Production runs `USER_TRACKING_ONBOARDING=force`, exempting only the `reviewer`
role. Everyone else is tracked, including admins, and cannot opt out.

- [ ] **Inform students before the case study** (GDPR Art. 13); for research
      use, consent and the university's data-protection/ethics process. Decision
      for the chair, not code.
- [ ] Add a visible tracking notice for `force` mode (today the privacy step and
      Settings → Privacy are hidden and nothing is shown).
- [ ] Fix the privacy text: `settings/privacy/page.tsx` says the id is "not tied
      to any account" and data stays in local SQLite; on the hosted instance
      events carry `user_id` (joins to email) and live in Postgres.
- [ ] Add a privacy policy page (none exists).
- [ ] Retention: data is kept forever by default (`retention_days` is per user
      and unset).
- [ ] Exempt staff from tracking (`USER_TRACKING_EXEMPT_USERS`) or filter at
      export.
- [ ] Tracking gaps for analysing a case study:
  - module parameter values (Radix Select/Slider/Switch fire no native `change`);
  - Events-tab filter contents;
  - module run/retry/cancel as semantic events;
  - process-detail tab switches (`?tab=`, query params are dropped);
  - visualisation interaction (pan, zoom, selection);
  - dashboard building (react-grid-layout moves are invisible);
  - exports;
  - `page_leave` / dwell time;
  - batches dropped on network errors without retry.
- Data export for analysis: `/admin/export` (XES, CSV, NDJSON, OCEL 2.0), the XES
  case can be session or user.

### 7.2 Large case-study log (591 MB XES, 30–40 students)

- Upload works (no size limit in Caddy, Next or the API; the university edge
  proxy is unverified). The import loads the whole log into memory inside the
  API process (8 GB container limit), so **test-import it once and watch
  `docker stats mate-api`** before the course.
- Do not let every student upload it: each upload is a separate copy, import and
  discovery run, roughly 40 × (original + Parquet + results).
- No "example log for every user" feature exists. Options today: a shared
  dashboard via an admin team (one copy, read-only, no log pages; a student's
  filter recomputes live), or `SAMPLE_CONTENT_MANIFEST` seeding (one copy per
  listed account, listed accounts only, at API start).
- Proposed feature: admin marks a log as an example log; it is stored once,
  visible to every user including new ones, read-only, shared result cache, with
  per-user filters. Medium-sized: relax owner-only checks on the read paths
  (`get_owned_event_log`, `EventLogAccess`, module context, dashboard binding)
  and make the active filter per user.

### 7.3 Smaller follow-ups

- [ ] Uploaded modules `case_count` and `num_cases` fail validation because of
      the removed `author:` manifest field (use `source:`); see stage 1.
- [ ] The production deploy uses a server-local `docker-compose.limits.yml`
      (API 8 GB, web 768 MB, log rotation). The deploy command must include it;
      the server's `~/.bashrc` sets `DC` accordingly.
- [ ] Module routes triggered over HTTP skip `MAX_OFFLOADS_PER_USER`
      (`jobs/runtime.py`, `_CURRENT_USER` is only set inside jobs).
- [ ] `docs/INSTRUCTIONS.md` documents git-URL and registry module installs that
      do not exist.

### 7.4 Done in the same session (for context)

- Login: Auth.js errors go to `/login` with a "try again" notice instead of the
  "Server error / server configuration" page (`pages.error`). The trigger was an
  expired PKCE cookie (`InvalidCheck`), not a server problem.
- Admin insights (overview, users, jobs, usage) ran SQLite-only SQL (`julianday`,
  `strftime`, `json_extract`) and failed on Postgres; now dialect-aware.

## 8. Security audit (2026-11-18)

Triggered by an external responsible-disclosure report: an uploaded module could
read the shared database (upload was already being disabled that day). Followed by
a two-pass code audit and a live pen-test against a production-faithful local
stack (Postgres, `ENV=prod`, real JWT auth with locally minted tokens for two
users and an admin), then a read-only scan of the VM.

### Fixed and deployed

| Finding | Fix |
| --- | --- |
| Uploaded module code ran unsandboxed (RCE for any account) | Upload off by default; earlier uploads not loaded (stage 0) |
| Demo bypass (`Bearer demo-access-token`) not pinned off in prod | `Settings` refuses `ENV=prod` + `DEMO_MODE`; prod compose pins it off; `.env.example` defaults false. Prod was checked: never open |
| SSRF via `POST /event-logs/from-url` and user-set AI `base_url` | `net_guard.assert_public_url` (public addresses only, redirects re-validated) |
| `/event-logs/probe-xml`, `/probe-json` without auth or size cap | `CurrentUserDep` + capped sample stream |
| Sharing a dashboard exposed all module outputs of its log | `sharing.user_can_access_log_module`: only modules the shared board places |
| Custom watched-folder paths for any account | Admin-only |
| Keycloak admin console and `master` realm public | Caddy 404 except via SSH tunnel (`docs/DEPLOY.md`) |
| App database on the default password | Rotated |
| No DB backup; world-readable dumps; no SSH brute-force protection | `scripts/backup-db.sh` nightly, owner-only; fail2ban on the VM |

### Attacked and held

Cross-tenant access on every id-bearing route (404), forged / `alg=none` /
wrong-audience tokens (401), non-admin on admin routes (403), SQL injection
(422), path traversal (404), XXE in XES upload, SSE cross-tenant leak, unbounded
pagination, CORS reflection, MCP scopes and PAT privilege, AI key read-back.

### Open

- [x] `NODE_TLS_REJECT_UNAUTHORIZED=0` removed from the web container: all
      server-side Keycloak calls use `KEYCLOAK_INTERNAL_URL`, and `auth.ts`
      passes the authorization endpoint explicitly so Auth.js runs no discovery
      against the public hostname.
- [ ] API, web and proxy containers run as root inside the container.
- [ ] CSP is `Report-Only` (`infra/caddy/Caddyfile`); enforcing needs a policy
      that still allows module panels (`new Function`).
- [ ] SSRF guard: DNS-rebinding race (resolve, then connect) is not closed; a
      self-hosted AI provider on a private address is now refused and would need
      an operator allowlist.
- [ ] AI chat shared-log context still uses the log-wide share check (bounded by
      the AI data wall).
- [ ] SSH on the VM still accepts passwords (owner decision); fail2ban mitigates.
- [ ] Module code is not sandboxed (stage 2).
