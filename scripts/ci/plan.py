#!/usr/bin/env python3
"""Deterministic CI planner: map a git diff to the CI lanes it can affect.

    python3 scripts/ci/plan.py --base origin/main [--head HEAD] [--json] [--github-output FILE]
    python3 scripts/ci/plan.py --full            # force the whole suite
    python3 scripts/ci/plan.py --files a.py b.md # plan for an explicit path list

Rules (see docs/ci/README.md):
  * Same diff + same rules -> same plan. No history, no model, no network.
  * Every selected lane carries the reasons (rule names) that selected it.
  * A path no rule recognises selects the FULL suite. Uncertainty widens the run;
    it never justifies a skip.
  * Changes to the CI itself (this planner, the workflow, the Makefile, lockfiles
    that feed every lane) select the FULL suite.

Stdlib only so it runs before any dependency install.
"""

from __future__ import annotations

import argparse
import fnmatch
import json
import os
import subprocess
import sys
from collections.abc import Iterable, Sequence
from dataclasses import dataclass, field
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]

LANES = ("static", "api", "modules", "web", "jvm")

# ---------------------------------------------------------------------------
# Inputs that the API test suite reads from *outside* apps/api. The API suite is
# otherwise skipped for changes confined to apps/web, landing/, docs and the JVM
# SDK. `scripts/ci/test_plan.py::test_api_out_of_tree_reads_are_declared` scans
# apps/api/tests for repo-root path building and fails if a read is not covered
# here; add the path here (not a skip) when it fails.
# ---------------------------------------------------------------------------
API_DECLARED_INPUTS: tuple[str, ...] = (
    "apps/web/lib/runtime-externals.json",
    "apps/web/lib/module-runtime.ts",
    "apps/web/lib/social-idps.ts",
    "apps/web/components/brand-icons.tsx",
    "apps/web/app/api/account/**",
    "apps/web/app/(auth)/login/**",
)

# Inputs read by *only* the listed API test files. A change confined to these runs
# just those files instead of the whole API suite. The guard test checks that no
# other API test reads them; any other rule that selects the API suite wins.
API_NARROW_INPUTS: dict[str, tuple[str, ...]] = {
    # Bundled module guides + the manual pages built from them.
    "modules/*.md": (
        "apps/api/tests/test_module_guides.py",
        # Copies the whole ocel_discovery folder (guide included) into a test app.
        "apps/api/tests/test_ocel_discovery_module.py",
    ),
    "landing/docs/site/**": ("apps/api/tests/test_module_guides.py",),
}

# Paths that change the CI contract itself -> full suite.
# The host side of the worker bridge and everything a ctx.* RPC lands in. The
# cross-runtime conformance suite (test_worker_conformance.py) runs its JVM cases
# only in the jvm lane, so a change here must select it: on 19 Nov 2026 a change
# to event_log_access.py broke every JVM precompute and no lane ran the JVM side.
BRIDGE_HOST_PATHS: tuple[str, ...] = (
    "apps/api/src/mate/api/modules/ctx_rpc.py",
    "apps/api/src/mate/api/modules/subprocess_host.py",
    "apps/api/src/mate/api/modules/subprocess_worker.py",
    "apps/api/src/mate/api/modules/job_worker.py",
    "apps/api/src/mate/api/modules/event_log_access.py",
    "apps/api/src/mate/api/modules/object_centric_log_access.py",
    "apps/api/src/mate/api/modules/cache.py",
    "apps/api/src/mate/api/modules/runtimes/**",
    "apps/api/tests/test_worker_conformance.py",
    "apps/api/tests/fixtures/conformance_worker/**",
)

FULL_TRIGGERS: tuple[str, ...] = (
    ".github/workflows/ci.yml",
    "scripts/ci/**",
    "Makefile",
    "pyproject.toml",
    ".python-version",
)

# Paths with no executable meaning for any lane (pure prose / editor / agent config).
NO_LANE: tuple[str, ...] = (
    "docs/**",
    "papers/**",
    "LICENSE",
    "PRODUCT.md",
    "README.md",
    "AGENT.md",
    "CLAUDE.md",
    ".claude/**",
    ".agents/**",
    ".github/skills/**",
    ".vscode/**",
    ".gitignore",
    ".dockerignore",
    # Deployed by .github/workflows/pages.yml, no test reads it (except site/, declared above).
    "landing/**",
    ".github/workflows/pages.yml",
    "simulated_data/**",
)


def _match(path: str, patterns: Iterable[str]) -> bool:
    for pat in patterns:
        if pat.endswith("/**"):
            if path.startswith(pat[:-2]):
                return True
        elif fnmatch.fnmatchcase(path, pat):
            return True
    return False


def module_ids_with_tests(root: Path = REPO) -> list[str]:
    return sorted(p.parent.name for p in (root / "modules").glob("*/tests") if p.is_dir())


