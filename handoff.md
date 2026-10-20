# Handoff: a compulsory "How to use" guide for every MATE module

Written 2026-10-20 by the session that built the platform side. **The platform side is done and verified. The content side is 5 of 18 guides.** Your job is to finish and verify the remaining guides, rebuild the manual, run the full gates, and open a draft PR.

Delete `handoff.md` and `handoff/` in your last commit (`git rm -r handoff.md handoff`); they must not reach the PR.

## The request and the decisions already made

User request, verbatim: *"Implement a how to use page for each module in the form of md + make it compulsory for every new module. For the modules that already have it, rewrite to the specific format. For the ones that don't have it yet, create it."*

Decisions the user made when asked (do not re-litigate):

| Question | Decision |
| --- | --- |
| Where shown | **App and manual.** A "How to use" panel in the app, and a "Module guides" group in the published manual, from the same file. |
| How strict | **Gate new modules, no CI.** Uploads without a valid guide are rejected. Bundled modules are checked by pytest. **No new GitHub Actions workflow.** Modules already installed keep loading (warning only). |
| Format | **Seven-section preview, with "Quick start" removed and no explicit references.** So six `##` sections: When to use it, Before you start, Settings, Reading the results, Tips and limits, Method. |

The approved plan is in `handoff/original-plan.md` (some details changed; see "Deviations").

## Where everything is

- Worktree: `/Users/yannickbrusa/Documents/MATE/.claude/worktrees/module-how-to-use-guides`
- Branch: `worktree-module-how-to-use-guides`, based on `origin/main` at `4eb28b4`. It is pushed to `origin`; check with `git ls-remote --heads origin worktree-module-how-to-use-guides`. If the worktree is gone, recreate it from that branch.
- **Never push to `main`, never force-push.** Open a draft PR.
- Fresh worktree setup (no `.venv` or `node_modules` in git): `uv sync --frozen --extra dev --all-packages` and `pnpm install --frozen-lockfile --prefer-offline`.

Commits on the branch, oldest first:

```
cb40d02 Add the module guide validator to the SDK
04ddbfc Require a how-to-use guide for uploaded modules and serve it
59a76d7 Disallow every HTML tag in module guides, <kbd> included
722e2af Show a module's guide in a "How to use" panel
560b375 Reject underscore emphasis in module guides
bf19a4f Add the guide template and Temporal Mapper's guide
ab5b8bd Document the compulsory how-to-use guide in the author contract
85fcd53 Publish module guides in the manual
850c434 Test that every bundled module has a valid, published, fresh guide
1812dd8 WIP: unverified draft guides for four modules
(next)  WIP: handoff notes (delete before merge)
```

## What is built (platform side)

| Piece | Where | State |
| --- | --- | --- |
| Format checker + CLI | `packages/module-sdk-py/src/mate/sdk/guide.py` | Done. `check_guide`, `require_guide`, `python -m mate.sdk.guide <folder or root>`. Deliberately **not** exported from `mate.sdk` (runpy warns when a `-m` module was already imported by its package). |
| Upload gate | `apps/api/src/mate/api/modules/install_jobs.py` (`_read_staged_manifest`) | Done. Missing or invalid guide fails the install job with every problem listed. |
| Boot / hot-reload warning only | `apps/api/src/mate/api/modules/guides.py`, called from `discovery.py` and `loader.load_one` | Done. Logs `modules.guide.invalid`, never blocks a load. `hot_reload.py` ignores `HOW_TO_USE.md` edits. |
| API | `routes/modules.py` | Done. `ModuleSummary.has_guide`; `GET /api/v1/modules/{id}/guide` (owner-only like `/manifest`, read from disk each request, symlinks leaving the folder refused). |
| Web panel | `apps/web/components/modules/module-guide.tsx`, `guide-markdown.tsx`, `lib/guide-parse.ts`, `lib/stores/guide-panel.ts` | Done. Topbar button + docked non-modal panel; first section open; Expand/Collapse all; exclusive with MATE AI; overlay below `lg`; markdown libs lazy-loaded. |
| Manual | `landing/docs/build.mjs` (`readModuleGuides`) | Done in code. Splices guides in as a "Module guides" group (title = module name, slug `guide-<folder>`) and writes `site/guides.json` (SHA-256 per guide). **`site/` itself is not rebuilt yet.** |
| Contract docs | `modules/README.md` §2 ("The how-to-use guide"), §11, §12; `30-module-development.md`; `docs/INSTRUCTIONS.md` §12; `docs/README.md`; `AGENT.md`; `landing/docs/README.md`; template `modules/HOW_TO_USE.template.md` | Done. |
| temporal_mapper | `modules/temporal_mapper/` | Done. Hard-coded `panel/UserGuide.tsx` deleted; guide written and hand-verified; README trimmed to method spec + pointer; the second paper the old in-app guide cited was added to the manifest `source` so it still shows in the info popover. |

