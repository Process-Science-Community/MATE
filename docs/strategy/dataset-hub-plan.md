# Public & private datasets in MATE: implementation plan

> **Summary**
> 1. A **hub dataset** is a catalogued, licensed, immutable revision of an event log. Admins publish it in Phase 1. It is stored once under `data/datasets/`.
> 2. "Use this dataset" gives the student **their own log**. Stage 1 copies the Parquet and the canonical results (copy-on-use). A zero-copy *reference log* comes later, only if the Stage 0 numbers call for it.
> 3. Every derived log keeps a lineage pointer and a license snapshot, so attribution, gating and takedown reach copies, dashboard shares and MCP/AI output.
> 4. Visibility is `private | restricted (team) | public`. Pseudonymised data is personal data: it can never be `public`, and anonymous `reviewer` accounts see only open-licensed, ungated data.
> 5. The course case-study log is the first dataset. Stage 0 measures import RSS, Parquet size and Arrow `nbytes`, and the chair answers the rights and privacy questions before anything goes beyond `private`.

Status: proposal, 2026-11-19, revised after the security, storage-scale, legal-license and UX-scope reviews. Scope: hub-roadmap Stage 3 items "Dataset pages" and "Shared example logs" (`docs/strategy/hub-roadmap.md:198-202`, §7.2 `:252-268`). They are brought forward for the course case-study log: 591 MB XES, 30-60 students; the load test targets 60.

Code claims below were checked against `/home/user/MATE` unless marked *(unverified)*.

**Owner input (2026-11-19):**
- Course start: as soon as possible. Ship the course path (Stage 0 → 1a → 1b) first.
- The chair's answers on the case-study log (§8 questions) are pending. Walk through the §10
  decisions with the owner once they are in; until then the recommendations are not approved.
- The module side may change: if modules always run isolated (like HF Spaces/models), the
  per-log sidecar and cache layout changes too. Copy-on-use fits that model (an isolated runner
  gets its own read-only input), so keep the hub-dataset storage independent of module internals.
- The owner found "datasets" a misleading name for module data outputs; they are now "module
  outputs" (done, see §2).

---

## 0. Core recommendation (TL;DR)

- **A dataset is a published, immutable, catalogued revision of a parsed event log, stored once under `data/datasets/{id}/r{n}/`.** No student reads it in place in Stage 1.
- **"Use this dataset" = copy-on-use.** It creates an ordinary `process_logs` row owned by the student and copies only `events.parquet` and `cases.parquet` (never `original.*`) plus whitelisted canonical module results into the student's own dirs. The row carries `dataset_revision_id` (lineage, license, takedown) and the snapshot row metadata.
- **Why copy-on-use first and not reference logs.** Every existing read and write path, owner check, S3 hydrate, sidecar dir and module freshness check keeps working unchanged. The reviews found that reference logs break in all of these places:
  - module sidecar uploads land in the shared dir (`conformance/module.py:47-48`, `concept_drift_explainer/module.py:52-53`);
  - `_cache_is_fresh` bypasses a seed overlay (`complexity/module.py:34-47`);
  - MCP lists `cache.dir.iterdir()` (`mcp/toolsets/analysis.py:295`);
  - S3 hydrate is keyed by `(user_id, log_id)` (`event_log_access.py:214`).
- **What copy-on-use costs.** 60 × (Parquet + results) on disk, plus one zstd Parquet copy per use. Stage 0 measures both; Parquet is expected to be roughly 10-20× smaller than the XES *(unverified until measured)*. If the measured disk exceeds budget, reference logs (§2, Stage 4b) replace the copy behind the same `use` API.
- **Students may edit their copy.** It is their log. The lineage pointer survives edits, duplication and "materialise", so the license banner and takedown still apply. Nothing a student does can touch `data/datasets/`, because no student path resolves there.
- **Canonical results are file copies (`copy2`, mtimes kept).** Run rows (`status="ready"`) are created lazily in `ensure` when the seed matches. That way a filter commit only re-runs modules the student actually opened (`refresh_working_set`, `processing.py:584-606`).
- **Publishing is admin-only and non-sandboxed in Phase 1**, via `may_curate_datasets(roles) = ADMIN_ROLE in roles and not is_sandboxed(roles)`. `require_admin` does not check sandboxing (`dependencies.py:222-231`).
- **Today's private event log already gives a private log with no catalog entry.** A `private` hub dataset is a curator's draft. A `restricted` dataset is visible only to members of one team, for course-only or partner terms. A `public` dataset is visible platform-wide, which includes the anonymous reviewer accounts.

---

## 1. HF comparison

| Concept | Hugging Face | MATE today | MATE proposal | Verdict |
|---|---|---|---|---|
| Identity | git repo `namespace/name`, `/datasets/{ns}/{name}` | `process_logs.id` UUID, `/processes/{logId}` | `hub_datasets` `(namespace, name)` unique, UUID PK; web `/datasets/{ns}/{name}` | **Adopt** |
| Namespace | user or org | users; admin-managed `teams` (`models.py:690-728`) | Phase 1: admin-set namespace (`mate`, `pm-course`). Stage 5: bound to a team | **Adapt** |
| Visibility | public / private (404 to others); orgs for audience | everything private, 404 to others | `private` (draft), `restricted` (one team), `public`. Non-readers get 404 | **Adapt** (team audience in place of HF org-private) |
| Gating | off / auto / manual | none | `none` or `accept_terms` (per user + revision + terms hash). Manual approval deferred | **Adapt** |
| Who publishes | anyone; Enterprise can force private | n/a | `may_curate_datasets` (admin, not sandboxed). User requests with approval in Stage 5 | **Adapt** |
| Card | README + YAML | `EventLog.description` | Stage 1: `summary` + markdown `card_md` + license fields. Structured card validator later | **Adapt** |
| License | ~80 ids or `other` | none | curated SPDX-mapped list incl. `4tu-general-terms`, `other`, `unknown` (blocks publish beyond `private`) | **Adopt** (shorter list) |
| Auto stats | dataset_info, size categories | counts, dates, `detected_schema`, `column_roles` on `EventLog` (`models.py:121-140`) | snapshotted into the revision at publish, plus per-attribute cardinality | **Adopt** |
| Search | name search, facet tags, sorts | `GET /event-logs?q=` own logs only (`routes/event_logs.py:700-711`) | Stage 1: `q` LIKE over title/summary/slug + license filter. Tags and facets: Stage 3 | **Adopt**, staged |
| Downloads / likes | downloads per IP; likes | n/a | `uses` = distinct users with a derived log. Likes deferred | **Adapt** |
| Versions | git revisions | mutable `data_revision` | immutable `hub_dataset_revisions`; derived logs pin a revision | **Adapt** (no git) |
| Fork | `duplicate_repo` | `duplicate_log` copytree (`log_aggregates.py:1167-1229`) | "Use" = copy of Parquet + results with lineage | **Adopt** |
| Viewer | Data Studio | Events/Variants/Activities tabs | detail page: snapshotted stats, top activities and variants, first 50 rows | **Adopt** |
| Quotas | private capped | none per user; S3 global (`storage/quota.py`) | `use` idempotent per (user, revision); disk measured in Stage 0 | **Adapt** |
| DOI | DataCite minting | none | store `source_doi`; cite button prefers the upstream DOI; no minting | **Skip** minting |
| Moderation | Report, DMCA/DSA | none | Stage 1: `disable` + purge + data-owner/DPO `mailto:` on the page. Report queue later | **Adapt** |
| ToS license grant | HF ToS gives an irrevocable grant | **no MATE ToS or privacy policy** (roadmap §7.1) | irrevocability only where the license itself grants it (CC/ODC); custom terms follow the rights holder | **Adapt**: no ToS to inherit |
| Billing, SSO/SCIM, geo-block | yes | n/a | none | **Skip** |

