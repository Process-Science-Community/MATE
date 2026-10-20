# Plan: a compulsory "How to use" guide for every module

## Context

Only `temporal_mapper` explains itself to users today, through a hard-coded TSX slide-over (`modules/temporal_mapper/panel/UserGuide.tsx`). The other modules have nothing, or have README files that mix developer and provenance notes with stale usage text.

| State | Modules |
|---|---|
| Has a usage doc | `temporal_mapper` (in-app guide + README "Try it"), `concept_drift_explainer` (README "Setup") |
| Partial | `actor_performance`, `cv4cdd`, `performance_java` (user sections inside dev READMEs) |
| None | 13 others, including `agentsimulator`, whose README is the upstream research repo's |

Two related problems in the docs:
- The manual promises something that doesn't exist: `landing/docs/content/30-module-development.md:21` says a module's `README.md` is "rendered on the module detail page".
- The two author checklists don't agree.

**Goal:**
- Every module ships one markdown guide in one fixed format.
- The platform shows it in the app (a "How to use" side panel) and in the published manual (a new "Module guides" group).
- A new module can't be uploaded without one.

**Decided with the user:**
- Shown in both the app and the manual.
- Enforced at upload and by tests, with no new CI workflow; modules already uploaded keep working.
- Fixed sections, minus "Quick start" and minus an explicit references list.

## 1. The format

The guide is `modules/<id>/HOW_TO_USE.md`:
- It has a fixed file name, not a manifest field, so the ~50 in-memory `Manifest(...)` test sites are untouched.
- `README.md` stays free for developer and provenance notes. `SIDECAR_SERVICES.md` still requires actor_performance's docker one-liner there.

```md
# How to use <manifest name>

<Lead: 1-3 sentences - what question the module answers.>

## When to use it
## Before you start        log type, required columns, minimum size; plus a second log / uploaded model / API key / sidecar / other module if needed
## Settings                table "Setting | Default | What it changes" (### "In the panel" / "On the module page" allowed), or "This module has no settings."
## Reading the results     one ### per view, chart or table
## Tips and limits
## Method                  how it works, in brief - no citation list (citations stay in manifest `source`, shown in the ⓘ popover)
```

Each rule below exists so the same file renders identically in the app (react-markdown) and in the manual's zero-dependency builder.
- **Title:** the first line is exactly `# How to use <manifest name>`, and it is the only H1. This also catches a guide copied from another module and never edited.
- **Lead:** a lead paragraph comes before the first `##`. The manual uses it as the page lead, the meta description and the `llms.txt` summary.
- **Sections:** exactly the six `##` sections, in this order, none empty, no others. `###` and `####` are allowed; nothing deeper.
- **Links:** only absolute `https://`, `http://` or `mailto:` links. Relative links break in both renderers.
- **Disallowed syntax:**
  - images, which nothing serves;
  - raw HTML and HTML comments, because a `<!-- group: -->` in a guide would hijack the manual's sidebar;
  - nested lists, which the builder flattens;
  - horizontal rules;
  - unbalanced code fences.
- **Size:** valid UTF-8, at most 64 KiB.

A copyable skeleton goes in `modules/HOW_TO_USE.template.md`. It is a loose file, so discovery and the bundler ignore it.

## 2. Validator (SDK) and enforcement

**New `packages/module-sdk-py/src/mate/sdk/guide.py`** (pyright-strict clean):
- `GUIDE_FILENAME` and `GUIDE_SECTIONS`.
- `check_guide(folder: Path, name: str) -> list[str]`: a fence-aware line scan that returns *all* problems as readable strings.
- `require_guide(...)`, which raises `ModuleManifestError` listing them.
- A CLI, `python -m mate.sdk.guide <module_dir | modules_root>...`. It reads the name via `Manifest.load_yaml`, skips `.`/`_` folders like discovery does, and exits 1 on any problem.
- These are exported from `mate.sdk/__init__.py`.

**Enforcement points:**
- **Upload (hard gate):** `apps/api/src/mate/api/modules/install_jobs.py::_read_staged_manifest` (L170) calls `require_guide(inner, manifest.name)` before anything touches disk.
  - The install job fails with the problem list in the jobs dock.
