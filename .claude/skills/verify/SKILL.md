---
name: verify
description: Verify a Mate change before committing or opening a PR. Runs the CI lanes the diff selects (same commands as GitHub CI), then exercises the change in the running app when it touches UI, module panels or user flows. Use before every commit/PR and whenever asked to "verify", "check" or "make sure it works".
---

# Verify a change

Rules and rationale: `CLAUDE.md` ("Verification & CI rules") and `docs/ci/README.md`.

1. **See what CI will run:** `make ci-plan`. The output lists the selected lanes with reasons.
   Anything under `.github/workflows/ci.yml`, `scripts/ci/`, `Makefile` or an unknown path
   selects the full suite. That is intended.
2. **Run it:** `make check-changed` (or `make ci` for everything). Read the per-lane summary.
   - `static` red: `make fmt` fixes ruff. Pyright ratchet red means your change added type
     errors in that file; fix them, never `--update` to absorb them.
   - `test_plan.py` red about undeclared inputs: an API test now reads a file outside
     `apps/api`. Declare it in `scripts/ci/plan.py` as the message says.
   - `modules` warning "no module tests ran": that suite needs module-only deps. It is not a pass.
3. **Run the change itself** when it touches behaviour a user sees:
   - Web / module panel / dashboard: `make lite MODULES=<ids>`, open the page, drive the flow,
     check the browser console. `make e2e` covers the Studio.
   - API route: hit it on the running stack (`make lite`) and check the response, not only the tests.
   - Module: `make test ID=<id>` plus the module page in `make lite`.
4. **Report evidence**: lanes run, pass/fail, timings, and what you saw in the app. If something
   still fails after three honest attempts, stop and report what fails, what you tried and why
   it didn't work. Never skip, xfail or retry a test into green.