Verified, with the evidence:
- `pytest apps/api/tests/test_module_guides.py`: **90 pass, 26 fail by design** (the 26 are 13 modules × 2 bundled-module tests; see below).
- Full `pytest apps/api/tests` exited 0 after the API commit. **It has not been re-run in full since**; do that at the end.
- pyright: no new errors in touched files (only the repo-wide `reportMissingTypeStubs` warnings). ruff check + format clean on touched files at last check.
- `pnpm --dir apps/web typecheck` clean; `node apps/web/scripts/bundle-modules.mjs temporal_mapper` builds.
- **Real browser** (Chrome via Playwright, demo mode, scratch DB): 24/24 checks pass for temporal_mapper: button, docked layout (page column 1384→944px), six sections, tables, expand/collapse, MATE AI exclusivity both ways, Escape + focus return, dark mode, overlay at 900px, panel closes on `/processes`. Driver: `handoff/drive.mjs`.

**Not verified:**
- The 4 draft guides (below). Nobody has checked a single label or default in them.
- **I took screenshots but never looked at them.** Re-run the driver and view `01`–`06` (light, dark, narrow, tables) before calling the UI done.
- The built manual pages were only checked structurally (headings, tables, link). Open one in a browser.
- Production build: `next build` was not run (only `next dev`). react-markdown/remark-gfm are ESM and loaded through `next/dynamic` with `ssr:false`; worth one `pnpm --dir apps/web build` if time allows.
- The browser run logged two 401s: `GET /api/v1/usage/config` and `POST /api/v1/usage/sync` (analytics calls, demo token). Almost certainly pre-existing and unrelated to the guide, but I did not confirm it against `main`.

## What remains

Progress meter: `uv run --frozen python -m mate.sdk.guide modules` (currently **5/18 valid**).

### 1. Guides (the bulk of the work)

| Module | Status | Notes |
| --- | --- | --- |
| temporal_mapper | **Done, verified** | The reference example: match its depth and tone. |
| discovery | Draft, **unverified** | 181 lines. Views: DFG, Petri net, process tree, prefix tree, heuristics net, BPMN; per-view settings popovers; eager precompute; config options. |
| log_evolution | Draft, **unverified** | 68 lines. |
| performance | Draft, **unverified** | 83 lines. |
| performance_over_time | Draft, **unverified** | 87 lines. |
| ocel_discovery | **To do** (new) | Object-centric logs only. |
| performance_java | **To do, REWRITE** | README has the user-facing parts ("What it reports", "Limits") and stale text ("runs on every `log.imported`"; precompute is lazy now). Move user text into the guide, trim the README. |
| complexity, complexity_over_time, complexity_v2, complexity_v2_over_time | **To do** (new) | One family: shared metric hints (`panel/metric-info.tsx`). |
| conformance | **To do** (new) | Upload a BPMN, technique choice, deviations. |
| process_comparison | **To do** (new) | Two filtered sides, tabs. |
| pcomp | **To do** (new) | Needs a second log; permutation and bootstrap tabs. |
| cv4cdd | **To do, REWRITE** | README is stale ("auto-run on import", "click Run detection": the panel has no such button). `original README.md` is upstream provenance: do not touch. |
| concept_drift_explainer | **To do, REWRITE** | Needs cv4cdd, an OpenAI key and Pinecone; not confidential-safe. |
| actor_performance | **To do, REWRITE** | Neo4j sidecar. **Keep the sidecar `docker run` one-liner in the README**: `modules/SIDECAR_SERVICES.md` requires it there. |
| agentsimulator | **To do** (new) | Its README is a copy of the upstream research repo, not a user guide. |

**How to write one.** Everything is in `handoff/guide-brief.md`: read it first. It is the exact brief I gave subagents (format, section contents, sources in priority order, rewrite rules, report format). Short version:
1. Read `modules/HOW_TO_USE.template.md` and `modules/temporal_mapper/HOW_TO_USE.md`.
2. Read the module's `panel/**/*.tsx`, `manifest.yaml`, `module.py`, widgets, README, tests.
3. Write `modules/<folder>/HOW_TO_USE.md`; run `uv run --frozen python -m mate.sdk.guide modules/<folder>` until it says `ok`.
4. **Verify against code** (next paragraph), then fix.

