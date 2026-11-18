# CI and verification

How Mate's checks are organised, why, and the rules every change follows. The
short form of the rules lives in [`CLAUDE.md`](../../CLAUDE.md); this file is the
rationale and the measurements behind them.

Sources this design adapts (not copies):
[Making OpenClaw CI faster and 95% cheaper](https://vincent.build/openclaw-ci-cost-reduction/)
and its [`ci.yml`](https://github.com/openclaw/openclaw/blob/a7d05c7af04aa17ac57c994da238d0c60f058ca6/.github/workflows/ci.yml),
and [How the Claude Code team thinks about verification](https://claude.dev/blog/how-the-claude-code-team-thinks-about-verification/).

## 1. Shape

```
pull_request ─► plan ──► static ─┐
                 │ ──► api     ─┤
 (scripts/ci/    │ ──► modules ─┼─► CI gate   (the one required check)
  plan.py)       │ ──► web     ─┤
                 └ ──► jvm     ─┘
```

| Lane | `make` target | What it proves | Local time (4 cores) |
|---|---|---|---|
| `static` | `ci-static` | ruff lint + format, module manifests, `HOW_TO_USE.md` guides, planner tests, pyright ratchet | 25 s |
| `api` | `ci-api` | `apps/api/tests` (981 tests), parallel via pytest-xdist | 62 s (176 s serial) |
| `modules` | `ci-modules` | each selected `modules/<id>/tests` on the platform env | ~60 s for all 16 |
| `web` | `ci-web` | `tsc --noEmit` for `apps/web` + esbuild of every module panel | 5-17 s |
| `jvm` | `ci-jvm` | `make jvm` (incl. SDK unit tests) + cross-runtime conformance, on JDK 21 | see §6 |

CI runs exactly the `make ci-*` targets, so a local run is the same check.

| Event | What runs |
|---|---|
| PR (any push, drafts included) | lanes selected by the planner |
| push to `main` | full suite (low volume, about 3 commits a day, keeps every main commit's evidence) |
| weekly schedule (Mon 04:17 UTC) | full suite, catches runner/toolchain drift no diff triggers |
| `workflow_dispatch` | full suite |

Rollback switch: set repository variable `MATE_CI_SELECTION=full` and every PR runs
the full suite. No workflow edit needed.

## 2. Selection rules (`scripts/ci/plan.py`)

Deterministic: same diff and same rules give the same plan. Every lane carries the
names of the rules that selected it (shown in the job summary).

- **Unknown path -> full suite.** A path no rule recognises never skips anything.
- **CI contract -> full suite.** `.github/workflows/ci.yml`, `scripts/ci/**`, `Makefile`,
  root `pyproject.toml`, `.python-version`.
- `apps/api/**` -> static + whole API suite. **No per-test selection inside the API
  suite**: `conftest.py` boots the whole app for most tests, and tests read files
  across the repo (see below), so a test-level import graph would be unsound.
- `packages/module-sdk-py/**`, `uv.lock` -> static + API + every module suite.
- `modules/<id>/**` -> static + API (it loads every bundled manifest) + that module's
  suite; frontend files add `web`; `performance_java` adds `jvm`.
- `modules/**/*.md`, `landing/docs/site/**` -> guide validation + only the API test
  files listed in `API_NARROW_INPUTS` (they are the only readers, guarded below).
- `apps/web/**`, TS packages, `pnpm-lock.yaml` -> web. The API suite is added only for
  the web files it reads (`API_DECLARED_INPUTS`).
- compose files, `.env.example`, `infra/**`, `scripts/**` -> API suite (tests read
  several of them; there is no dedicated ops lane).
- Prose (`docs/**`, top-level `*.md`, `landing/**`, `papers/**`, agent/editor config)
  -> nothing but the plan and the gate.

### Declared out-of-tree inputs (the guard)

An import graph cannot see a test that reads `apps/web/lib/runtime-externals.json`
from disk. `scripts/ci/test_plan.py` scans every API test for repo-root path
building (`parents[3] / "x" / ...`, aliases, globs, variable segments) and fails if:

- a path an API test reads would *not* select the API suite (`API_DECLARED_INPUTS`), or
- a narrowed input (`API_NARROW_INPUTS`) is read by a test that is not in its list.

Both failures say which constant to edit. When adding a test that reads a file
outside `apps/api`, expect this guard to fail once and declare the input. Never
work around it by skipping.

Both guards were checked against planted faults: removing one declared input,
and the `ocel_discovery` test that copies a whole module folder. Each was caught.

### Backtest

`python3 scripts/ci/backtest.py` replays history through the planner. On the 87
non-merge commits up to 2026-11-18:

- owner recall: **404/404**. Every test file changed in a commit is still selected
  when only that commit's non-test files are planned.
- full suite 14%, nothing to run 18%, API suite 80% (30 of those narrowed to the
  guide tests), module suites 18%, web 29%, jvm 14%.

Re-run it after changing rules. A `MISS` is a rule bug: fix the rule or widen it.

## 3. Baseline (2026-11-18, before this CI existed)

- The only workflow was `pages.yml` (landing deploy). **No check ran on PRs or main.**
- Result of that: `main` had drifted: 1 ruff error, 4 unformatted files (fixed in
  the commit that introduced CI), **306 pyright strict errors** in 31 files,
  `next lint` unconfigured (interactive prompt, so not a usable gate), and **2 failing
  `discovery` layout-router tests** (deterministic `KeyError` in
  `modules/discovery/layout/obstacles.py`, reproduced 3/3).
- API suite: 981 passed / 20 skipped in 171 s serial; 55-71 s with `-n 4`, identical
  results across 4 parallel runs (including one under CPU contention).
- Module suites: 16 suites, ~60 s total, cv4cdd 25 s the largest.
  `concept_drift_explainer` collects nothing on the platform env (its deps live only
  in the module's own venv); the lane reports this as a warning, never as a pass.

## 4. Cost and feedback time

**Measured on GitHub** (run [44275332842](https://github.com/Process-Science-Community/MATE/actions/runs/44275332842),
full suite, ubuntu-latest, warm caches, 2026-11-18):

| Job | Job wall time | of which the `make` step | Queue |
|---|---|---|---|
| plan | 5 s | - | 3 s |
| static | 30 s | 15 s | 3 s |
| web | 28 s | 9 s | 3 s |
| modules | 55 s | 42 s | 3 s |
| jvm | 84 s | 66 s | 3 s |
| api | 97 s | 84 s | 3 s |
| CI gate | 4 s | - | 3 s |

- Feedback time for a full run: about **1 min 50 s** from push to gate (critical path
  plan → api → gate). The API lane is the critical path; serial it would be ~3 min longer.
- Compute for a full run: ~5 runner-minutes. Per-job overhead (start, checkout, cached
  `uv sync`/`pnpm install`) is **13-19 s**, not the 40 s I assumed before measuring.
- Replaying the 87 commits with these measured job times: always-full ≈ 454
  runner-min, with selection ≈ 168 runner-min (**63% less**), and 60% fewer jobs.
  Conservative: a partial module selection is still priced as the full 55 s job. A docs-only PR (18% of
  history) costs plan + gate, about 10 s.

Compute for public repositories on GitHub-hosted runners is free. The cost here is
mainly **feedback time**, and the shared queue when agents push in parallel.

## 5. Rules (also in CLAUDE.md)

1. **Run the cheap checks before pushing.** `make check-changed` runs what CI would
   run for your diff. Don't spend shared CI on a lint error you could catch locally.
2. **Preserve required checks.** `CI gate` is the required check. Never weaken a lane,
   gate condition or ratchet to make a change pass.
3. **When selection is uncertain, run the full suite.** Add rules that widen, never
   rules that skip on a guess. New file reads from outside `apps/api` get declared.
4. **No flaky-test workarounds.** No `skip`, `xfail`, sleeps or retries to get green.
   Fix the root cause; if that's impossible, delete the test and record why in the commit.
5. **Make every test earn its place.** A test must name the behaviour it protects
   and fail for a believable bug. Prefer consolidating to adding. Deleting a
   redundant test is a deliverable; deleting the only guard for a contract is not.
6. **Ratchets only go one way.** `scripts/ci/pyright-baseline.json` may only fall:
   after fixing type errors, run `uv run python scripts/ci/pyright_ratchet.py --update`.
7. **Measure, don't assume.** Changing CI means re-running `backtest.py` and reporting
   lane times before and after, with correctness, feedback time and compute side by side.
8. **Verification means running the change.** Tests are necessary, not sufficient:
   for UI or flow changes, drive the app (`make lite`, `make e2e`) and say what you saw.
9. **Three strikes, then a human.** If a check still fails after three honest fix
   attempts, stop and report what failed, what you tried and why it didn't work.

## 6. Known gaps and next steps

- **Branch protection is a repository setting**: mark `CI gate` as required on
  `main` (Settings → Branches). Also consider a CODEOWNERS entry for
  `.github/workflows/` and `scripts/ci/`. A PR runs the workflow from its own head,
  so a change could weaken its own checks; review those paths with extra care.
- **JVM SDK deadlocks on JDK 17** (found by the first CI run, 2026-11-18).
  `WireConnection` reads and writes one blocking `SocketChannel` through
  `Channels.newInputStream`/`newOutputStream`. On JDK 17 those adapters hold the
  channel's `blockingLock()` while blocked in `read`, so a write from another
  thread (every outbound request) waits forever. Reproduced locally: all 6
  `WireConnectionTest` tests hang on JDK 17 and pass on JDK 21; a 30-line
  standalone repro shows the same 3/3. Production ships Temurin 21, so the platform
  is unaffected, but the README promises "JDK 17 or newer" to module authors. The
  `jvm` lane runs on 21 (the shipped runtime). Either fix the SDK to read and write
  the channel directly, or raise the documented minimum to 21.
- **No Docker build check.** Dockerfiles and compose changes run the API suite only.
  An image build on `main`/schedule is a candidate if image breakage shows up.
- **No ESLint gate.** `next lint` has no config. Add one (prefer a non-interactive flat
  config) before gating on it.
- **Module suites run on the platform env**, so module-only dependencies make tests
  skip. Running each suite in its materialised module venv would close that gap.
- **Playwright e2e (`make e2e`)** is not in CI: it needs a running lite stack.
  A scheduled or `workflow_dispatch` lane is the cheapest next step.
- Track PR-open to merge (agent cycle time) next to the per-lane times in §4, so
  CI changes are judged on delivery and not only on runner-minutes.
- Actions on Node 20 (`checkout@v4`, `setup-uv@v6`, `setup-java@v4`,
  `setup-gradle@v4`) emit a deprecation warning; bump them when Node-24 majors are verified.
