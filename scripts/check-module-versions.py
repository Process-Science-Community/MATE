"""Check that every changed module folder bumps its manifest version enough.

    uv run python scripts/check-module-versions.py [--base origin/main] [--repo .]

For each folder under ``modules/`` whose files differ between ``--base`` and the
working tree (untracked files included), compares a contract snapshot of the base
version (read with ``git show``) against the working tree's:

- major: something dependents or stored state rely on goes away or changes type - a
  ``@route`` (method + path, parsed statically from ``module.py``), a dataset or its
  ``artifact_type`` / ``cache_key``, an input port or its type, a ``provides`` /
  ``consumes`` topic, a ``config_schema`` property or its type, ``runtime.kind`` /
  ``isolation`` - or the module demands more: a new or changed hard dep, a new
  required column, a higher ``min_events`` / ``min_cases``, another ``log_model``, a
  new or newly required input port;
- minor: any of those added, or a demand relaxed;
- patch: anything else (code, docs, tests).

Fails when the version bump is lower than that, or files changed without one; only
``MAJOR.MINOR.PATCH`` counts, a prerelease/build-only change is no bump. Defaults are
filled in before comparing, so writing one out (``isolation: in_process``) changes
nothing. In ``0.x`` a minor bump may break (SemVer §4), so a major change needs a minor
bump there.
A new module or a deleted one is not checked. Exit 0 ok, 1 under-bumped, 2 usage error.
"""

from __future__ import annotations

import argparse
import ast
import re
import subprocess
import sys
from pathlib import Path, PurePosixPath
from typing import Any, cast

import yaml

LEVELS = ("none", "patch", "minor", "major")
_SEMVER = re.compile(r"(0|[1-9]\d*)\.(0|[1-9]\d*)\.(0|[1-9]\d*)(?:[-+].*)?")
_METHODS = {"get", "post", "put", "patch", "delete"}
_DEP_ID = re.compile(r"\s*([a-z0-9_]+)\s*(.*?)\s*")


def _git(repo: Path, *args: str) -> str:
    return subprocess.run(
        ["git", "-C", str(repo), *args], check=True, capture_output=True, text=True
    ).stdout


def _show(repo: Path, ref: str, path: str) -> str | None:
    proc = subprocess.run(
        ["git", "-C", str(repo), "show", f"{ref}:{path}"], capture_output=True, text=True
    )
    return proc.stdout if proc.returncode == 0 else None


def _read(path: Path) -> str | None:
    return path.read_text(encoding="utf-8") if path.is_file() else None


def changed_folders(repo: Path, base: str, modules: str) -> list[str]:
    """Module folder names with a file that differs from *base* or is untracked."""
    names = _git(repo, "diff", "--name-only", base, "--", modules).splitlines()
    names += _git(repo, "ls-files", "--others", "--exclude-standard", "--", modules).splitlines()
    parts = [PurePosixPath(n).relative_to(modules).parts for n in names]
    return sorted({p[0] for p in parts if len(p) > 1})


def routes(source: str | None) -> set[str]:
    """``METHOD /path`` of every ``@route.<method>(path)`` decorator in *source*."""
    if source is None:
        return set()
    out: set[str] = set()
    for node in ast.walk(ast.parse(source)):
        if not isinstance(node, ast.FunctionDef | ast.AsyncFunctionDef):
            continue
        for deco in node.decorator_list:
            if not (isinstance(deco, ast.Call) and isinstance(deco.func, ast.Attribute)):
                continue
            owner = deco.func.value  # `route` in `@route.get(...)`, or `sdk.route`
            name = (
                owner.id
                if isinstance(owner, ast.Name)
                else owner.attr
                if isinstance(owner, ast.Attribute)
                else None
            )
            if name != "route" or deco.func.attr not in _METHODS:
                continue
            path = (
                deco.args[0]
                if deco.args
                else next((k.value for k in deco.keywords if k.arg == "path"), None)
            )
            if isinstance(path, ast.Constant) and isinstance(path.value, str):
                out.add(f"{deco.func.attr.upper()} {path.value}")
    return out


def _dict(value: Any) -> dict[str, Any]:
    return cast(dict[str, Any], value) if isinstance(value, dict) else {}


def _list(value: Any) -> list[Any]:
    return cast(list[Any], value) if isinstance(value, list) else []


