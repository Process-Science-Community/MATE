"""Tests for the CI planner (scripts/ci/plan.py). Run: `make ci-plan-test`.

The planner decides which checks a PR skips, so it is guarded like product code:
each rule has a case here, unknown input must widen to the full suite, and the
API suite's out-of-tree file reads must be declared (the import graph cannot see
a test that reads `apps/web/lib/foo.ts` from disk).
"""

from __future__ import annotations

import fnmatch
import re
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent))

from plan import (
    API_DECLARED_INPUTS,
    API_NARROW_INPUTS,
    BRIDGE_HOST_PATHS,
    LANES,
    REPO,
    _match,
    plan_for,
)

MODS = ["discovery", "conformance", "pcomp"]


def lanes(*files: str) -> set[str]:
    return set(plan_for(list(files), MODS).lanes)


def test_docs_only_runs_nothing() -> None:
    assert lanes("docs/DEPLOY.md", "README.md", "papers/x.pdf", "landing/index.html") == set()


def test_unknown_path_forces_full() -> None:
    p = plan_for(["some/new/thing.toml"], MODS)
    assert p.full and set(p.lanes) == set(LANES) and set(p.modules) == set(MODS)
    assert p.unknown == ["some/new/thing.toml"]


@pytest.mark.parametrize(
    "path", [".github/workflows/ci.yml", "scripts/ci/plan.py", "Makefile", "pyproject.toml"]
)
def test_ci_contract_forces_full(path: str) -> None:
    assert plan_for([path], MODS).full


def test_api_change_runs_static_and_api_only() -> None:
    assert lanes("apps/api/src/mate/api/routes/admin.py") == {"static", "api"}


def test_sdk_change_runs_every_module_suite() -> None:
    p = plan_for(["packages/module-sdk-py/src/mate/sdk/context.py"], MODS)
    assert set(p.modules) == set(MODS) and {"static", "api", "modules"} <= set(p.lanes)


def test_module_change_selects_only_that_module() -> None:
    p = plan_for(["modules/discovery/module.py"], MODS)
    assert set(p.modules) == {"discovery"}
    assert {"static", "api", "modules"} == set(p.lanes)


def test_module_guide_change_runs_guide_checks_only() -> None:
    p = plan_for(["modules/pcomp/HOW_TO_USE.md"], MODS)
    assert set(p.lanes) == {"static", "api"} and not p.modules
    assert "apps/api/tests/test_module_guides.py" in p.api_targets


def test_narrowing_loses_to_a_full_api_selection() -> None:
    p = plan_for(["modules/pcomp/HOW_TO_USE.md", "apps/api/src/mate/api/main.py"], MODS)
    assert p.api_targets == []  # whole suite


def test_module_panel_runs_web() -> None:
    assert "web" in lanes("modules/discovery/panel/index.tsx")


def test_web_only_change_skips_api() -> None:
    assert lanes("apps/web/components/foo.tsx") == {"web"}


def test_web_file_read_by_api_tests_runs_api() -> None:
    assert lanes("apps/web/lib/runtime-externals.json") == {"web", "api"}


def test_landing_site_read_by_api_tests_runs_api() -> None:
    p = plan_for(["landing/docs/site/guides.json"], MODS)
    assert set(p.lanes) == {"api"} and p.api_targets == ["apps/api/tests/test_module_guides.py"]


def test_python_outside_owners_gets_lint() -> None:
    assert lanes("landing/docs/gen-endpoints.py") == {"static"}


def test_ops_inputs_run_api() -> None:
    assert lanes("docker-compose.yml") == {"api"}
    assert lanes(".env.example") == {"api"}
    assert lanes("infra/keycloak/realm.json") == {"api"}


def test_jvm_sdk_runs_jvm_only() -> None:
    assert lanes("packages/module-sdk-jvm/build.gradle.kts") == {"jvm"}


@pytest.mark.parametrize(
    "path",
    [
        # 7c9f31f changed this file alone and broke every JVM precompute; the
        # JVM conformance cases never ran because no rule selected the jvm lane.
        "apps/api/src/mate/api/modules/event_log_access.py",
        "apps/api/src/mate/api/modules/ctx_rpc.py",
        "apps/api/src/mate/api/modules/runtimes/jvm.py",
        "apps/api/tests/fixtures/conformance_worker/module.py",
    ],
)
def test_bridge_host_change_runs_jvm_conformance(path: str) -> None:
    assert {"api", "jvm"} <= lanes(path)


def test_bridge_host_paths_exist() -> None:
    for pat in BRIDGE_HOST_PATHS:
        base = pat[:-3] if pat.endswith("/**") else pat
        assert (REPO / base).exists(), f"stale bridge host path: {pat}"


def test_uv_lock_runs_python_not_web() -> None:
    p = plan_for(["uv.lock"], MODS)
    assert "web" not in p.lanes and set(p.modules) == set(MODS)


def test_plan_is_deterministic() -> None:
    files = ["modules/pcomp/a.py", "apps/web/x.tsx", "docs/a.md"]
    assert plan_for(files, MODS).to_json() == plan_for(list(reversed(files)), MODS).to_json()


# --- declared-input guard --------------------------------------------------------

_ROOT_ALIAS = re.compile(r"^\s*(\w+)\s*=\s*(.*parents\[3\].*)$")
_ALIAS_DEF = re.compile(r"^\s*(\w+)\s*=\s*(\w+)((?:\s*/\s*\"[^\"]+\")+)\s*$")
_LIT = re.compile(r"\"([^\"]+)\"")