- **Boot / hot reload (warning only):** a helper `warn_on_guide_problems(folder, manifest)` logs `modules.guide.invalid` with the problems. The module still loads.
  - It is called in `discovery.py::discover` after a manifest validates, and in `ModuleLoader.load_one` (`loader.py:700`). Hot reload goes `Manifest.load_yaml` → `load_one` and bypasses discovery.
  - Existing uploads without a guide therefore keep working; they just get no button.
- **Bundled modules:** pytest gates them (see Tests). There is deliberately no GitHub Actions workflow.
- **Hot reload noise:** `hot_reload.py::_IGNORED_NAMES` (L45) gets `HOW_TO_USE.md`. The guide is read from disk per request, so editing it needs no module reload.

## 3. API (`apps/api/src/mate/api/routes/modules.py`)

- **`ModuleSummary.has_guide: bool`:**
  - It is a stat of `<folder>/HOW_TO_USE.md`.
  - The folder comes via a small `_guide_path(loader, module_id) -> Path | None` helper, resolved through `loader.loaded[id].discovered.folder` (as `get_module_asset` does, L576).
  - The helper uses `getattr` defensively, because `test_mcp_analysis.py:104` injects stubs with no `.discovered`.
- **`GET /api/v1/modules/{module_id}/guide` → `ModuleGuide {module_id, markdown}`:**
  - Ownership is checked with `_assert_owns_module` (L62), giving a 404 for non-owners, as `/manifest` does.
  - It returns 404 when the module has no guide.
  - The file is read per request.
  - No module defines a `/guide` route, so there is no clash.

## 4. Web app

- **Dependencies:** `react-markdown@10.1.0` and `remark-gfm@4.0.1` in `apps/web` (`pnpm --dir apps/web add …`). Nothing renders markdown today. These are safe by default (no raw HTML, sanitised URLs) and loaded lazily through `next/dynamic`, so they cost nothing until the panel opens.
- **Types and fetching:**
  - `lib/api-types.ts`: add `has_guide` to `ModuleSummary`. The file is hand-mirrored; never run codegen.
  - `lib/query-keys.ts`: add `moduleGuide`.
  - `lib/queries.ts`: add `useModuleGuide(id, {enabled})`, using `api<T>()`.
- **Page metadata:** in `lib/page-meta.ts`, `ModuleAbout` gains `id` and `hasGuide`, and a new `moduleIdFromPath()` covers both module routes. `components/topbar.tsx` fills these in (L111-129).
- **New `components/modules/module-guide.tsx`:**
  - **`ModuleGuideButton`:** sits in the topbar's right cluster, left of `MateTopbarButton` (L171-174), outside the pathname-keyed `AnimatePresence`. It shows a `BookOpen` icon plus "How to use", the label hidden below `md`. It is only rendered on module routes with `hasGuide`.
  - **`ModuleGuidePanel`:** a docked, non-modal right panel (~440px).
    - It reuses the MATE AI `<aside>` shell (width transition, `mate-ai-sidebar.tsx:645-654`) and is mounted beside `<MateAiSidebar />` in `app/(platform)/layout.tsx`.
    - Header: "How to use · <name>" and a close button. Body: `ScrollArea`.
    - The lead paragraph is always visible. Each `##` section is a collapsible `<details>`, the first one open, as in temporal_mapper's guide today.
    - It shows a skeleton while loading and an error line on failure.
    - Escape closes it when focus is inside.
  - **State:** a tiny non-persisted zustand store, `lib/stores/module-guide.ts`. It is deliberately not `useUi`, whose defaults are server-persisted.
    - The guide and MATE AI are mutually exclusive: opening the guide calls `setMateOpen(false)`, and the panel closes itself when `mateOpen` turns true.
    - The panel renders nothing off module routes.
  - **`GuideMarkdown`:** a react-markdown + remark-gfm renderer mapped to the app's tokens. It drops the H1 (the header shows it) and supports:
    - `###`/`####`, paragraphs and lists;
    - tables in an `overflow-x-auto` wrapper;
    - inline and block code;
    - external links with `target=_blank rel=noreferrer`;
    - blockquotes rendered as callouts, with `[!NOTE|TIP|WARNING]` markers stripped and styled.
