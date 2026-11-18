#!/usr/bin/env python3
"""Replay git history through the CI planner.

    python3 scripts/ci/backtest.py [--rev-range main] [--verbose]

Two outputs:

1. Lane mix: for every non-merge commit, which lanes the planner would run. This
   estimates what selection saves on this repository's real change mix.
2. Owner recall: a commit that changed a test *and* non-test code is evidence that
   the code change needed that test. Plan only the non-test files and check that
   the lane owning the changed test is still selected. A miss is a planner rule
   to fix (or a reason to widen), never something to ignore.
"""

from __future__ import annotations

import argparse
import subprocess
import sys
from collections import Counter
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from plan import LANES, REPO, module_ids_with_tests, plan_for


def test_owner(path: str) -> tuple[str, str | None] | None:
    if path.startswith("apps/api/tests/"):
        return ("api", None)
    parts = path.split("/")
    if len(parts) > 3 and parts[0] == "modules" and parts[2] == "tests":
        return ("modules", parts[1])
    if path == "scripts/ci/test_plan.py":
        return ("static", None)
    return None


def commits(rev_range: str) -> list[tuple[str, list[str]]]:
    out = subprocess.run(
        ["git", "log", "--no-merges", "--format=@@%h", "--name-only", rev_range],
        capture_output=True,
        text=True,
        check=True,
        cwd=REPO,
    ).stdout
    result: list[tuple[str, list[str]]] = []
    for line in out.splitlines():
        if line.startswith("@@"):
            result.append((line[2:], []))
        elif line.strip() and result:
            result[-1][1].append(line.strip())
    return result


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--rev-range", default="HEAD")
    ap.add_argument("--verbose", action="store_true")
    args = ap.parse_args()

    mods = module_ids_with_tests()
    history = commits(args.rev_range)
    lane_runs: Counter[str] = Counter()
    full = nothing = api_narrow = 0
    checked = misses = 0
    for sha, files in history:
        plan = plan_for(files, mods)
        full += plan.full
        api_narrow += "api" in plan.lanes and bool(plan.api_targets)
        nothing += not plan.lanes
        for lane in plan.lanes:
            lane_runs[lane] += 1

        tests = [(f, test_owner(f)) for f in files]
        source = [f for f, owner in tests if owner is None]
        if not source:
            continue
        src_plan = plan_for(source, mods)
        for f, owner in tests:
            if owner is None:
                continue
            checked += 1
            lane, mod = owner
            ok = lane in src_plan.lanes and (mod is None or mod in src_plan.modules)
            if not ok:
                misses += 1
                print(f"MISS {sha}: {f} not selected by its commit's source changes")
            elif args.verbose:
                print(f"ok   {sha}: {f}")

    n = len(history)
    print(f"\n{n} non-merge commits replayed")
    print(f"  full suite: {full} ({full / n:.0%})   nothing to run: {nothing} ({nothing / n:.0%})")
    for lane in LANES:
        print(f"  {lane:8} runs on {lane_runs[lane]:3} ({lane_runs[lane] / n:.0%})")
    print(f"  api runs narrowed to named test files: {api_narrow}")
    print(
        f"owner recall: {checked - misses}/{checked} changed tests selected by their source change"
    )
    return 1 if misses else 0


if __name__ == "__main__":
    raise SystemExit(main())
