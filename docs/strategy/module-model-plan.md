# Module model after upload removal: plan

Status: approved by the owner on 2026-11-18 with the decisions in §5; not started. Built from a code map (backend, upload machinery,
frontend, data/ops), three candidate designs and a skeptical review against the repo.
Line numbers refer to `main` at `961fb1c`.

## 1. Problem

Hosted instances run **only repo modules** (`modules/`, merged by reviewed PR). Upload is off, and
earlier uploads are not loaded. The per-user **install model** was built for uploaded code:
- `module_installs` table (`db/models.py:322-341`), `modules/installs.py`;
- reference-counted teardown (`modules/uninstall.py`), co-ownership of identical uploads
  (`install_jobs.py:208-246`);
- default seeding/reconcile on every `GET /modules` (`routes/modules.py:99-144,213`), "restore
  defaults", admin "declare default", "withhold", "force install/uninstall", "Uploaded by";
- S3 module archive/restore/GC (`storage/module_archive.py`, `main.py:340-355`).

With repo-only modules none of this does anything useful: every module is loaded for everyone, and
"uninstall" only hides it. The table now has two **accidental** jobs:
1. **A per-user visibility list.**
2. **A tenant gate**, read in about 15 places: bus delivery (`loader.py:1410,1471,1625-1638`),
   registry (`loader.py:1731-1733`), processing, datasets (`datasets.py:74-99,120`), MCP
   (`mcp/toolsets/analysis.py:50-63,164-174`), AI nav, admin pages.