**The accuracy rule that matters most.** The checker proves the *shape*, never the *truth*. Writing the reference guide from memory, I still made three factual errors that only a re-read of the code caught: (a) claimed changing a setting "clears the map" when the handler only hides the heatmap and the selection; (b) attached a quantity to the wrong period (all of a case's transition counts go to the period of its *first* cross-activity transition's source); (c) invented an author's first name. Verify every bolded label, default, range, limit, and "what happens when" statement:

```bash
grep -o '\*\*[^*]*\*\*' modules/<id>/HOW_TO_USE.md | sort -u     # every bold UI label in the guide
grep -rn "<label text>" modules/<id>/panel                         # does each exist on screen?
```

The 4 drafts need exactly this pass. Treat any claim you cannot trace to a file and line as wrong and cut it.

**Subagents and the rate limit.** I ran two general-purpose agents at once for wave 1. **Both died mid-run on the API session limit (HTTP 429)**, leaving the four unverified drafts and nothing for their other modules. An earlier Plan agent, started right after three Explore agents, hit the same limit. Either write guides yourself, or run **one agent at a time**, one or two modules each, and expect a 429 to cut a run short (check `git status` for partial files afterwards). They write files directly and run the checker; you still verify.

### 2. Rebuild the manual (after the last guide)

```bash
node landing/docs/build.mjs      # deterministic: run twice, the output must not change
```