def snapshot(manifest_text: str, module_py: str | None) -> dict[str, Any]:
    """The contract of one module version (raw YAML: a base manifest need not validate
    under today's SDK)."""
    m = _dict(yaml.safe_load(manifest_text))
    reqs = _dict(m.get("requirements"))
    log = _dict(reqs.get("event_log"))
    python = _dict(_dict(m.get("dependencies")).get("python"))
    runtime = str(_dict(m.get("runtime")).get("kind") or "python")
    deps: dict[str, str] = {}
    for entry in _list(reqs.get("modules")):
        match = _DEP_ID.fullmatch(str(entry))
        if match:
            deps[match.group(1)] = match.group(2)
    props = _dict(_dict(m.get("config_schema")).get("properties"))
    return {
        "version": str(m.get("version") or ""),
        "routes": routes(module_py),
        "datasets": {
            str(d.get("id")): (d.get("artifact_type"), d.get("cache_key"))
            for d in map(_dict, _list(m.get("datasets")))
        },
        "inputs": {
            str(p.get("id")): (p.get("artifact_type"), bool(p.get("required")))
            for p in map(_dict, _list(m.get("inputs")))
        },
        "provides": {str(t) for t in _list(m.get("provides"))},
        "consumes": {str(t) for t in _list(m.get("consumes"))},
        "config": {str(k): str(_dict(v).get("type")) for k, v in props.items()},
        "deps": deps,
        "columns": {str(c) for c in _list(log.get("required_columns"))},
        "min_events": int(log.get("min_events") or 0),
        "min_cases": int(log.get("min_cases") or 0),
        "log_model": str(log.get("log_model") or "case_centric"),
        # Defaults filled in, so writing one out (or dropping it) changes nothing.
        "runtime": (
            runtime,
            "subprocess" if runtime != "python" else python.get("isolation") or "in_process",
        ),
    }


def classify(base: dict[str, Any], head: dict[str, Any]) -> list[tuple[str, str]]:
    """``(level, reason)`` for every contract difference, major first."""
    out: list[tuple[str, str]] = []

    for name in ("routes", "provides", "consumes"):
        old, new = cast(set[str], base[name]), cast(set[str], head[name])
        out += [("major", f"{name}: removed {x}") for x in sorted(old - new)]
        out += [("minor", f"{name}: added {x}") for x in sorted(new - old)]
    for name in ("datasets", "config"):
        old_map, new_map = cast(dict[str, Any], base[name]), cast(dict[str, Any], head[name])
        out += [("major", f"{name}: removed {k}") for k in sorted(old_map.keys() - new_map.keys())]
        out += [("minor", f"{name}: added {k}") for k in sorted(new_map.keys() - old_map.keys())]
    old_ds, new_ds = cast(dict[str, Any], base["datasets"]), cast(dict[str, Any], head["datasets"])
    for key in sorted(old_ds.keys() & new_ds.keys()):
        for field, o, n in zip(
            ("artifact_type", "cache_key"), old_ds[key], new_ds[key], strict=True
        ):
            if o != n:  # declaring one where there was none only adds
                out.append(
                    ("minor" if o is None else "major", f"datasets: {key} {field} {o} -> {n}")
                )
    old_cfg, new_cfg = cast(dict[str, str], base["config"]), cast(dict[str, str], head["config"])
    for key in sorted(old_cfg.keys() & new_cfg.keys()):
        if old_cfg[key] != new_cfg[key]:
            out.append(("major", f"config: {key} type {old_cfg[key]} -> {new_cfg[key]}"))
    old_in, new_in = cast(dict[str, Any], base["inputs"]), cast(dict[str, Any], head["inputs"])
    for port in sorted(old_in.keys() - new_in.keys()):
        out.append(("major", f"inputs: removed {port}"))
    for port in sorted(new_in.keys() - old_in.keys()):
        required = new_in[port][1]
        out.append(("major" if required else "minor", f"inputs: added {port}"))
    for port in sorted(old_in.keys() & new_in.keys()):
        (old_type, old_req), (new_type, new_req) = old_in[port], new_in[port]
        if old_type != new_type:
            out.append(("major", f"inputs: {port} retyped {old_type} -> {new_type}"))
        if old_req != new_req:
            out.append(("major" if new_req else "minor", f"inputs: {port} required={new_req}"))
    old_deps, new_deps = cast(dict[str, str], base["deps"]), cast(dict[str, str], head["deps"])
    for dep in sorted(new_deps.keys() - old_deps.keys()):
        out.append(("major", f"hard dep added: {dep}{new_deps[dep]}"))
    for dep in sorted(old_deps.keys() - new_deps.keys()):
        out.append(("minor", f"hard dep dropped: {dep}"))
    for dep in sorted(old_deps.keys() & new_deps.keys()):
        old_spec, new_spec = old_deps[dep], new_deps[dep]
        if old_spec != new_spec:
            # shortcut: any new specifier counts as tighter; compare ranges if it matters.
            level = "minor" if not new_spec else "major"
            out.append((level, f"hard dep {dep}: {old_spec or '*'} -> {new_spec or '*'}"))
    old_cols, new_cols = cast(set[str], base["columns"]), cast(set[str], head["columns"])
    out += [("major", f"required column added: {c}") for c in sorted(new_cols - old_cols)]
    out += [("minor", f"required column dropped: {c}") for c in sorted(old_cols - new_cols)]
    for name in ("min_events", "min_cases"):
        if head[name] != base[name]:
            level = "major" if head[name] > base[name] else "minor"
            out.append((level, f"{name}: {base[name]} -> {head[name]}"))
    for name in ("log_model", "runtime"):
        if head[name] != base[name]:
            out.append(("major", f"{name}: {base[name]} -> {head[name]}"))
    return sorted(out, key=lambda r: -LEVELS.index(r[0]))


