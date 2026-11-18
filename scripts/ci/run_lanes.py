#!/usr/bin/env python3
"""Run CI lanes locally - the same commands the GitHub workflow runs.

    make check-changed            # lanes the planner selects for HEAD vs origin/main (+ uncommitted)
    make ci                       # every lane (the full suite)
    python3 scripts/ci/run_lanes.py --lanes static,api

Prints the plan, runs each lane, and reports per-lane wall time so feedback-time
regressions are visible locally too. Exit code is non-zero if any lane failed.
"""

from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from plan import (
    LANES,
    REPO,
    Plan,
    changed_files,
    module_ids_with_tests,
    plan_for,
    summary_markdown,
)


def lane_commands(lane: str, modules: list[str], api_targets: list[str]) -> list[list[str]]:
    if lane == "api" and api_targets:
        return [["make", "ci-api", f"API_TESTS={' '.join(api_targets)}"]]
    if lane == "modules":
        return [["make", "ci-modules", f"MODULES_TO_TEST={' '.join(modules)}"]] if modules else []
    return [["make", f"ci-{lane}"]]


def working_tree_changes() -> list[str]:
    out = subprocess.run(
        ["git", "status", "--porcelain", "--untracked-files=all"],
        capture_output=True,
        text=True,
        check=True,
        cwd=REPO,
    ).stdout
    files: list[str] = []
    for line in out.splitlines():
        path = line[3:]
        files.extend(p.strip('"') for p in path.split(" -> "))
    return files


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--base", default=os.environ.get("BASE", "origin/main"))
    ap.add_argument("--full", action="store_true")
    ap.add_argument("--lanes", help="comma-separated lanes to run (skips planning)")
    ap.add_argument("--dry-run", action="store_true")
    args = ap.parse_args()

    mods = module_ids_with_tests()
    if args.full:
        plan = Plan()
        plan.make_full("forced-full", mods)
    elif args.lanes:
        plan = Plan()
        for lane in args.lanes.split(","):
            plan.add(lane, "explicit")
        if "modules" in plan.lanes:
            for m in mods:
                plan.add_module(m, "explicit")
    else:
        try:
            files = sorted(set(changed_files(args.base, "HEAD")) | set(working_tree_changes()))
        except (RuntimeError, subprocess.CalledProcessError) as exc:
            print(f"plan: {exc}; running the full suite", file=sys.stderr)
            plan = Plan()
            plan.make_full("diff-unavailable", mods)
        else:
            plan = plan_for(files, mods)

    print(summary_markdown(plan))
    if args.dry_run:
        print(json.dumps(plan.to_json(), indent=2))
        return 0

    results: list[tuple[str, int, float]] = []
    for lane in LANES:
        if lane not in plan.lanes:
            continue
        for cmd in lane_commands(lane, sorted(plan.modules), plan.api_targets):
            print(f"\n=== {lane}: {' '.join(cmd)}", flush=True)
            start = time.monotonic()
            rc = subprocess.call(cmd, cwd=REPO)
            results.append((lane, rc, time.monotonic() - start))

    print("\n=== summary")
    for lane, rc, secs in results:
        print(f"  {lane:<8} {'ok  ' if rc == 0 else 'FAIL'} {secs:7.1f}s")
    if not results:
        print("  nothing to run for this change (docs/prose only)")
    return 1 if any(rc for _, rc, _ in results) else 0


if __name__ == "__main__":
    raise SystemExit(main())
