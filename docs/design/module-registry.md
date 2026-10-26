# Public Module Registry and Module Versioning

**Status:** Proposed. Nothing in this document is implemented.
**Scope:** A public, curated catalogue of community modules; how instances install and update from it; how modules are versioned.
**Verified against:** `main` as of 2026-10-20. File and line references drift; re-check before acting on them.

---

## 1. Goal and non-goals

Anyone can suggest or submit a module. Admins curate. A module an admin flags as default is installed automatically on every new instance; everything else is opt-in per instance.

Non-goals: ranking, ratings, payments, a hosted multi-tenant store. The manual lists "a plugin marketplace with ranking" as out of scope (`landing/docs/content/60-project.md`), so this is a curated, unranked catalogue.

"New instances" means new deployments. New *users* on an instance already receive defaults through `modules.default_ids` and `_reconcile_default_modules`.

## 2. Decision

A separate public repo `Process-Science-Community/mate-modules` holds community module sources (vendored, not pointers). CI validates every PR. On admin merge a release job publishes one immutable `tar.gz` per module version plus a signed, multi-version `index.json` to GitHub Pages. The platform gets a thin registry client that verifies and downloads an archive, then hands it to the existing install pipeline. The 18 bundled modules stay in `modules/` as the `core` tier.

```
contributor ─ issue form / PR ─▶ mate-modules (public, protected main, CODEOWNERS)
                                   │ CI: manifest · tests · bundle · risk report · version gates
                                   ▼ admin merges
                    release: <id>-<ver>.tar.gz + index.json (+ signature) → Pages
                                   │ HTTPS
instance registry client ── verify sha256 + signature ─▶ install / upgrade job
   └▶ loader.load_one ─▶ modules.default_ids ─▶ every user seeded on first GET /modules
```

### 2.1 Alternatives rejected