@dataclass
class Plan:
    full: bool = False
    lanes: dict[str, set[str]] = field(default_factory=dict)  # lane -> reasons
    modules: dict[str, set[str]] = field(default_factory=dict)  # module id -> reasons
    unknown: list[str] = field(default_factory=list)
    files: list[str] = field(default_factory=list)
    api_all: bool = False  # the API lane runs the whole suite
    api_tests: set[str] = field(default_factory=set)  # else: only these files

    def add(self, lane: str, reason: str) -> None:
        self.lanes.setdefault(lane, set()).add(reason)
        if lane == "api":
            self.api_all = True

    def add_api_tests(self, tests: Iterable[str], reason: str) -> None:
        self.lanes.setdefault("api", set()).add(reason)
        self.api_tests.update(tests)

    @property
    def api_targets(self) -> list[str]:
        """Empty list = whole suite."""
        return [] if self.api_all else sorted(self.api_tests)

    def add_module(self, mod: str, reason: str) -> None:
        self.modules.setdefault(mod, set()).add(reason)
        self.add("modules", reason)

    def make_full(self, reason: str, all_modules: Sequence[str]) -> None:
        self.full = True
        for lane in LANES:
            self.add(lane, reason)
        for m in all_modules:
            self.add_module(m, reason)

    def to_json(self) -> dict[str, object]:
        return {
            "full": self.full,
            "lanes": {k: sorted(v) for k, v in sorted(self.lanes.items())},
            "modules": {k: sorted(v) for k, v in sorted(self.modules.items())},
            "api_targets": self.api_targets or "all",
            "unknown_paths": self.unknown,
            "changed_files": len(self.files),
        }


def classify(path: str, plan: Plan, all_modules: Sequence[str]) -> None:
    """Add the lanes one changed path selects. Unrecognised -> full suite."""
    p = path

    if _match(p, FULL_TRIGGERS):
        plan.make_full(f"ci-contract:{p}", all_modules)
        return

    # Python dependency lock: every Python lane, not the web.
    if p == "uv.lock":
        for lane in ("static", "api"):
            plan.add(lane, "python-lock")
        for m in all_modules:
            plan.add_module(m, "python-lock")
        return

    # JS dependency lock / workspace: web lane + the API suite (bundle parity tests).
    if p in ("pnpm-lock.yaml", "pnpm-workspace.yaml", "package.json"):
        plan.add("web", "js-lock")
        return

    hit = False

    for pattern, tests in API_NARROW_INPUTS.items():
        if _match(p, [pattern]) and not p.endswith(".py"):
            plan.add_api_tests(tests, "api-narrow-input")
            if p.startswith("modules/"):
                plan.add("static", "module-guide")
            return

    if _match(p, API_DECLARED_INPUTS):
        plan.add("api", "api-declared-input")
        hit = True

    if p.endswith(".py"):
        plan.add("static", "python-source")
        hit = True

    if _match(p, BRIDGE_HOST_PATHS):
        plan.add("jvm", "bridge-host")

    if p.startswith("apps/api/"):
        plan.add("api", "api-owner")
        plan.add("static", "api-owner")
        return

    if p.startswith("packages/module-sdk-py/"):
        # Every module and the platform are built on the SDK.
        for lane in ("static", "api"):
            plan.add(lane, "sdk-py")
        for m in all_modules:
            plan.add_module(m, "sdk-py")
        return

    if p.startswith("modules/"):
        parts = p.split("/")
        mod = parts[1] if len(parts) > 2 else None
        # API suite loads every bundled manifest/guide (test_manifest_*, test_module_guides).
        plan.add("api", "module-manifest-consumer")
        plan.add("static", "module-validate")
        if mod and mod in all_modules:
            plan.add_module(mod, "module-owner")
        if (
            p.endswith((".ts", ".tsx", ".js", ".mjs", ".css", ".json"))
            or "/panel/" in p
            or "/widgets/" in p
        ):
            plan.add("web", "module-frontend")
        if mod == "performance_java":
            plan.add("jvm", "jvm-module")
        return

    if p.startswith("apps/web/"):
        plan.add("web", "web-owner")
        if p in ("apps/web/lib/runtime-externals.json", "apps/web/scripts/bundle-modules.mjs"):
            # Panel bundles of every module depend on the externals list.
            plan.add("web", "module-bundle")
        return

    if p.startswith(("packages/module-sdk-ts/", "packages/shared-types/")):
        plan.add("web", "ts-package")
        return

    if p.startswith("packages/module-sdk-jvm/"):
        plan.add("jvm", "jvm-sdk")
        return

    # Ops files the API suite reads (compose, .env.example, keycloak realm/theme,
    # lite scripts) - no dedicated lane, so run the API suite.
    if (
        p.startswith(("infra/", "scripts/"))
        or fnmatch.fnmatchcase(p, "docker-compose*.yml")
        or fnmatch.fnmatchcase(p, "compose*.yml")
        or p in (".env.example",)
    ):
        plan.add("api", "ops-input")
        return

    if hit:
        return

    if _match(p, NO_LANE) or (p.endswith(".md") and not p.startswith("modules/")):
        return

    plan.unknown.append(p)
    plan.make_full(f"unknown-path:{p}", all_modules)