---

## 2. Concept and vocabulary

**Naming: done.** "Datasets" used to also mean module data outputs. That concept is now
**module outputs** everywhere:
- manifest `datasets:` → `outputs:` (a manifest that still has `datasets:` fails validation);
- `/api/v1/datasets/catalog` → `/api/v1/module-outputs/catalog` (`routes/module_outputs.py`);
- package `mate/api/datasets/` → `mate/api/module_outputs/` (`envelope.py`, `adapters.py`, `transforms.py`);
- MCP `list_datasets`/`get_dataset` → `list_module_outputs`/`get_module_output_data`, no aliases;
- layout and dashboard cards `dataset_ref`/`dataset_id` → `output_ref`/`output_id` (stored layouts
  rewritten by migration `0017_rename_module_outputs`, so the hub migration in §3 takes the next
  free number).

So "dataset" means only a published event log, and the hub concept is simply **dataset** in code
and UI, as on HF: the UI says **"Datasets"** at `/datasets`. The `hub_` prefixes used further down
this plan (`hub_datasets*`, `/api/v1/hub/datasets`, `search_hub_datasets`/`get_hub_dataset`) are the
pre-rename draft names; drop the prefix when building them. "Hub dataset" in the prose below means
the same thing. MCP tool descriptions should say "dataset (published event log)", because a client
may have cached the old module-output tool `get_dataset` from an earlier session.

| Term | Meaning | Storage | Mutable? |
|---|---|---|---|
| **Event log** (private) | today's `process_logs` row | `data/users/{uid}/event_logs/{log_id}/` | yes, owner |
| **Hub dataset** | catalog entry: slug, card, license, visibility, audience team, gating, status | DB | card: curator; data: no |
| **Dataset revision** | immutable snapshot: Parquet + stats + preview + canonical results | `data/datasets/{id}/r{n}/data/` and sibling `r{n}/module_results/` | never |
| **Derived log** (Stage 1 "use") | student's own row with `dataset_revision_id` + license snapshot; Parquet and results copied into own dirs | own dirs | yes, owner; lineage kept |
| **Reference log** (Stage 4b, conditional) | derived log with no Parquet copy, reads the revision dir | row + own results/sidecar dir | metadata/filter only |

**Why not one shared row (roadmap §7.2 sketch):**
- `active_filter` sits on the row (`models.py:146-150`);
- `log_module_runs` PK is `(log_id, module_id)` with one `user_id` (`models.py:364-400`);
- SSE drops foreign `user_id` (`events_sse.py:111-116`);
- it would mean widening ~35 owner checks.

All three reviews confirmed this reasoning.

**Why copy-on-use before reference logs.** Reference logs need, before they are safe:
- (a) a per-(user, log, module) writable sidecar dir, with `conformance` and `concept_drift_explainer` migrated to it;
- (b) a path-keyed, single-flight S3 hydrate (`sync.py:89` treats a dir as warm on its first file);
- (c) a seed overlay that the module freshness checks, MCP `iterdir`, `ResultCache.exists` and the subprocess/JVM `cache_dir` (`ctx_rpc.py:126-128`, `PROTOCOL.md:204-210`) all honour;
- (d) a read-only `:ro` nested bind mount of `data/datasets`. The api runs as root (no `USER` in `apps/api/Dockerfile`), so `chmod` gives no protection.

Copy-on-use needs none of these. Hardlinks and symlinks stay **rejected**: `dispatch.py:285,289` writes `to_parquet` in place and would truncate the shared inode.

**Operations on a derived log (Stage 1):** all existing operations are allowed, because it is an ordinary owned log.
- Cell edit, bulk fill, reimport and remap work on the student's own copy. Reimport and remap are **disabled**, because no `original.*` is copied; the UI hides them and the API returns 409 `no_original`. "Re-derive from dataset" creates a fresh derived log.
- `duplicate_log` propagates `dataset_revision_id` and the license snapshot. It must also copy `column_roles` and `mapping_needs_review`, which it drops today (`log_aggregates.py:1194-1222` copies only `detected_schema`); fix this in the same PR.
- `delete_log_and_data` is a soft delete (`deleted_at`) plus rmtree of the own log dir (`log_aggregates.py:1232-1255`). It never touches `data/datasets/`.

---

## 3. Data model + migration

Migration `0018_hub_datasets.py` with `down_revision = "0017_rename_module_outputs"` (latest is `0017_rename_module_outputs.py`). It is additive and uses `op.batch_alter_table("process_logs")` for the new FK column. SQLite cannot ALTER-add a constraint, and Alembic's engine does not set `PRAGMA foreign_keys=ON` (only `db/engine.py:30` does), so the batch recreate does not cascade into `log_module_runs`; state this in the migration.