That gate is what breaks shared dashboards (viewer doesn't "own" the module), the first visit
(rows exist only after the reconcile runs) and `PUT /config` for users with no row.

**Activate/deactivate already exists:**
- `module_configs.enabled` per user (`models.py:306-319`);
- fallback to manifest `default_enabled` (`sdk/manifest.py:489`);
- UI switches in `modules-client.tsx:106-158` and `module-detail-client.tsx:300-307`;
- MCP `set_module_config(enabled=…)`.

## 2. Target model

| Layer | Who | Storage | Meaning |
|---|---|---|---|
| **Catalog** | instance | code: modules loaded from `modules/` | What the server runs. Changes only by PR + deploy (hosted) or a folder + hot reload (lite). |
| **Default** | repo | manifest `default_enabled` | Whether a module starts active for a user. "Opt-in module" = `default_enabled: false`, changed by PR. |
| **Active** | each user | existing `module_configs.enabled` | The user's own on/off switch. No row → manifest default. |

No per-user install, no ownership, no upload.

**One rule for every call site** (new `modules/access.py` with `available_ids()` and `is_active()`):
- **Data paths use "loaded"**, so nothing breaks:
  - module routes and assets
  - datasets
  - registry RPC
  - dependencies (`precompute_deps`)
  - user-triggered runs (`ensure`)
  - cards already on any dashboard, including shared ones
  - plain `@on_event` handlers (cache refresh)
- **Discovery and automatic work use "active":**
  - `/modules` list flag, process grid, card palette default view
  - AI nav, AI guidance
  - MCP `list_modules` flag
  - automatic precompute on import (`precompute_dispatcher`, `processing.py:580,833`)
- **Tenant isolation does not come from ownership.** It comes from events requiring `user_id`
  (`loader.py:1401-1406`) and the context being bound to the event's user (`:1413-1417`). That
  stays.

**What people see**

- **Student/researcher:** `/modules` lists all modules with an Active switch. There is no Install,
  Uninstall, Restore defaults or Import.
  - Deactivating a module hides it from the grid, AI nav and palette, and stops automatic runs on
    imports.
  - It does **not** break dashboard cards or pages: opening the module page still works, and
    that's where you switch it back on.
- **Admin:** Admin → Modules shows the loaded modules with "Precompute on import" (kept) and how
  many users have them active. Default/withhold/force/owners/uploaded-by disappear.
- **Author:** PR into `modules/` for hosted; locally `make lite` + `make new ID=x` or drop a folder
  into `modules/` (hot reload + Studio). No archive upload anywhere.
- **MCP:** `list_modules` returns all loaded modules with `active`; on/off via
  `set_module_config(enabled=…)`. `uninstall_module` / `restore_default_modules` are removed.

**Deliberately not built (YAGNI):**
- an admin "availability" layer (on / opt-in / off);
- a hard admin "Off" that returns 403.

Opt-in is a manifest flag; taking a module off the platform is a PR (or `MODULES_ONLY`). Revisit
only when a concrete need appears, see step 0.

## 3. Stages (one PR each, deployable on its own)

### Step 0: Production facts (no code, before stage 3)

Run on the server and record the answers:

```sql
SELECT key, value_json FROM system_settings
 WHERE key IN ('modules.default_excluded_ids','modules.default_ids');
SELECT count(DISTINCT user_id) FROM module_installs;
SELECT count(*) FROM users;
SELECT module_id, count(*) FROM module_installs GROUP BY 1 ORDER BY 1;
SELECT count(*) FROM user_settings WHERE key='modules_defaults_seeded';
```

- Withheld or declared defaults non-empty → decide per id (manifest `default_enabled: false` PR,
  or keep).
- Users with **no install rows** (e.g. MCP-only users) get every default module active after
  stage 3, so eager precompute starts running for them. Note that in the release.

### Stage 1: Defuse the boot GC and back up (no behaviour change)

- **Why first:** in S3 mode the boot GC deletes `data/uploaded_modules/<id>` folders that have no
  install row (`main.py:346-351`, `modules/maintenance.py`). Any later change to install rows
  could wipe sources.
- **Change:**
  - delete the GC/restore block (`main.py:340-355`) and `modules/maintenance.py`, plus its tests
    (`test_storage_module_archive.py:125-137`);
  - `docs/DEPLOY.md`: add the `pg_dump` + `data/uploaded_modules/` backup step.
- **Verify:** take a backup, deploy, confirm `ls data/uploaded_modules` is unchanged.
- **Rollback:** previous image. **Risk:** very low.

### Stage 2: Remove the upload machinery (hosted and lite)

Done before the ownership switch so stage 3 doesn't have to keep dead install-row writers alive.

- **API:**
  - delete `POST /modules/install`, `GET /modules/install/status` and the `data/module-uploads`
    staging (`routes/modules.py:520-588`);
  - delete `modules/install_jobs.py` and its registration (`main.py:47,339`);
  - delete `storage/module_archive.py` (move `BUILD_ARTIFACT_NAMES` into `modules/content_hash.py`
    first; keep `module_content_hash` and `id_lock`).
- **Loader:**
  - discover from `modules_dir` only (`loader.py:551-561,611-636,776-778`);
  - remove `uploaded_modules_dir` and `module_upload_enabled` from `config.py`, `.env.example`,
    `docker-compose.yml` and `scripts/lite.sh`. A stale env var still boots because of
    `extra="ignore"` (`config.py:24`);
  - take `id_lock` in `hot_reload._reload`.
- **Entry-point discovery** (`discovery.py:110-191`, `mate.modules`): remove (decision D3).
- **Web:**
  - delete `/modules/import`;
  - move the "Download README" card to `/modules`;
  - remove the 5 import links (`module-grid.tsx:184,221`, `process-detail-client.tsx:224-229`,
    `processes/[logId]/modules/[moduleId]/page.tsx:60`, `app-splash.tsx:64`, `page-meta.ts:71`)
    and the AI-nav destination (`ai_nav.py:160-170`);
  - hand-edit `api-types.ts`.
- **Docs:** `modules/README.md` §11, `docs/DEV_LITE.md`, `docs/S3_OFFLOAD.md`, `docs/INSTRUCTIONS.md`,
  `README.md`, landing docs, `AGENT.md`, `hub-roadmap.md`; run `make docs`.
- **Tests:**
  - delete the upload tests (`test_module_upload_toggle.py`, `test_storage_module_archive.py`,
    `test_modules_loader.py:325-386`, `test_modules_per_user.py:276-410`,
    `test_tracking_exemption.py:269-276`, `test_module_guides.py:595-640`), stating the reason in
    the commit;
  - reduce the discovery tests to a single root;
  - add "a folder under `data/uploaded_modules` is never discovered";
  - extend `test_lite_dev.py`: a new folder hot-loads, and dev and watchdog reloads serialise.
- **Verify:**
  - `make lite`, then `make new ID=demo_x`: it appears and the Studio reloads it;
  - `/modules/import` returns 404 with no dead links;
  - `make e2e` (the studio flow copies a fixture into `modules/`).
- **Rollback:** revert. Upload folders were never deleted. **Risk:** low to medium (the lite
  authoring path changes).

### Stage 3: Switch from "owned" to "loaded/active" (backend + web + data migration, one PR)

Atomic: if the reconcile disappears but the ownership gate on `PUT /config` (`routes/modules.py:459`)
stays, new users can't toggle anything.

- **`modules/access.py`** (pyright 0 errors): `available_ids(loader)` and a batched
  `is_active(user, module)` / `active_map(user)` (row → `enabled`, else manifest
  `default_enabled`). It replaces the six inline copies:
  - `routes/modules.py:256,432-438`
  - `processing.py:247-252`
  - `ai_nav.py:329`
  - `analysis.py:206`
  - `routes/ai_guidance.py:150-160`, fixing the wrong `True` fallback at `:159`.
- **Migration `0017_module_activation`** (data only, no schema change, Postgres + SQLite):
  - for each user whose `modules_defaults_seeded` is a list, and each id in it that is **in
    `modules/` today** and has no install row (= the user removed it): set
    `module_configs.enabled=false` (update `enabled` only, keep `config_json`);
  - never read "no install row" alone as "deactivated", and skip a bare `true` / missing /
    unparsable value;
  - the downgrade is a documented no-op (old code honours `enabled=false` too; precedent
    `0011_drop_flows.py`).
- **API:**
  - `_assert_owns_module` → `_assert_available` (404 "not on this instance") at
    `routes/modules.py:372,398,412,430,459,624,642`;
  - `GET /modules` lists all loaded modules + `enabled`, with no reconcile; `/modules/cards` the
    same;
  - delete restore-defaults (`:729-759`) and `DELETE /modules/{id}` (`:762-772`).
- **Loader:**
  - the bus gate `_user_owns` (`:1625-1638`) checks loaded instead (plain handlers), failing
    closed;
  - job-backed handlers already go through the dispatcher's `enabled` check, so no extra gate;
  - registry scoping uses loaded.
- **Processing** (`processing.py:44,302,514,599,711,738,806`): owned → loaded. Auto-run at `:580,833`
  stays on active, and `ensure()` ignores activation.
- **Datasets** (`datasets.py:28,59-99,120,151`): the catalog is all loaded modules + `active`, and
  fetch requires only loaded. This fixes shared-dashboard viewers.
- **MCP:**
  - `_installed_module_ids` / `_ensure_installed` and **`list_modules` (`analysis.py:164-174,192`)**
    use loaded + `active`;
  - delete `uninstall_module` / `restore_default_modules`;
  - keep the `modules:manage` scope id as "deprecated, grants nothing" (`mcp/scopes.py:60`, stored
    tokens).
- **Smaller call sites:**
  - `ai_nav.py:320-327`: drop the owned check;
  - `sample_content.py`: drop the reconcile, and warm `wanted & loaded`;
  - `admin_policies.py:270-277`: manifests only;
  - `admin_insights.py:906-915`: `installs_by_module` becomes `active_users_by_module` (computed in
    Python, portable).
- **Admin:**
  - delete default/withhold/force/owners (`admin_modules.py:106-161,228-297,329-367`) and the
    `AdminModuleRow` fields; keep precompute;
  - `admin_users.py:254-265`: drop the module section; `user_deletion.py`: drop the teardown;
  - `middleware/usage.py:42`: drop `module_uninstalled`.
- **Delete** `modules/installs.py`, `modules/uninstall.py`, `modules/defaults.py` (move the
  precompute overrides to `modules/admin_settings.py`). **Keep the `ModuleInstall` model** until
  stage 4.
- **Web:**
  - remove Install/Restore/Uninstall (`modules-client.tsx:68-104`,
    `module-detail-client.tsx:233-240,495-536`, `lib/queries.ts:624-658`);
  - module page: an "Activate to use" banner when inactive, "not available" only when not loaded;
  - empty grid → "Show disabled modules";
  - palette: hide inactive modules behind the same toggle; placed cards keep rendering;
  - admin modules page trimmed;
  - copy sweep "installed" → "available/active" (`generic-viz-card.tsx:38`,
    `module-widgets.tsx:27,147`, `settings/about/page.tsx:220`,
    `admin/users/[userId]/page.tsx:177`, `admin/modules/page.tsx:343`, `analysis-step.tsx`,
    `page-meta.ts`);
  - the toggle mutation stays optimistic (invalidate `['modules']` in `onSettled`); no new SSE
    event.
- **Tests** (each guards a contract):
  1. Events without `user_id` never reach a handler; ctx is bound to the event's user; a DB error
     in the gate means no fire.
  2. A new user with no rows sees all loaded modules with `enabled == default_enabled`, and
     `PUT /config` works without a prior visit.
  3. Deactivated: hidden from the list flag and AI nav, still 200 on config/manifest/guide/layout,
     other users unaffected; an unloaded id gives 404.
  4. Producer chain: a deactivated producer is skipped on auto-run, but `ensure(consumer)` still
     runs it.
  5. Shared-dashboard viewer who deactivated X can still fetch X's dataset.
  6. MCP: the two tools are gone; `list_modules` works for a user with no rows; `modules:manage`
     alone does not widen scopes.
  7. Migration on SQLite: list-minus-installs gives exactly one `enabled=false`; `config_json` is
     kept; bare-`true` users are untouched; ids not in `modules/` are ignored; a second run is
     idempotent.
  8. `conftest.py` stops seeding install rows; update the affected tests.
  9. **Grep guard:** `rg '\bModuleInstall\b|user_module_ids|user_owns_module|_user_owns|module_installs' apps/api/src`
     returns 0 hits outside `db/models.py` and alembic.
  10. Pyright counts do not rise (`scripts/ci/pyright-baseline.json`).
- **Manual:**
  - run 0017 on a Postgres restore of the prod dump;
  - in `make lite`: deactivate discovery → it leaves the grid and AI nav; import a log → no
    auto-run; dashboard cards still render; re-activate from `/modules/discovery`;
  - `make e2e`.
- **Verify on prod:** the migration log count matches step 0; spot-check a user who had removed a
  module; no 404 "not installed" in the logs.
- **Rollback:** redeploy N-1. Install rows are intact and `enabled=false` rows are harmless.
- **Risk:** high (the semantic switch). Mitigated by the tests above, the kept table and the step-0
  facts. Removing the two MCP tools is a breaking change, so note it in `docs/MCP.md`.

### Stage 4 (a later release, after stage 3 has been stable): Drop the table

- **`0018_drop_module_installs`:**
  - drop `module_installs` + index;
  - delete the `modules_defaults_seeded`, `modules.default_ids` and
    `modules.default_excluded_ids` settings;
  - the downgrade recreates the empty table (precedent `0013`).
- Remove `ModuleInstall` from `models.py`.
- **Runbook** in `DEPLOY.md`: archive `data/uploaded_modules/` (incl. `case_count`, `num_cases`)
  and S3 `_system/modules/` offline, then delete them.
- **Rollback:** `alembic downgrade 0017` before starting an older image. **Risk:** low for
  behaviour; the data is irreversible without the offline copy.

## 4. Safety rules

1. Stage 1 before any change to install rows.
2. Back up (`pg_dump` + `data/uploaded_modules/`) before stages 3 and 4.
3. Stage 3 ships backend, web and migration together.
4. Data paths use **loaded**; only discovery UI and auto-run use **active**.
5. The bus gate fails closed; every new bus event carries `user_id`.
6. A migration never treats "no install row" as "deactivated".
7. Keep the `modules:manage` scope id until stored tokens and the realm are migrated.
8. Edit `apps/web/lib/api-types.ts` by hand (no `make codegen`).
9. Rehearse migrations on a Postgres restore; CI runs SQLite only.
10. `make check-changed` per PR, plus `make lite` / `make e2e` for UI flows (CLAUDE.md).

## 5. Decisions (owner, 2026-11-18)

| # | Question | Decision |
|---|---|---|
| D1 | Per-user activate/deactivate, or admin-only? | **Per user** (it already exists). Admin influence via manifest `default_enabled` + precompute switch. |
| D2 | Remove archive upload from lite too? | **Yes.** A folder + `make new` + hot reload + Studio cover authoring. |
| D3 | Remove Python entry-point discovery (`mate.modules`)? | **Yes.** |
| D4 | Does deactivating hide cards already on dashboards? | **No.** Cards keep working; only grid, palette, nav and auto-run change. |
| D5 | Uploads `case_count` / `num_cases`? | Ask the authors to open PRs; archive offline, delete in stage 4. |
| D6 | `cv4cdd` (needs a model file) for new users? | **Stays on by default** for all users, with the model setup as it is today. No manifest change. |
| D7 | Users whose old seed flag is a bare `true` get removed defaults back once. | Accepted; mention in the release note. |

## 6. Later (not in this plan)

- Admin availability (opt-in/off for everyone): only if a concrete case appears.
- Roadmap stage 2 (sandboxed community modules): admitted at instance level into the same catalog,
  with consent per user on `module_configs`, not a revived install table.
- A dedicated `PUT /modules/{id}/active` endpoint.
- A Postgres migration CI lane.