def plan_for(files: Sequence[str], all_modules: Sequence[str] | None = None) -> Plan:
    mods = list(all_modules) if all_modules is not None else module_ids_with_tests()
    plan = Plan(files=list(files))
    for f in files:
        classify(f, plan, mods)
    return plan


def changed_files(base: str, head: str) -> list[str]:
    """Paths touched between merge-base(base, head) and head, both sides of renames."""
    mb = subprocess.run(["git", "merge-base", base, head], capture_output=True, text=True, cwd=REPO)
    if mb.returncode != 0:
        raise RuntimeError(f"no merge base between {base} and {head}: {mb.stderr.strip()}")
    out = subprocess.run(
        ["git", "diff", "--name-status", "-M", mb.stdout.strip(), head],
        capture_output=True,
        text=True,
        check=True,
        cwd=REPO,
    ).stdout
    files: list[str] = []
    for line in out.splitlines():
        cols = line.split("\t")
        files.extend(cols[1:])  # rename/copy rows carry old and new path
    return sorted(set(files))


def write_github_output(plan: Plan, dest: str) -> None:
    with open(dest, "a", encoding="utf-8") as fh:
        fh.write(f"full={'true' if plan.full else 'false'}\n")
        for lane in LANES:
            fh.write(f"run_{lane}={'true' if lane in plan.lanes else 'false'}\n")
        fh.write(f"modules_json={json.dumps(sorted(plan.modules))}\n")
        fh.write(f"api_targets={' '.join(plan.api_targets)}\n")
        fh.write(f"plan_json={json.dumps(plan.to_json(), separators=(',', ':'))}\n")


def summary_markdown(plan: Plan) -> str:
    rows = ["| lane | runs | reasons |", "|---|---|---|"]
    for lane in LANES:
        reasons = plan.lanes.get(lane)
        rows.append(
            f"| `{lane}` | {'yes' if reasons else 'skip'} | "
            f"{', '.join(sorted(reasons)[:6]) + (' …' if reasons and len(reasons) > 6 else '') if reasons else '-'} |"
        )
    head = (
        f"### CI plan ({'FULL' if plan.full else 'selected'}, {len(plan.files)} changed files)\n\n"
    )
    mods = ", ".join(f"`{m}`" for m in sorted(plan.modules)) or "-"
    if "api" in plan.lanes and plan.api_targets:
        mods += "\n\nAPI tests (narrowed): " + ", ".join(f"`{t}`" for t in plan.api_targets)
    unknown = (
        "\n\nUnknown paths (forced full): " + ", ".join(f"`{u}`" for u in plan.unknown)
        if plan.unknown
        else ""
    )
    return head + "\n".join(rows) + f"\n\nModule test suites: {mods}{unknown}\n"


def main(argv: Sequence[str] | None = None) -> int:
    ap = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    ap.add_argument("--base", help="diff base ref (merge-base with --head is used)")
    ap.add_argument("--head", default="HEAD")
    ap.add_argument("--files", nargs="*", help="plan for these paths instead of a git diff")
    ap.add_argument("--full", action="store_true", help="force the full suite")
    ap.add_argument("--json", action="store_true", help="print the plan as JSON")
    ap.add_argument("--github-output", help="append key=value outputs to this file")
    ap.add_argument("--summary", help="append a markdown summary to this file")
    args = ap.parse_args(argv)

    mods = module_ids_with_tests()
    if args.full or os.environ.get("MATE_CI_SELECTION") == "full":
        plan = Plan()
        plan.make_full("forced-full", mods)
    elif args.files is not None:
        plan = plan_for(args.files, mods)
    elif args.base:
        try:
            plan = plan_for(changed_files(args.base, args.head), mods)
        except (RuntimeError, subprocess.CalledProcessError) as exc:
            # Cannot compute a trustworthy diff -> widen, never skip.
            print(f"plan: {exc}; falling back to the full suite", file=sys.stderr)
            plan = Plan()
            plan.make_full("diff-unavailable", mods)
    else:
        ap.error("pass --base, --files or --full")

    if args.github_output:
        write_github_output(plan, args.github_output)
    if args.summary:
        with open(args.summary, "a", encoding="utf-8") as fh:
            fh.write(summary_markdown(plan))
    if args.json:
        print(json.dumps(plan.to_json(), indent=2))
    elif not args.github_output:
        print(summary_markdown(plan))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