- **temporal_mapper:** delete `modules/temporal_mapper/panel/UserGuide.tsx`, plus its import and button (`panel/index.tsx` L8, L114). The "See “How to use”" note at L125 stays true.

## 5. Manual

- **`landing/docs/build.mjs` `readContent()` (L2369):** also read `../../modules/*/HOW_TO_USE.md` into one virtual block sorted as `15-module-guides.md`, so it lands between "Using MATE" (10-) and "Platform" (20-). Every content file opens with its own group comment.
  - The block is `<!-- group: Module guides -->` followed by each guide, sorted by module name.
  - Each guide's H1 is rewritten to the module name, followed by `<!-- slug: guide-<id with _→-> -->`. The slug regex only allows `[a-z0-9-]`.
  - The search index, `llms.txt` and the pager pick the guides up automatically.
- **`landing/docs/content/10-using-mate.md`:** the bundled table (L203-225) links each module to `guide-….html` and gains the missing Temporal Mapper row. The module-page description (L189) mentions "How to use".
- **`00-getting-started.md:198-200`:** "Read a module" points to "How to use".
- **Rebuild:** `node landing/docs/build.mjs`, and commit `landing/docs/site/`, since Pages and the VM publish the committed tree. Pages runs only on `landing/**`, so the guide section of the contract says "after editing a bundled guide, run `make docs`".

## 6. Content - 18 guides

- **Rewrite into the format.** User-facing README sections move into the guide; each README keeps its dev/provenance notes plus a one-line pointer to `HOW_TO_USE.md`.
  - `temporal_mapper`: all 9 `UserGuide.tsx` sections plus README "Try it". The example CSV is linked by its absolute GitHub URL.
  - `concept_drift_explainer`: What it does, Setup, Costs.
  - `actor_performance`: intro and behaviour classes, plus the Neo4j sidecar under "Before you start".
  - `cv4cdd`: §1 and §4. This drops the stale "auto-run on import" and "click Run detection" claims. `original README.md` is upstream provenance and stays untouched.
  - `performance_java`: What it reports, Limits. This fixes "runs on every `log.imported`".
- **Create from code:** `agentsimulator`, `complexity`, `complexity_over_time`, `complexity_v2`, `complexity_v2_over_time`, `conformance`, `discovery`, `log_evolution`, `ocel_discovery`, `pcomp`, `performance`, `performance_over_time`, `process_comparison`.
- **Sources per guide:**
  - the manifest: `about`, `requirements`, and `config_schema` with its defaults;
  - `panel/**`: exact UI labels, defaults and views;
  - `module.py`;
  - widget `help:` text;
  - the README and tests.

  Every label and default must be traceable to a file and line.
- **Process:** six batches of related modules, run **sequentially with at most 2 subagents at once**, because the account's session limit tripped with 4 concurrent agents while planning. Each subagent returns the guide text plus an evidence list. I write the file, run the validator, and review the guide against its evidence.
  - Batches: temporal_mapper + log_evolution · discovery + ocel_discovery · complexity ×4 · performance ×3 · conformance + process_comparison + pcomp · cv4cdd + concept_drift_explainer + actor_performance + agentsimulator.

## 7. Contract and docs

- **`modules/README.md`:**
  - §2 layout gets `HOW_TO_USE.md  # required`.
  - A new section "The how-to-use guide" covers the format, the rules, the template, where it is shown, the CLI check and `make docs`.
  - §11: archives must contain it; uploads without it are rejected.
  - §12 gets a checklist item.
- **`30-module-development.md`:**
  - The tree at L11-22: replace the false README line with `HOW_TO_USE.md`.
  - "Your first module" gets a step to write the guide.
  - The CI snippet (L692-697) adds the guide check.
  - Distribution (L708).
  - The checklist at L725 replaces the README line.