Avoid the circular FK: `hub_datasets.current_revision_id` is a plain column, enforced in the service. Add `tests/test_hub_migration.py` (pattern: `test_card_migration.py`) to run upgrade and downgrade on SQLite; the suite itself only does `create_all` (`tests/conftest.py:65`).

**`hub_datasets`** (Stage 1)
- `id` String(36) PK; `namespace` String(64), `name` String(96), `UNIQUE(namespace,name)`, regex `^[a-z0-9][a-z0-9-]{1,63}$`
- `title`, `summary` String(300), `card_md` Text
- `visibility` (`private|restricted|public`, default `private`); `audience_team_id` FK `teams.id` SET NULL (required for `restricted`)
- `status` (`active|deprecated|disabled`), `disabled_at`, `disabled_reason`, `quarantine_until`
- `gating` (`none|accept_terms`), `gating_prompt`
- `license_id` NOT NULL, `license_name`, `license_url`, `license_text`, `attribution`
- `allow_external_ai` Boolean, default false for `other`, `4tu-general-terms` and every gated or restricted dataset
- `source_doi`, `source_url`, `citation_bibtex`, `contact` (data owner / DPO `mailto:`)
- `retention_until` Date, nullable
- `current_revision_id` (no FK); `created_by` FK users **SET NULL**; `created_at`, `updated_at`, `published_at`
- Indexes `(visibility,status)`, `(namespace)`

**`hub_dataset_revisions`** (Stage 1)
- `id` PK, `dataset_id` FK CASCADE, `number`, `UNIQUE(dataset_id, number)`
- `source_sha256` (sha of the published `events.parquet`), `source_format`, `log_model`
- Row snapshot copied onto every derived log, as `duplicate_log` does today: `detected_schema`, `column_roles`, `column_overrides`, `mapping_needs_review`, counts, `date_min/max`, `variants_count`
- `bytes_parquet`; `attributes` JSON (name, dtype, scope, null ratio, cardinality, unique-per-case share, free-text flag)
- `preview` JSON (first 50 events, top 20 activities and variants)
- `canonical_results` JSON: `{module_id: {module_version, config_hash, keys}}`
- `declaration` JSON (§4); `declared_by` SET NULL plus `declared_by_name` snapshot (deliberate accountability record, disclosed in the privacy notice)
- `changelog`, `created_at`
- **No stored path.** The path is derived: `datasets_dir / safe_segment(dataset_id) / f"r{int(n)}"` and asserted with `resolve().is_relative_to(datasets_dir)`. `safe_segment` accepts a single segment only (`path_segments.py:18-22`).

**`hub_dataset_acceptances`** (Stage 1 only if the chair requires gating): `(dataset_id, user_id, revision_id)` PK, `accepted_at`, `terms_sha256`. User FK CASCADE is a recorded trade-off: the compliance record goes with the account (decision 10.10).

**`process_logs`**
- `dataset_revision_id` FK → revisions **ON DELETE SET NULL**, indexed
- `license_snapshot` JSON (`slug`, `title`, `revision`, `license_id`, `attribution`, `allow_external_ai`, `gating`)
- Lineage is never cleared by edits or duplication. Purge does not null it. Purge deletes the derived data (§4), so SET NULL only covers rows that are already gone or soft-deleted.

**Later stages, each with its own migration:** `hub_dataset_tags` (prefixed facet tags) and `search_text` in Stage 3; `hub_dataset_reports` in Stage 3; likes in Stage 3; `origin`, `domain`, `language`, `properties` and XES extension metadata in Stage 3.

**License list** `hub/licenses.py`: `(id, spdx, name, url, redistribution, commercial, attribution, share_alike)`.
- Ids: `cc0-1.0`, `cc-by-4.0`, `cc-by-sa-4.0`, `cc-by-nc-4.0`, `cc-by-nc-sa-4.0`, `odc-by-1.0`, `odbl-1.0`, `pddl-1.0`, `4tu-general-terms`, `other`, `unknown`.
- `unknown` blocks anything beyond `private`. `other` requires a name plus a URL or text.
- Served at `GET /api/v1/hub/licenses`.
- "Open" licenses (for reviewer visibility and default egress): `cc0-1.0`, `cc-by-4.0`, `cc-by-sa-4.0`, `odc-by-1.0`, `odbl-1.0`, `pddl-1.0`.

---

## 4. Permissions, privacy, isolation

**Roles**
- `may_curate_datasets(roles) = ADMIN_ROLE in roles and not is_sandboxed(roles)`, next to `may_watch_custom_path` (`dependencies.py:251`).
- It gates every hub write route and MCP tool. Test: an admin+reviewer token gets 403.
- `curator` role comes in Stage 5.

**Read predicates** (in `sharing.py`):
- `can_read_hub_dataset(ds, user)`:
  - `status != disabled`;
  - and `public`, or `restricted` with the user a member of `audience_team_id`, or the user is a curator;
  - and, for a sandboxed user, only `public` + open license + `gating == none`. Reviewer accounts are anonymous external conference reviewers (`config.py:85-89,122-130`).
- `can_use_hub_dataset` = read + current-terms acceptance when gated.
- Non-readers get 404.

**Row-level takedown gate.** `ensure_log_usable(row)` refuses with 410 `dataset_unavailable` when the row's revision belongs to a `disabled` dataset. It is called in:
- `get_owned_event_log`;
- MCP `ensure_owned_log`;
- `ModuleProcessingCoordinator._read`/`list_states`;
- `_make_context`, **outside** its `try/except Exception` (`loader.py:1696-1747`, which swallows exceptions today);
- the dashboard share read path.

It covers cached results, AI guidance (`routes/ai_guidance.py`, 5 `ResultCache` sites) and MCP, because all of them pass one of these gates.