def _out_of_tree_reads() -> set[str]:
    """Repo-relative prefixes the API tests build from the repo root."""
    return {path for path, _ in _reads_by_test()}


def _reads_by_test() -> set[tuple[str, str]]:
    """(repo-relative path or glob, reading test file) pairs."""
    found: set[tuple[str, str]] = set()
    for test in sorted((REPO / "apps/api/tests").glob("*.py")):
        src = test.read_text(encoding="utf-8")
        alias: dict[str, str] = {}
        usage_lines: list[str] = []
        for line in src.splitlines():
            m = _ROOT_ALIAS.match(line)
            if not m and not _ALIAS_DEF.match(line):
                usage_lines.append(line)
            if m:
                tail = m.group(2).split("parents[3]", 1)[1]
                segs = _LIT.findall(tail.split(".")[0] if "." in tail and "/" not in tail else tail)
                alias[m.group(1)] = "/".join(segs)
                continue
            m = _ALIAS_DEF.match(line)
            if m and m.group(2) in alias:
                alias[m.group(1)] = "/".join(
                    s for s in [alias[m.group(2)], *_LIT.findall(m.group(3))] if s
                )
        # Alias definitions are not reads; only uses of an alias are.
        usage = "\n".join(usage_lines)
        for name, base in alias.items():
            # Segments: "literal", f"..." or a variable; the last two become `*`.
            seg = r"(?:f?\"[^\"]+\"|[A-Za-z_]\w*)"
            pattern = rf"\b{name}\b((?:\s*/\s*{seg})*)\)?(?:\s*\.r?glob\(\"([^\"]+)\"\))?"
            for m in re.finditer(pattern, usage):
                segs = [
                    t[1:-1] if t.startswith('"') else "*"
                    for t in re.findall(r'f?"[^"]+"|[A-Za-z_]\w*', m.group(1))
                ]
                path = "/".join(s for s in [base, *segs] if s)
                if path:
                    found.add((path, str(test.relative_to(REPO))))
                    if m.group(2):  # a glob below the path: record the pattern too
                        found.add((f"{path}/{m.group(2)}", str(test.relative_to(REPO))))
    return found


# One concrete file per narrowed pattern, used to ask "does this read cover it?".
_NARROW_PROBES: dict[str, list[str]] = {
    "modules/*.md": [
        f"{p.parent.relative_to(REPO)}/HOW_TO_USE.md"
        for p in sorted((REPO / "modules").glob("*/manifest.yaml"))
    ],
    "landing/docs/site/**": ["landing/docs/site/guides.json"],
}


def _covers(read: str, probe: str, all_reads: set[str]) -> bool:
    if any(ch in read for ch in "*?["):
        return fnmatch.fnmatchcase(probe, read)
    if read == probe:
        return True
    # A bare directory read covers everything below it, unless the same test only
    # ever globs it for something narrower (recorded as a separate glob entry).
    return probe.startswith(read + "/") and not any(
        r.startswith(read + "/") and any(ch in r for ch in "*?[") for r in all_reads
    )


def test_narrowed_inputs_have_no_other_readers() -> None:
    assert set(_NARROW_PROBES) == set(API_NARROW_INPUTS)
    reads = _reads_by_test()
    by_test: dict[str, set[str]] = {}
    for path, test in reads:
        by_test.setdefault(test, set()).add(path)
    bad = []
    for pattern, probes in _NARROW_PROBES.items():
        allowed = set(API_NARROW_INPUTS[pattern])
        for test, paths in by_test.items():
            if test in allowed:
                continue
            for path in paths:
                if any(_covers(path, probe, paths) for probe in probes):
                    bad.append(f"{test} reads {path}")
    assert not bad, (
        "These API tests read inputs that API_NARROW_INPUTS sends to other tests only. "
        f"Add the test to the pattern's list in scripts/ci/plan.py: {bad}"
    )


def test_guard_finds_known_reads() -> None:
    reads = _out_of_tree_reads()
    assert "apps/web/lib/runtime-externals.json" in reads
    assert any(r.startswith("landing/docs/site") for r in reads)


def test_api_out_of_tree_reads_are_declared() -> None:
    missing = []
    for path in sorted(_out_of_tree_reads()):
        if path.startswith("apps/api"):
            continue
        # A prefix (directory) read: any file below it must select the API suite.
        probe = path if "." in path.rsplit("/", 1)[-1] else f"{path}/__probe__.txt"
        selected = lanes(probe)
        # JVM build outputs (conformance jars) only exist after `make jvm`; the jvm lane
        # builds them and runs test_worker_conformance.py itself.
        if "api" not in selected and not (
            path.startswith("packages/module-sdk-jvm/") and "jvm" in selected
        ):
            missing.append(path)
    assert not missing, (
        "apps/api/tests read these paths but a change to them would skip the API suite. "
        f"Add them to API_DECLARED_INPUTS in scripts/ci/plan.py: {missing}"
    )


def test_declared_inputs_exist() -> None:
    for pat in API_DECLARED_INPUTS:
        base = pat[:-3] if pat.endswith("/**") else pat
        assert (REPO / base).exists(), f"stale declared input: {pat}"
        assert _match(base if not pat.endswith("/**") else base + "/x", [pat])