- **Other docs:**
  - `docs/INSTRUCTIONS.md` §12 steps.
  - `docs/README.md` L41.
  - `landing/docs/README.md`: groups table row, a "Module guides" note under generated content, and the chapter count.
  - `AGENT.md`: one bullet in the module-system section.

## 8. Tests

**New `apps/api/tests/test_module_guides.py`:**
- Every bundled module passes `check_guide`. This is parametrised per folder, alongside `test_bundled_manifests_all_load` (`test_manifest_widgets.py:191`).
- Every bundled guide has a `landing/docs/site/guide-*.html` page. A failure means "run `make docs`".
- Validator unit tests cover a valid minimal guide and one case per rule.
- The CLI returns exit code 0 or 1.
- Upload without a guide: the job is `failed` and the error names `HOW_TO_USE.md`.
- `GET …/guide`:
  - owner gets 200;
  - non-owner gets 404;
  - a module without a guide gets 404 and `has_guide: false`.

**Fixture changes:**
- Add `HOW_TO_USE.md` to `apps/api/tests/fixtures/modules/sample_mod`; `sample_cards` stays guide-less as the negative case.
- Add a `minimal_guide(name)` helper to `apps/api/tests/conftest.py`.
- Include a guide in the upload zips of `test_modules_per_user.py::_module_zip` (L27) and `test_modules_loader.py::test_module_install_from_upload` (L323). Otherwise both now fail.
- Discovery, ocel_discovery and the MCP tests are unaffected, because boot only warns.

## 9. Implementation order

1. `EnterWorktree` and branch. Commits are staged by explicit paths, never `git add -A` (the `.claude/worktrees` gitlink pitfall).
2. SDK `guide.py`, CLI, exports, and unit tests.
3. API: upload gate, warnings, hot-reload ignore, `has_guide`, the guide route, test fixture updates. Commit.
4. Web: dependencies, types and hook, the store, the panel and renderer, topbar and layout wiring, the temporal_mapper cleanup. Commit.
5. The 18 guides, in batches, each validated as written, plus the README moves. Commit.
6. `build.mjs`, the manual pages, the contract and docs, and the template. Commit. Then the regenerated `landing/docs/site/` in its own commit.
7. Verify (below), push the branch, open a **draft PR**. No push to main.

## 10. Verification

- **Tests:** `uv run --frozen --extra dev pytest apps/api/tests -q -p no:cacheprovider`. The baseline is 781 passed / 20 skipped, plus the new tests.
- **Guide check:** `uv run --frozen python -m mate.sdk.guide modules` should report all 18 guides OK.
- **Type checks:**
  - `uv run --frozen --extra dev pyright` on the touched Python files, with no new diagnostics compared with `main` (around 300 are pre-existing repo-wide).
  - `cd apps/web && pnpm typecheck`.
- **Lint:** `ruff check` and `ruff format --check` on the touched files. Never run `next lint`.
- **Bundle:** `node apps/web/scripts/bundle-modules.mjs temporal_mapper` still bundles without `UserGuide`.
- **Manual:** `node landing/docs/build.mjs`, run twice to confirm the output is deterministic, plus a dead-link and anchor scan of `site/*.html` (a script in the job tmp). Spot-check `guide-discovery.html` and `guide-temporal-mapper.html`.
- **Browser** (Playwright with installed Chrome):
  - Setup: `DEMO_MODE=true`, a scratch `DATA_DIR`/`DATABASE_URL`, the API on :8011 and web on :3011. Never touch :3000/:8000 or `data/metadata.db`.
  - Import `simulated_data/input/autonomous/simulated_log_0.csv`.
  - Discovery → "How to use": screenshot in light and dark mode.
  - Toggle MATE AI; the guide closes, and the reverse also works.
  - Navigate to `/processes`; the panel is gone.
  - `/modules/temporal_mapper`: tables render, and the panel shows no duplicate guide button.

## Out of scope

- A CI workflow (user choice).
- An MCP guide tool. The guides do reach AI tools via the manual's `llms.txt`.
- Images in guides.
- The unused `frontend.panel_help`.
- The stale module table in the root `README.md`.