**Takedown and unpublish**
- *Unpublish* (`→ private`): no new uses; existing derived logs keep working. Irrevocability holds only if the license grants it: CC/ODC yes; `other`/4TU per the terms text. The confirm dialog says which applies.
- *Disable*: catalog 404; every derived log returns 410 (logs stay listed with the reason); `quarantine_until` defaults to 30 days.
- *Purge* (typed confirm; automatic at quarantine end for `personal_data` reasons):
  - deletes `r*/`;
  - soft-deletes every derived log (including materialised and duplicated ones) through `delete_log_and_data`, which also cancels jobs;
  - removes their `module_results/{log_id}`;
  - keeps a tombstone (slug, sha256, reason, actor, time).

  For non-personal-data reasons an explicit "retain derived copies" option exists only for irrevocable licenses.
- Any status change publishes nothing unscoped. Per-user `event_log.updated` carries each owner's `user_id`.

**Personal data / declaration** (structured fields, required before `restricted` or `public`):
1. Rights holder (legal entity) and basis of permission: own data, DUA reference, or upstream license URL + version. MATE hosts as distributor only with written permission referenced here.
2. Personal data:
   - `none`, or `anonymised`: needs a re-identification assessment reference (method + who);
   - `pseudonymised` or `contains_personal_data`: never `public`. Allowed only as `restricted`, with controller, legal basis (Art. 6, Art. 89 safeguards), DPIA/ethics reference and `retention_until` all required.
3. Per-attribute disposition (kept / generalised / removed) for resource, case and free-text attributes, prefilled from snapshot cardinality and unique-per-case stats. Column-name hints (`org:resource`, `*user*`, `*name*`, `*email*`) stay advisory.
4. Permitted uses: teaching / thesis / publication / commercial (thesis with an industry partner may be commercial).
5. External AI/MCP egress allowed? Sets `allow_external_ai`.

**Source integrity**
- `POST /hub/datasets` requires `get_owned_event_log(source_log_id, curator.id)`. It refuses a source with a non-empty `active_filter`; `column_overrides` are snapshotted.
- The source log is **never mutated**.
- Only `events.parquet`/`cases.parquet` are published. `original.*` is not copied into `r{n}/` unless `declaration.personal_data == none` and no edit rows exist for the source log in `event_edits` (`models.py:478`). Edits mean the raw original may contain what was anonymised away.
- The curator's source log and its edit audit stay private.

**Egress and licence propagation**
- AI routes and MCP data tools (`mcp/toolsets/processes.py:220-360`, `analysis.py`) refuse derived logs whose `license_snapshot.allow_external_ai` is false.
- Dashboard shares: creating or reading a share on a board bound to a derived log of a gated or restricted dataset requires `can_use_hub_dataset(recipient)`. The loader sets `storage_user_id` = owner for recipients (`loader.py:1741-1747`), so this check is the only gate.
- Attribution and license are shown on the derived log page, dashboards (including shared ones), MCP `list_processes`/`get_module_results` payloads and `GET /event-logs`.

**Tenant isolation**
- Every bus event for a derived log carries the student's `user_id`.
- No unscoped `hub_dataset.*` events (`events_sse.py:111-116` forwards envelopes without `user_id` to everyone).
- `use()` does **not** publish `log.imported`, so no eager-module fan-out; test that no job is queued.
- Only `hub/storage.py` writes under `data/datasets/`.

**Privacy notice.** Acceptances, uses and the admin per-user lists are new processing. A privacy notice covering datasets (roadmap §7.1, none exists today) must be live before gating or `restricted` goes live.

---

## 5. Discovery