| Option | Why not |
| --- | --- |
| In-tree PRs only (today, PR #13) | Every module becomes a default for every instance, first-boot venv builds grow with module count, no opt-in tier, platform `main` doubles as the module supply chain. |
| PyPI entry points (what `docs/INSTRUCTIONS.md` §7.6.2 specs as "registry") | Installs into the platform venv, bypassing per-module venvs and the content-hash/ownership pipeline; uncurated namespace; modules also carry TSX and JVM jars. |
| Pointer-only index (Obsidian style) | Author repos can vanish or force-push; SDK breaks have no owner. Kept as a later entry type: `source: git@sha`, built and published by registry CI so consumers still fetch only registry artifacts. |

## 3. Reused as-is

| Need | Existing piece |
| --- | --- |
| Extract, validate, hot-load | `modules/install_jobs.py` (`_safe_extract`, `load_one`) |
| Same id with different code; no shadowing core | `_stage_validated_upload`, `modules/content_hash.py` |
| "Everyone gets it", opt-out | `modules.default_ids`, `mandate_default_for_all_users`, `_reconcile_default_modules`, `/withhold` |
| Fresh-VM durability | `storage/module_archive.py` |
| Review checklist as CI | `modules/README.md` §12, `bundle-modules.mjs`, `runtime-externals.json` |
| Static publishing | `.github/workflows/pages.yml` |
| Contribution flow | PR, admin merge (PR #13, Temporal Mapper) |

## 4. Tiers and trust

- `core`: bundled in `modules/`, implicit defaults, `in_process` allowed. No migration.
- `community`: from the registry, opt-in per instance.
- `default: true`: only admins can set it, behind a two-approver CODEOWNERS rule on the defaults list. Only reviewed entries qualify for automatic installation.

**Review is the only security boundary today** (see §9). Auto-install therefore stays limited to reviewed defaults until the hardening in §9 exists. `MODULE_REGISTRY_URL=` (empty) disables the client for air-gapped instances. An optional `registry.lock` pins the default set (§7.4).

## 5. Registry repo

- Layout: `modules/<id>/…`, a defaults list, `platform.ref` (pinned platform tag CI validates against), templates.
- Protected `main`, required checks, CODEOWNERS (module authors co-own their folder; admins own the defaults list, release workflow and `.github/`). A push to this `main` reaches every instance, so the direct-push habit used on the platform repo must not carry over.
- CI checks out the platform at `platform.ref` so validation uses the real rules: `Manifest.load_yaml`, module tests against the `inherit` set, the esbuild bundle with the `runtime-externals.json` import allowlist, a risk report (flags `subprocess`, `socket`, `os.environ`, writes outside `ctx.workdir`, direct-URL or VCS dependencies, binaries, symlinks), `pip-audit`/`osv-scanner`, size caps, and a required SPDX `license:` from an allowlist.
- Suggest: an issue form `module-request`. Submit: a PR from a template embedding the author checklist (`modules/README.md` §12). Promotion path: community, default, core.

## 6. Versioning

### 6.1 Facts that shape the design

- `Manifest.version` is an unvalidated `str` (`manifest.py:448`). Only 3 commits ever touched `version:`; all 18 bundled modules sit at `1.0.0` (`temporal_mapper` at `0.1.0`), including modules with later code commits.
- It still drives invalidation: a log's run becomes `stale` when stored `module_version` differs from the loaded one (string `!=`, `processing.py:121-124`; column `String(64)`, `models.py:392`). The bump is the cache-invalidation signal. An unbumped code change leaves old results shown as fresh; a rollback re-stales, which is desired.
- One code body per id per process, so versions are instance-wide. `_stage_validated_upload` rejects different content for an id another user owns, and registry defaults are owned by every user, so the existing upload job cannot upgrade a shared module.
- `load_one` unloads before it builds (`loader.py:708-713`) and `_restage` deletes the old folder first, so a failed upgrade leaves the module gone for everyone with no rollback.
- `requirements.modules`, `provides` and `consumes` are unversioned strings; dashboards persist `(module_id, widget_id)` (`models.py:654`); there is no config migration hook.

### 6.2 Scheme

1. **SemVer, with the bump computed.** Registry CI diffs a contract snapshot of the new version against the last published one: widget ids, dataset ids and routes, `provides`, route paths (from the loader's bound routes), event topics, `config_schema` properties, `requirements.event_log`, hard deps, `runtime.kind` and `isolation`. Removal, rename, retype or tightening is major; addition is minor; otherwise patch. An under-bumped PR fails.
2. **Immutable releases.** `(id, version)` never changes. The same version with a different `content_hash` (existing `module_content_hash`) fails CI. Mistakes are yanked or deprecated, never overwritten. Artifacts are `<id>-<version>.tar.gz`, tagged `<id>/v<version>`, with a per-module `CHANGELOG.md` linked from the index. The same "files changed implies version changed" check runs in platform CI for core modules.
3. **Multi-version index with compat ranges.** Each id lists `versions[]` with `requires.sdk` (PEP 440 against `mate-sdk`, currently `0.2.0`), `archive{url,sha256}`, `content_hash`, `log_model`, `license`, `source[]` (citations), `default`, `yanked`, `deprecated{message,replaced_by}`. The client installs the highest non-yanked, non-prerelease version its SDK satisfies; `-rc.N` versions are indexed but never auto-selected. Compat lives in the index because `Manifest` is `extra="ignore"` (`manifest.py:444`) and older platforms silently drop new manifest keys. While the SDK is `0.x`, a minor bump may break modules, enforced through `requires.sdk` upper bounds.
4. **Inter-module constraints.** Specifiers inside the existing string lists (`requirements.modules: ["discovery>=1,<2"]`; a bare id stays valid), enforced in `topo_sort`, which today only checks presence. Registry CI runs reverse-dependency tests: every module that requires or uses the changed one, core included.
5. **No two versions of one id.** A side-by-side major is a new id plus `deprecated.replaced_by`, following the `complexity` / `complexity_v2` precedent. Per-user pinning is not possible with one code body per process.
6. **Config and dashboards.** `config_schema` changes are additive within a major; widget ids are public API. There is no migration hook, so one would be an SDK feature of its own.

## 7. Platform side

### 7.1 Registry client

New `registry_client.py`, admin routes beside `admin_modules.py`, and a Catalogue tab in Admin, Modules. It fetches the index (ETag, timeout, offline-tolerant), verifies signature and `sha256`, caps archive size, and feeds the archive to the install job. First-boot bootstrap installs `default: true` entries as a non-blocking background task, best-effort like `module_archive.py` (logged, never raised). It declares defaults with `set_admin_default_ids` and `mandate_default_for_all_users`. A removed default is remembered (tombstone) so the next sync does not reinstall it.

### 7.2 Upgrades

A new privileged `upgrade_module` path (admin/system), not the upload job:

1. Download, verify, extract and build venv and bundle in `uploaded_modules/.staging/<id>` (dot-prefixed dirs are skipped by `discovery.py` and the GC).
2. Import-check, then swap, keeping the old version in `uploaded_modules/.prev/<id>`.
3. Roll back automatically if `load_one` raises.
4. Ownership rows, configs and saved dashboards are untouched.

Apply when idle: `unload_one` ignores running jobs and stops a subprocess module's worker, and prod pins `ENV: prod` because the dev watchdog "reloads modules mid-job" (`docker-compose.prod.yml:144-146`). Apply on next restart, or via an admin "Apply" that waits for the module's jobs to finish. Results need nothing new: the version change re-stales each log's run and the lazy `ensure` recompute clears the cache.

### 7.3 Update policy

`MODULE_REGISTRY_UPDATES=manual|patch|off`, default `manual`. `patch` auto-applies same-minor bumps only to `default: true` modules, only when idle. Minor and major bumps are always an admin click. Community modules never auto-update. The catalogue shows "update available" and warns when an installed `content_hash` differs from the index for the same version (modified or tampered).

### 7.4 Lock

`registry.lock` pins `id`, `version` and `sha256`; `make modules-lock` resolves the highest compatible version. A mismatch fails loudly, like `uv sync --frozen` in `apps/api/Dockerfile`.

## 8. Supply chain

- Phase 1: `sha256` pins in the index, HTTPS only, branch protection, two-person rule on the defaults list, the client refuses an index older than the last seen one.
- Phase 3: detached ed25519 signature over `index.json`, public key shipped in the platform release (current plus next for rotation), signing job behind a reviewer-gated GitHub Environment.
- Revocation: `yanked` entries are disabled for new installs and flagged to admins; running modules are not force-removed.

## 9. Blockers to fix before a public catalogue

1. `POST /modules/install` takes `CurrentUserDep`, not `AdminUserDep` (`routes/modules.py:456-458`): any signed-in user runs code in the API process (DB, env, all users' data). Add `MODULE_INSTALL_POLICY=admin|users|off`, default `admin`.
2. `isolation: subprocess` is not a sandbox: `env={**os.environ, …}` (`subprocess_host.py:292`) passes every secret, and manifest `permissions:` is parsed (`manifest.py:488`) but read nowhere. `docs/strategy/extensibility-strategy.md` §8 Q1 made env/config redaction and T1 limits conditional on a third-party catalog; this is that catalog.
3. S3-mode boot GC (`main.py:333`) deletes `uploaded_modules/<id>` with no install row, so a registry default on a user-less fresh instance vanishes next boot. Fold `get_admin_default_ids` into `install_ids`.
4. No CI (only `pages.yml`), `main` unprotected, no CONTRIBUTING, SECURITY, CODEOWNERS or templates. `modules/README.md` §10 claims module CI exists, and §11 advertises git/PyPI channels that do not.
5. Smaller: GitHub reports the license as "Other" (probably the wrapped copyright line in `LICENSE`); `*.har` is not gitignored; manifest `license:` is optional; pm4py (GPL-3.0) against the MIT repo is an open legal item (`extensibility-strategy.md` §4.5).

## 10. Phased plan (estimates)

| Phase | Work |
| --- | --- |
| P0, platform repo (about 1 wk) | Gate the install route; scrub worker env; fix the GC seam; CI, branch protection, community files. Versioning: CI check that changed module files require a `version:` change; `Manifest` warns on non-SemVer and caps `version` at 64 chars. |
| P1, `mate-modules` repo (1–2 wk) | CI at pinned platform ref plus the version gates (SemVer check, contract diff computing the required bump, immutability check, reverse-dependency tests). Release job: immutable artifacts, tags, multi-version `index.json`. Templates, Pages gallery. |
| P2, platform (about 1.5 wk) | `requires.sdk` and specifiers in `requirements.modules` enforced in `topo_sort`; registry client; admin routes and Catalogue tab; staged `upgrade_module`; `MODULE_REGISTRY_UPDATES`; `registry.lock` and `make modules-lock`. |
| P3 | Signed index; yank/revoke handling; `deprecated.replaced_by` UI; nightly SDK-compat canary over the registry; T1 hardening for community modules. |

## 11. Open decisions

- SDK `0.x` policy: this document assumes "minor may break modules" until 1.0.
- Whether any community module may ever auto-update: assumed no.
- Whether existing instances auto-install new defaults: assumed no (`manual`); new instances do.
- License policy for the registry given the pm4py dependency: needs a call.