def bump(old: str, new: str) -> str | None:
    """The level *new* bumps *old* by, or None when either is not SemVer or *new*'s
    core is lower. Only ``MAJOR.MINOR.PATCH`` counts: a prerelease/build-only change
    (``1.0.0`` -> ``1.0.0+b2`` or ``-rc.1``) is ``none``, so the new version always
    has strictly higher SemVer precedence."""
    a, b = _SEMVER.fullmatch(old), _SEMVER.fullmatch(new)
    if a is None or b is None:
        return None
    old_core = tuple(int(x) for x in a.groups())
    new_core = tuple(int(x) for x in b.groups())
    if new_core < old_core:
        return None
    for level, o, n in zip(("major", "minor", "patch"), old_core, new_core, strict=True):
        if n > o:
            return level
    return "none"


def check(repo: Path, base: str, modules: str = "modules") -> list[tuple[bool, str]]:
    """``(ok, report line)`` per changed module folder that exists on both sides."""
    results: list[tuple[bool, str]] = []
    for folder in changed_folders(repo, base, modules):
        old_manifest = _show(repo, base, f"{modules}/{folder}/manifest.yaml")
        new_manifest = _read(repo / modules / folder / "manifest.yaml")
        if old_manifest is None or new_manifest is None:
            continue  # a new or deleted module: nothing to compare against
        old = snapshot(old_manifest, _show(repo, base, f"{modules}/{folder}/module.py"))
        new = snapshot(new_manifest, _read(repo / modules / folder / "module.py"))
        changes = classify(old, new)
        needed = changes[0][0] if changes else "patch"
        if needed == "major" and old["version"].startswith("0."):
            needed = "minor"
        got = bump(old["version"], new["version"])
        ok = got is not None and LEVELS.index(got) >= LEVELS.index(needed)
        why = "; ".join(reason for _, reason in changes) or "files changed"
        verdict = "ok  " if ok else "FAIL"
        results.append(
            (
                ok,
                f"{verdict} {folder} {old['version']} -> {new['version']} (needs {needed}: {why})",
            )
        )
    return results


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=(__doc__ or "").split("\n\n", 1)[0])
    parser.add_argument("--base", default="origin/main", help="git ref to compare against")
    parser.add_argument("--repo", default=".", help="repository root (default: .)")
    parser.add_argument("--modules", default="modules", help="modules folder in the repo")
    args = parser.parse_args(argv)
    repo = Path(args.repo)
    try:
        _git(repo, "rev-parse", "--verify", f"{args.base}^{{commit}}")
    except subprocess.CalledProcessError:
        print(f"error: {args.base!r} is not a commit in {repo.resolve()}", file=sys.stderr)
        return 2
    results = check(repo, args.base, args.modules)
    for _, line in results:
        print(line)
    if not results:
        print(f"no changed module against {args.base}")
    return 0 if all(ok for ok, _ in results) else 1


if __name__ == "__main__":
    raise SystemExit(main())
