# CLAUDE.md

Project memory for Claude Code. Orientation and conventions live in AGENT.md (imported below);
this file adds the verification and CI rules every change follows. Rationale and measurements:
[`docs/ci/README.md`](docs/ci/README.md).

@AGENT.md

## Verification & CI rules (apply to every change)

**Before you call work done**
- Run `make check-changed`. It runs the CI lanes your diff selects (vs `origin/main`, including
  uncommitted files) with the same commands CI uses. `make ci` runs everything. Never push to find
  out whether lint or tests pass.
- Verification means running the change, not only its tests. For UI, module panel or user-flow
  changes, drive the app (`make lite`, `make e2e`, the `run` skill) and report what you saw.
- If a check still fails after three honest fix attempts, stop and report what fails, what you
  tried and why it didn't work. Don't keep retrying, and don't weaken the check.

**Checks are not negotiable**
- `CI gate` (`.github/workflows/ci.yml`) is the required check. Never weaken a lane, the gate, the
  planner or a ratchet to get a change through. A change to CI must not loosen the checks that
  judge it.
- No flaky-test workarounds: no `skip`/`xfail`/`sleep`/retry-until-green. Fix the root cause. If that
  is impossible, delete the test and say why in the commit message.
- Pyright is ratcheted (`scripts/ci/pyright-baseline.json`, 306 errors at introduction). New code
  must not add errors. After fixing some, lower the ceiling:
  `uv run python scripts/ci/pyright_ratchet.py --update`.
- The JVM SDK must work on JDK 17 (the documented minimum) and 21 (what production ships);
  the `jvm` lane runs the SDK tests on both. Never drop 17 from the lane to get green.

**Test selection (`scripts/ci/plan.py`)**
- Deterministic path rules. Unknown paths and CI-contract files run the full suite. When
  selection is uncertain, widen and never skip. `make ci-plan` shows what a diff selects and why.
- Writing an API test that reads a file outside `apps/api` (web source, compose, `.env.example`,
  `landing/`, a module folder)? Declare it in `API_DECLARED_INPUTS` (or `API_NARROW_INPUTS`).
  `scripts/ci/test_plan.py` fails until you do. That failure is the guard working.
- After changing planner rules: `uv run --extra dev pytest scripts/ci/test_plan.py` and
  `python3 scripts/ci/backtest.py` (owner recall must stay 100%).

**Tests earn their place**
- A new test names the behaviour it protects and would fail for a believable bug. Don't add tests
  that restate the implementation, duplicate an existing contract, or only raise the count.
- Prefer extending or consolidating an existing test to adding a new file. Deleting redundant
  tests is a valid deliverable. Never delete the only guard for a contract.
- Keep tests fast and isolated (the API suite runs under pytest-xdist): no shared mutable files
  outside `tmp_path`, no dependence on test order.

**Changing CI itself**
- Measure before and after (lane time, jobs, `backtest.py`) and report correctness, feedback
  time and compute side by side. Don't assume another project's tools or numbers transfer.
- Run cheap checks early and expensive ones only where their answer is needed (PR selection,
  full on `main`/weekly). Pack small work into one job instead of matrix fan-out.