- `landing/docs/site/` is currently **dirty from partial test builds** (modified pages plus untracked `guide-*.html` and `guides.json`). Do not commit it piecemeal; rebuild once at the end and commit the whole directory in its own commit.
- Expect 47 pages (29 existing + 18 guides); `landing/docs/README.md` already says 47. Confirm with `ls landing/docs/site/*.html | wc -l`.
- Scan the built pages for dead links and anchors (the manual's README asks for it): every `href="x.html#id"` must resolve. The Modules chapter table links all 18 `guide-<folder>.html` pages.
- Open one guide page in a browser.

### 3. Final gates

```bash
uv run --frozen --extra dev pytest apps/api/tests -q -p no:cacheprovider    # all pass, including the 26 that fail now
uv run --frozen python -m mate.sdk.guide modules                            # 18/18
uv run --frozen --extra dev ruff check <touched .py files> && uv run --frozen --extra dev ruff format --check <touched .py files>
uv run --frozen --extra dev pyright --outputjson <touched files> > <tmp>/p.json    # judge by touched lines only
pnpm --dir apps/web typecheck
node apps/web/scripts/bundle-modules.mjs temporal_mapper
```

Baseline noise to ignore (from the repo's working notes, not re-measured this session): pyright reports ~300 errors repo-wide on `main`; `ruff format --check` flags 4 untouched files. Never run `next lint` (no ESLint config; it prompts and hangs).

Re-run the browser check (below), this time **looking at the screenshots**, and again with a second module that has tables and callouts.

### 4. Ship

1. `git rm -r handoff.md handoff`, commit.
2. `git push` the branch (plain push), then `gh pr create --draft` against `main`. `gh` is authenticated as `brusayannick`. Body: summary, the three user decisions, how to test, and the end line `🤖 Generated with [Claude Code](https://claude.com/claude-code)`.
3. Tell the user in the PR/summary: merging to `main` republishes the manual (GitHub Pages workflow, and a VM timer syncs `landing/` every 5 minutes) but **does not deploy app code** (`make deploy` does), so the manual will describe the "How to use" button before it exists on the VM.
4. Commit messages end with `Co-Authored-By: Claude Sonnet 5.5 <noreply@anthropic.com>` (use whatever attribution your session's reminder gives).

## Browser check recipe (scratch environment; I stopped everything)

Never use the repo's `data/metadata.db`, and never touch `:3000`/`:8000`. **`:3011` was occupied by another session's `social-login` worktree dev server: leave it alone.** The older notes suggest `:8011`/`:3011`; parallel sessions collide there, so I used **API `:8021`, web `:3021`** (check `lsof -nP -iTCP:<port> -sTCP:LISTEN` first). Scratch dir `S` below is any empty directory outside the repo.

```bash
mkdir -p S/data S/modules && cp -R modules/temporal_mapper S/modules/      # a one-module modules dir boots in seconds
DATA_DIR=S/data DATABASE_URL=sqlite+aiosqlite:////S/data/metadata.db uv run --frozen alembic -c apps/api/alembic.ini upgrade head
# background:
DATA_DIR=S/data DATABASE_URL=sqlite+aiosqlite:////S/data/metadata.db MODULES_DIR=S/modules MCP_ENABLED=false DEMO_MODE=true DEMO_ADMIN=true CORS_ORIGINS='["http://localhost:3021"]' uv run --frozen uvicorn mate.api.main:app --app-dir apps/api/src --port 8021
# background:
NEXT_PUBLIC_API_URL=http://localhost:8021 INTERNAL_API_URL=http://localhost:8021 AUTH_URL=http://localhost:3021 DEMO_MODE=true DEMO_ADMIN=true AUTH_TRUST_HOST=true AUTH_SECRET=anything pnpm --dir apps/web exec next dev --port 3021
curl --retry 60 --retry-delay 2 --retry-connrefused -s -o /dev/null -w "%{http_code}\n" http://localhost:8021/health
curl --retry 60 --retry-delay 2 --retry-connrefused --max-time 180 -s -o /dev/null -w "%{http_code}\n" http://localhost:3021/login   # first hit compiles
curl -X PUT -H "Authorization: Bearer demo-access-token" -H "Content-Type: application/json" -d '{"completed":true,"experience_level":"intermediate","tour_completed":true}' http://localhost:8021/api/v1/onboarding   # else the onboarding overlay covers every page
npm --prefix S install playwright-core                                     # drives the installed Google Chrome
```

Then copy `handoff/drive.mjs` into `S/` (it must sit where `playwright-core` resolves), fix the paths at its top (`OUT` for screenshots, `CSV`; ports are already 3021/8021), and run `node drive.mjs temporal_mapper "Temporal Mapper"`. It imports `simulated_data/input/autonomous/simulated_log_0.csv` through the real wizard on first run. To test another module, copy that module folder (with its `.dist`: run `node apps/web/scripts/bundle-modules.mjs <id>` first) into `S/modules/`; pick one whose manifest has no heavy `dependencies.python.packages`, or the venv build takes minutes. Stop servers by killing the port's listener: `lsof -ti tcp:8021 -sTCP:LISTEN | xargs kill` (same for 3021), never a broad `pkill`.

## Deviations from `handoff/original-plan.md`

- `mate.sdk.guide` is **not** re-exported from `mate.sdk` (runpy warning on `-m`).
- The checker bans **all** HTML including `<kbd>` (react-markdown shows HTML as literal text) and `_underscore_` / `__bold__` emphasis (the manual's builder does not render it). Neither was in the plan.
- Added `site/guides.json` plus a freshness test, so an edited guide with a stale manual page fails pytest. Not in the plan.
- Manual chapter title is the module name (not "How to use X"); the slug uses the **folder** name (equal to the id for all 18).
- The store is `useGuidePanel` (`lib/stores/guide-panel.ts`), not part of the server-persisted `useUi`.
- `_module_zip` in `test_modules_per_user.py` gained `with_guide` / `guide_text` options; `tests/guide_text.py` holds `minimal_guide()`.
- Added the 2025 BPM paper to temporal_mapper's manifest `source` (IEEE style, DOI only in `url`) because the removed in-app guide was the only UI place citing it.

## Environment gotchas that cost me time

- **Worktree guard.** In this isolated session, compound or complex `git` commands, heredocs, shell variables and `cd` into the main checkout are refused. Use plain separate commands, literal paths, `git commit -F <message file>`, and put scripts in files. `git add a b && git commit -F m` worked. Avoid `cd`: one stray `cd` changed the session's working directory (use `pnpm --dir`).
- **Commit with explicit paths.** A `git rm` leaves a staged deletion that a later `git commit` sweeps in. Use `git add <paths>` and check `git diff --cached --name-only` first; never `git add -A` (it sweeps `.claude/worktrees/*` in as gitlinks).
- The API reads `./.env` from its cwd; a worktree has none, which is why demo mode needs the explicit env vars above.
- `Edit` occasionally returns "auto mode classifier gave no verdict": transient; retry once.
- A first `next dev` hit compiles the route and shows a "Caching workspace…" splash; wait for it to disappear before screenshots. Never wait for `networkidle` (SSE keeps it busy).
- Scratch files from my session lived in `/Users/yannickbrusa/.claude-secondary/jobs/e2caa0e8/tmp/` (cleaned up when that job is deleted). Everything you need was copied into `handoff/`.

## Files in `handoff/`

| File | What it is |
| --- | --- |
| `guide-brief.md` | The full brief for writing/verifying a guide (reuse it verbatim for subagents or yourself). |
| `drive.mjs` | The Playwright driver that produced the 24/24 browser result. |
| `original-plan.md` | The approved plan, for reference (see "Deviations"). |