**Stage 1 catalog `GET /api/v1/hub/datasets`**
- Params: `q` (LIKE over lower(title, summary, namespace/name), with `%`, `_`, `\` escaped and tokens ANDed), `license`, `sort ∈ {uses, updated, title}`, `limit ≤ 50`, `cursor`.
- The visibility predicate is applied server-side. `include_private=1` is honoured for curators only.

**Detail `GET /hub/datasets/{ns}/{name}`**
- Card, license object, stats, preview, uses, contact.
- `viewer_state: {accepted, my_logs}`, where `my_logs` is filtered by the caller's `user_id`.

**Stage 3:** tags, facets and facet counts, `search_text`, sort by likes, revisions list, Croissant/schema.org JSON-LD (via the SPDX ids).

**Web (Stage 1)**
- `/datasets` page (`apps/web/app/(platform)/datasets/page.tsx` + client): search box, license filter, cards (title, `ns/name`, summary, license badge, events/cases/time span, uses). No facet sidebar until Stage 3.
- `/datasets/[namespace]/[name]`:
  - card rendered with `apps/web/components/modules/guide-markdown.tsx` (react-markdown, no `rehype-raw`, safe);
  - license badge + attribution;
  - cite button preferring `source_doi` ("cite the source DOI; the MATE slug is not a persistent identifier");
  - stats, top activities/variants, first 50 rows;
  - declaration summary, contact;
  - "Copy link".
- **"Use this dataset"** is idempotent per (user, revision): it opens the existing derived log, and "Add another copy" is a separate secondary action. Gated datasets show the accept dialog first. If a course module is missing from the user's installs, the page shows "Install required modules".
- Admin: "Edit card", "Unpublish", "Disable".
- `/processes`: "Dataset" badge with license; detail banner ("From `pm-course/case-study-2026` r1 · CC BY 4.0"); reimport/remap hidden (no original).
- Entry points:
  - sidebar item "Datasets" (`apps/web/components/sidebar.tsx`; there is no top nav);
  - empty state of `/processes` for users with zero logs;
  - import flow `file-step.tsx:99-105`.

**Card template.** Stage 1 requires Description, Source & provenance, License/attribution and Personal data (from the declaration). Structure, Intended use, Limitations, Citation and Contact are optional sections. The `hub/card.py` validator comes with Stage 3.

---

## 6. API, MCP and web changes per file

**API, new**
- `hub/__init__.py`, `licenses.py`, `storage.py`:
  - `revision_dir(ds_id, n)` with derived and asserted paths, the only writer under `data/datasets/`;
  - `snapshot_revision`;
  - `copy_into_user(rev, user_id, log_id)`;
  - `purge_revision`.
- `hub/service.py`: publish, use, accept, unpublish, disable, purge, stats/preview via DuckDB.
- `routes/hub_datasets.py` `prefix="/hub/datasets"`:
  - `GET ""`, `GET /{ns}/{name}`, `POST /{ns}/{name}/accept`, `POST /{ns}/{name}/use` (→ `EventLogSummary`);
  - curator: `POST ""`, `PATCH /{ns}/{name}`, `POST /{ns}/{name}/disable`, `DELETE /{ns}/{name}` (purge);
  - `GET /api/v1/hub/licenses`.
- `schemas/hub_datasets.py`. Register in `main.py`.

**`use()` contract**
1. Checks: `can_use_hub_dataset`, the current terms hash, `HUB_DATASETS_ENABLED`.
2. Insert the row with `status="ready"`, the snapshot metadata (`detected_schema`, `column_roles`, `column_overrides`, `mapping_needs_review`, counts, dates, `log_model`) and lineage. Availability gating reads these fields (`processing.py:312-318`).
3. Run a job (with progress) that:
   - `copy2`s the Parquet into `log_paths(new_id, uid)`, then the whitelisted results (no `_v_*`, no `__*` keys) into `module_results/{new_id}/{module_id}/`;
   - keeps result mtime ≥ `events.parquet` mtime (the `_cache_is_fresh` invariant; asserted in a test);
   - persists via `storage_sync` in S3 mode.
4. Does not publish `log.imported`.

**API, modified**
- `db/models.py`: new models, `EventLog.dataset_revision_id` and `license_snapshot`; fix the stale docstring path (`models.py:83`).
- `modules/processing.py` `_ensure_one`: when no run row exists and the log has lineage, `module_id` is in `canonical_results` with matching `module_version` and resolved `config_hash`, the row's `data_revision` equals the use-time revision, and `active_filter` is empty, then insert `status="ready"`, `job_id=None`, `finished_at=now` instead of submitting. The vocabulary is `queued|running|ready|failed|cancelled` (`models.py:386`). Only opened modules join the working set.
- `ensure_log_usable` call sites (§4), with `_make_context` fixed so the gate is not swallowed.
- `services/log_aggregates.py`: `duplicate_log` propagates lineage and copies `column_roles` and `mapping_needs_review`. Reimport/remap return 409 `no_original` when `original.*` is absent.
- Dashboard share create/read: lineage check (§4).
- `routes/ai.py`, `routes/ai_guidance.py`, MCP `processes.py`/`analysis.py`: `allow_external_ai` check; `source_dataset` in `list_processes`.
- `schemas/event_logs.py`: `source_dataset: {slug, title, revision, license_id, attribution} | null`, joined in `GET /event-logs`.
- `config.py`: `HUB_DATASETS_ENABLED` (default true).
- Prerequisite fix (independent PR, pre-existing bug): `concept_drift_explainer/module.py:202` writes `docs_dir / file.filename` unsanitised, which is a path-traversal write. Use `safe_segment` / basename.

**MCP**
- Stage 3: `hub.py` with `search_hub_datasets`, `get_hub_dataset`, and `use_hub_dataset` (behind `ensure_writable`, `mcp/core.py:109-120`; refused when gated and not accepted).
- Stage 1: only the egress check and `source_dataset`.
- Update `docs/MCP.md`.

**Web**
- New: `datasets/page.tsx`, `datasets-client.tsx`, `datasets/[namespace]/[name]/page.tsx`, `components/datasets/{dataset-card,license-badge,accept-terms-dialog,use-dataset-button,dataset-preview}.tsx`, `lib/hub-queries.ts` (`use` = create mutation with `onSuccess` invalidate; accept optimistic).
- Admin: `admin/datasets/page.tsx` (publish-from-own-log wizard, card + declaration).
- Modified: `sidebar.tsx`, `processes-client.tsx`, process detail banner, `file-step.tsx`, dashboard attribution footer, `lib/api-types.ts` via `make codegen`.

**Docs:** `docs/DATASETS.md` (user + curator + takedown runbook, including module-upgrade re-warm), `docs/strategy/hub-roadmap.md` decisions log, `docs/README.md` index.

**Tests** (each guards a believable bug):
- **Visibility**:
  - private/disabled → 404;
  - restricted → 404 for non-members;
  - reviewer sees only open + ungated;
  - admin+reviewer token cannot publish.
- **Search**: LIKE escaping on SQLite.
- **Publish**:
  - `unknown` blocks;
  - pseudonymised blocks `public`;
  - publishing another user's log → 404;
  - the source log is unchanged after publish (sha of its Parquet).
- **`use`**:
  - idempotent;
  - copies only Parquet + whitelisted results (no `original.*`, `_v_*`, `__*`);
  - queues no job;
  - `list_states` shows `idle` until opened, then `ready` with **zero** module computes for `discovery`, `performance`, `complexity` (parametrised over the canonical modules);
  - a filter commit re-runs only opened modules.
- **Sidecar**: a conformance model uploaded on user A's derived log is invisible to user B; the sha of `data/datasets/` tree is unchanged after all actions.
- **Takedown**:
  - disable → 410 on module route, Events tab, MCP `get_module_results`, AI guidance and the dashboard share;
  - purge removes derived logs, including duplicates.
- **Gating bypass**: dashboard share of a gated derived log → refused for a non-accepted recipient.
- **Egress**: AI/MCP refused when `allow_external_ai` is false.
- **Data integrity**:
  - `duplicate_log` keeps `column_roles`;
  - curator deletion keeps the dataset;
  - migration upgrade/downgrade on SQLite.
- **Planner**: tests read only `apps/api`; confirm with `make ci-plan` that the e2e selects both web and api.

---

## 7. Storage and scale

- **Layout:** `data/datasets/{id}/r{n}/data/{events,cases}.parquet` (or `ocel/*.parquet`), `r{n}/module_results/{module_id}/…` as a sibling (never inside `data/`), and `r{n}/original.xes.gz` only per §4 (explicitly gzipped at snapshot; the pipeline keeps the upload as-is). It lives outside `data/users/`, so `user_deletion.py:122-127` and eviction (`eviction.py:178-192`, `users/*` only) never touch it. Revisions are not evictable; document that `local_cache_bytes` excludes them.
- **Snapshot flow:**
  1. The curator imports into their own account and checks column roles.
  2. The curator warms the course modules with default config and an empty filter.
  3. `POST /hub/datasets` copies Parquet and whitelisted results with `copy2`, computes stats/preview/attribute cardinality, and records `canonical_results` (`module_version`, `config_hash`).
  4. It never rewrites `events.parquet` after the results exist; the mtime invariant is asserted.

  It runs as one job with progress. The source log is left untouched.
- **Module upgrades mid-semester:** a version bump makes the seed gate miss, and every student recomputes on first open. The runbook re-warms the canonical results into a new revision's results (or an admin "re-warm" action) **before** deploying the bump.
- **Memory (prod facts):** `mem_limit: 8g`, one uvicorn process, `DUCKDB_MEMORY_LIMIT: "2GB"`, `DUCKDB_THREADS: "2"` already set (`docker-compose.prod.yml:137,184-185`).
  - The DuckDB limit is **per connection**, and `EventLogAccess` opens one per access (`event_log_access.py:351`).
  - The Arrow read cache skips tables above 512 MiB (`_READ_CACHE_MAX_BYTES`, `:57,78`).
  - `pandas()` does a full `to_pandas()` per call (`:253`).
  - With copy-on-use each student has their own path, so there is no cross-student Arrow cache hit; reference logs would share one only if `nbytes` ≤ 512 MiB.
  - Mitigations:
    - lower `DUCKDB_MEMORY_LIMIT` to ~(8 GB − baseline) / expected concurrent queries (likely 512 MB-1 GB);
    - add a process-wide semaphore around full-frame `pandas()`/`pm4py()`;
    - load test 10, 30 and 60 sessions with **module pages open**, plus a 10-user simultaneous filter commit, watching RSS.
- **Import memory:** list of dicts (`xes.py:153,178`) → `pd.DataFrame(rows)` (`dispatch.py:208`) in the API process. Stage 0 measures peak RSS. If it exceeds ~5 GB, Stage 1 **must** include the offline route: `uv run python -m mate.api.hub.cli import-revision <dir>`, which imports in lite on a workstation and copies the revision dir. The streaming `ParquetWriter` + subprocess parser is a separate, independent PR.
- **Disk:** copy-on-use = 1 × revision + N × (Parquet + results). Stage 0 computes N=60 against free disk. If over budget, switch to reference logs (Stage 4b).

---

## 8. The first dataset: the course case-study log

**Answers from the chair (2026-11-19)** for the Process Thinking course logs
(`CreditLoanApplication_2020.xes`, `CreditLoanApplication_2025.xes`, with the course data
dictionary DD-EVL-2026-01):
- Rights holder / source: Eindhoven University of Technology via 4TU.Centre for Research Data,
  https://data.4tu.nl/articles/_/12721292/1. The hosting page must show the publisher details.
- Personal data: real company data, anonymised by blinding all data about individuals.
- Audience: the Process Thinking course only → `restricted` to a course team, not `public`.
- License: 4TU General Terms of Use (`4tu-general-terms`); all academic uses permitted;
  publishing results requires the citation of the dataset.
- Gating: none (no terms to accept).
- Retention: keep after the semester; it may be reused next winter semester.
- Still open: whether students may send data or results to external AI providers
  (`allow_external_ai`), and whether the 2020 log has its own 4TU source (its activity names
  match BPI Challenge 2012, the 2025 log's match BPI Challenge 2017), which would need its own
  citation.

**Questions for the data owner (the chair answers before anything beyond `private`):**
1. Rights holder and basis: chair-owned, partner under NDA/DUA (reference?), or a public source (DOI + version)? Is MATE permitted to host and redistribute it?
2. Audience: the course only (→ `restricted` to a course team), or every platform account, **including anonymous external conference reviewers**?
3. Personal data: real company or employee data? Resource, customer or case IDs? Anonymised or pseudonymised, and by which method? Re-identifiable by participants? If pseudonymised: controller, legal basis, DPIA/ethics reference.
4. License: for upstream logs, the exact terms per DOI. BPIC 2017 is listed under 4TU General Terms of Use; confirm BPIC 2012/2019 and any other source per DOI, including whether re-hosting is allowed.
5. Allowed uses: course, thesis (incl. with industry partners), publication of results, commercial; may students export or republish derived results?
6. External AI/MCP: may students send data or results to external LLM providers with their own keys?
7. Gating: are accepted terms needed, and an audit of who accepted?
8. Retention: an end date, and what happens to students' copies then?

**License, visibility and gating by case**

| Case | License | Visibility | Gating | `allow_external_ai` |
|---|---|---|---|---|
| Chair owns, truly anonymised, reusable | `cc-by-4.0` | `public` | none | yes |
| Chair owns, no commercial reuse | `cc-by-nc-4.0` | `public` | none | per Q6 |
| Partner data, course-only | `other` ("Course-only terms WS 2026/27" + text), attribution names the partner | **`restricted`** to the course team | `accept_terms` | no |
| Upstream public log | upstream verbatim (`cc-by-4.0` / `4tu-general-terms`) | `public` if re-hosting allowed, else link-only (Stage 6) | per upstream | per terms |
| Pseudonymised or personal data | n/a | `restricted` only, with legal basis + DPIA + `retention_until`; otherwise do not host | `accept_terms` | no |

Do not choose a CC license on someone else's behalf. The conservative default is `other` + `restricted` + `accept_terms` + no external AI.

**Steps**
1. Get answers 1-8 and record them in the declaration.
2. Stage 0 in lite: peak RSS, import time, Parquet size, Arrow `nbytes`, warm time for the course modules, and N=60 disk.
3. Deploy Stage 1a+1b. Import on prod, or offline + `hub.cli` if peak > 5 GB. Warm the course modules (candidates: `discovery`, `performance`, `complexity`, `conformance`, `performance_over_time`, `process_comparison`; final list per course plan).
4. Publish as `private`. QA with 2-3 accounts, one of them `reviewer`, which must see nothing unless open + ungated. Then create the course team and switch to `restricted` or `public`.
5. Announce the `/datasets/pm-course/case-study-2026` link. Retire any `SAMPLE_CONTENT_MANIFEST` plan for this log.
6. Module availability:
   - Bundled modules are always effective defaults (`modules/defaults.py:1-12`; `default_ids` only adds admin-declared ones).
   - The 3 users without installs are seeded lazily on reconcile.
   - Lower counts (`performance_java` 48, `temporal_mapper` 41) are uninstalls or late additions.
   - Check that no course module is in `modules.default_excluded_ids` (today only `actor_performance`), and rely on the "Install required modules" prompt.

**Timeline (course start T)**
- T-4w: questions sent; Stage 0 measurements.
- T-3w: Stage 1a+1b merged, dark (dataset `private`).
- T-2w: import, warm, QA, 10/30/60-session load test.
- T-1w: privacy notice covering datasets live; switch visibility.
- **Fallbacks if Stage 1 slips:**
  - `SAMPLE_CONTENT_MANIFEST` seeding (`services/sample_content.py`): one full import per listed account, heavy but existing;
  - a team shared dashboard: read-only, no Events/Variants tabs and no raw rows (`sharing.py:180`), so not sufficient for analysis on its own.

---

## 9. Staged rollout (one PR each, independently deployable)

**Stage 0: measure + decide (no code)**
- Numbers per §8 step 2, owner answers, and the §10 decisions, recorded in `hub-roadmap.md` §7.2.
- **Measured on the VM (2026-11-19)**, import pipeline (parse → roles → timestamps → Parquet)
  in a throwaway api container, 8 GB limit, VM 15 GB RAM:

  | Log | XES | Events | Cases | Activities | Peak RSS | Time | Parquet |
  |---|---|---|---|---|---|---|---|
  | `CreditLoanApplication_2020` | 74 MB | 262,200 | 13,087 | 24 | 0.36 GB | 8 s | 3 MB |
  | `CreditLoanApplication_2025` | 579 MB | 1,202,267 | 31,509 | 5 | 2.04 GB | 52 s | 18 MB |

  Consequences: 60 copy-on-use copies ≈ 1 GB of Parquet, so reference logs (Stage 4b) are not
  needed; import on prod is fine (no offline `hub.cli`); about four parallel self-uploads would
  exhaust the api's 8 GB, which is the main reason to publish the log once. Still to measure:
  module warm time on the 1.2 M-event log.
- **The 5 activities were a MATE bug** (fixed 2026-11-19): the column resolver took the
  `Action` attribute (Created, statechange, ...) over `concept:name` because it came first in
  each event. The data dictionary lists ~23 activities for the 2025 log. Re-measure after the
  fix; logs of this shape imported earlier need a re-map (Settings → Column roles).
- Separate prerequisite PR: the CDE filename sanitisation.

**Stage 1a: data model + copy-on-use + takedown (API)**
- Migration 0018 (Stage 1 tables only, `down_revision = "0017_rename_module_outputs"`) + migration test.
- `hub/` package; curator publish/patch/disable/purge; public `GET`/`use` (+ `accept` if gating is required).
- `ensure_log_usable`, lazy seeded run rows, lineage on duplicate (+ `column_roles` fix).
- Share/egress checks; `source_dataset` on log schemas.
- `hub.cli import-revision` if Stage 0 peak > 5 GB.
- Verify:
  - `make check-changed` and the §6 tests;
  - in lite: curator publishes, a second demo user `use`s, opens the module pages, and sees zero computes;
  - a filter commit recomputes only the opened modules;
  - a conformance upload stays per user;
  - disable → 410 everywhere;
  - the `data/datasets/` tree hash is unchanged.
- Rollback:
  - `HUB_DATASETS_ENABLED=false` hides routes; derived logs are ordinary logs and keep working;
  - downgrade only after purge, and it refuses while `dataset_revision_id` rows exist.
- Risk: medium (takedown gate coverage). Mitigate with a test parametrised over the router's log-scoped routes.

**Stage 1b: web**
- List + search + license filter, detail, accept dialog, idempotent use, badge/banner, sidebar entry, empty states, attribution on dashboards, admin publish wizard.
- Verify: `make lite` + `make e2e` (search → use → open process → module page shows results with no run), and report what was seen.
- Rollback: revert. Risk: low.

**→ Course log goes live after 1a + 1b + privacy notice.**

**Stage 2: hardening from the course**
- Memory semaphore and DuckDB limit tuning from the load test; re-warm action for module upgrades; retention automation (`retention_until` → disable → purge).

**Stage 3: discovery depth + lifecycle**
- Tags/facets/`search_text`, likes, MCP hub tools, JSON-LD, structured card validator, new revisions ("Update to r{n+1}" = new derived log), report queue, `admin/datasets` moderation, audit entries in the insights stream (with the actor's `user_id`).

**Stage 4b (conditional, only if Stage 0/2 disk numbers require it): reference logs**
- Prerequisites, each its own PR with tests:
  - per-(user, log, module) sidecar dir + migration of `conformance`/`concept_drift_explainer`;
  - path-keyed single-flight S3 hydrate with a completion marker;
  - a seed overlay honoured by module freshness checks, `ResultCache.exists`, MCP and `ctx_rpc`;
  - `:ro` nested bind mount of `data/datasets`.
- Then `resolve_log_storage` over the ~45 read sites (incl. `ai_guidance.py`, `analysis.py:453`, `processing.py:437`).

**Stage 5: user-owned datasets**
- `curator` role, user cards on own logs, "request publish" with admin approval, team namespaces, per-user quotas. Gated on Stage 3 moderation.

**Stage 6: linked datasets**
- Metadata + DOI + source URL only, with "Import from source" behind `net_guard.py` (SSRF guard), for 4TU/ocel-standard logs: "link, don't re-publish".

---

## 10. Open decisions for the owner

1. **Naming**: *Done.* Module data outputs were renamed (`datasets:` → `outputs:`, see §2) before Stage 1a, so "dataset" means only published event logs and the hub concept is simply "dataset" in code and UI.
2. **Use model**: copy-on-use (Stage 1) vs reference logs. *Rec: copy-on-use; reference logs only if Stage 0 shows 60 × (Parquet + results) over the disk budget.*
3. **`use` idempotent per (user, revision)** with an explicit "Add another copy". *Rec: yes.*
4. **Publishers in Phase 1**: `may_curate_datasets` (admin and not sandboxed). *Rec: yes; `curator` in Stage 5.*
5. **Hosting policy vs "hub, not publisher"** (`hub-roadmap.md:4-7`): host only with documented permission from the rights holder (declaration item 1); otherwise link-only. *Rec: adopt; record in the decisions log.*
6. **Course log visibility, license, gating, egress**: from the chair's answers. *Rec if any doubt: `other` + `restricted` (course team) + `accept_terms` + `allow_external_ai=false`.*
7. **Reviewer accounts**: see only `public` + open license + ungated datasets. *Rec: yes.*
8. **Unpublish semantics**: copies keep working where the license is irrevocable; otherwise follow the terms. *Rec: yes; disable/purge cut off everything.*
9. **Seeded results with a non-default config**: recompute (`config_hash` mismatch). *Rec: yes.*
10. **Acceptance records**: CASCADE on user deletion (lose the record) vs keep a pseudonymous hash. *Rec: CASCADE; disclose. Only relevant if gating is chosen.*
11. **Import route**: prod vs offline `hub.cli`. *Rec: decide from Stage 0 RSS; offline if > 5 GB.*
12. **Retention**: open licenses → `deprecated` at semester end; DUA or personal data → `retention_until` → disable → purge, including derived logs. *Rec: yes.*

---

## 11. Out of scope / YAGNI

- Git history, branches, LFS, slug-rename redirects.
- DOI minting, DataCite, DMCA counter-notice, DSA appeals, geo-blocking.
- Billing and per-plan quotas; per-user quotas before Stage 5.
- Manual-approval gating, custom gating fields, notification cadence.
- Anonymous (logged-out) access; federation.
- `tsvector`/embeddings before ~1k datasets.
- Shared filtered-variant caches; cross-user result sharing beyond the canonical default view.
- Non-admin dataset upload, YAML card import/export, Croissant export before Stage 3.
- Rate limiting `use` (idempotency is enough at ~60 users).
- Enforcing share-alike on exports (Stage 3 shows the inherited license only).
- Renaming the module-output "datasets" concept.

---

Verified code anchors:
- `sharing.py:1-14,180`; `auth/ownership.py:24-37`
- `auth/dependencies.py:222-231` (`require_admin` checks only `ADMIN_ROLE`), `:251` (`may_watch_custom_path` = admin and not sandboxed)
- `config.py:85-89,122-130` (reviewer = anonymous conference accounts; sandboxed), `:138` (`worker_concurrency`), `:212`
- `db/models.py:83` (stale docstring), `:88-90`, `:146-150`, `:364-400` (run status vocabulary `:386`), `:478`, `:690-728`
- `modules/loader.py:1694-1752` (`try/except Exception` swallow; share sets `storage_user_id`)
- `modules/processing.py:57` (`_WORKING_SET`), `:300-303`, `:312-318`, `:584-606` (`refresh_working_set`)
- `modules/event_log_access.py:57,78,214,253,351`; `modules/defaults.py:1-12`
- `modules/conformance/module.py:40-48`; `modules/concept_drift_explainer/module.py:48-53,196-214` (unsanitised filename)
- `modules/complexity/module.py:34-47` (`_cache_is_fresh` mtime check)
- `mcp/toolsets/analysis.py:286-300` (`cache.dir.iterdir()`); `routes/ai_guidance.py` (5 `ResultCache` sites)
- `routes/events_sse.py:111-116`; `routes/datasets.py:33`; `apps/api/src/mate/api/datasets/` package (both since renamed to `module_outputs`, §2)
- `services/log_aggregates.py:1194-1222` (`duplicate_log` copies `detected_schema` but not `column_roles`), `:1232-1255` (soft delete + own rmtree)
- `ingest/dispatch.py:208,285,289,340,519`; `ingest/xes.py:153,178`; `ingest/storage.py:79-92`; `path_segments.py:18-22`
- `storage/sync.py:89`; `storage/eviction.py:178-192`; `services/user_deletion.py:122-127`
- `tests/conftest.py:65` (`create_all`); `tests/test_card_migration.py`
- `docker-compose.prod.yml:137,184-185`; `apps/api/Dockerfile` (no `USER`)
- `apps/web/components/modules/guide-markdown.tsx`; `apps/web/components/sidebar.tsx`
- latest migration `0017_rename_module_outputs.py`

Unverified: prod `STORAGE_MODE` (`.env.example` says `local`); Parquet size and Arrow `nbytes` of the course log; XES extension exposure in `ingest/xes.py`; license terms of BPIC 2012/2019 per DOI; `ctx_rpc.py:126-128` and `PROTOCOL.md:204-210` (taken from reviews; only relevant to Stage 4b).

---

## Appendix: Review points rejected

- **Storage #8 (`data_revision == 0` gate is brittle; base the seed on "no active filter" only): rejected for Stage 1.** Copy-on-use logs are editable, so a revision check is needed for correctness. The gate is now "`data_revision` equals the use-time revision + no filter + config hash match". The point's second half, comparing the resolved `config_hash` instead of requiring no `ModuleConfig` row, was accepted.
- **Storage #12 (convert the curator's source log, cancel jobs first): moot.** The source log is no longer mutated (security H5, accepted).
- **Security C1, read-only bind mount and snapshot writes outside the API: deferred, not adopted for Stage 1.** Under copy-on-use no student path resolves to `data/datasets/`. The `:ro` mount is a Stage 4b prerequisite. The sidecar finding itself was confirmed and drove the switch to copy-on-use.
- **Security M3 (rate limit on materialise/use): rejected as YAGNI at ~60 users.** Idempotent `use` plus the Stage 0 disk check cover it, and there is no separate materialise step in Stage 1.
- **Security M5 / storage #1 / storage #7 (seed overlay and path-keyed S3 hydrate fixes): not implemented in Stage 1.** They apply to reference logs, which moved to the conditional Stage 4b and are listed there as prerequisites. Copy-on-use avoids them.
- **Legal #10, share-alike driving behaviour on exports: deferred to Stage 3.** Stage 1 shows the inherited license on derived logs and dashboards. The SPDX mapping and the `4tu-general-terms` id were accepted.
- **UX #3, deferring gating "unless the chair requires it": accepted only partially.** `restricted` visibility and the declaration stay in Stage 1, because the legal review showed course-only terms cannot be met without them.
- **Plan claim "DuckDB limits default to 0/empty, set them before the course" (legal reviewer listed it as verified):** the config defaults are empty, but prod already sets `2GB`/`2` (`docker-compose.prod.yml:184-185`). The plan now follows the security/storage framing: the limit is per connection and needs re-sizing.
