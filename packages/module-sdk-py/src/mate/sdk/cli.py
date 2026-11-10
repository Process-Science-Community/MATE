"""``mate-module``: scaffold, check and test modules from a shell.

    mate-module new my_module [--name "My module"] [--panel] [--dir modules]
    mate-module validate [PATH ...]      # default: every module under ./modules
    mate-module test my_module [--dir modules] [PYTEST_ARGS ...]

``validate`` checks ``manifest.yaml``, ``HOW_TO_USE.md`` (``mate.sdk.guide``) and, when
present, ``layout.json``: its schema, and that every card points at one of the module's
own manifest ``datasets`` / ``frontend.widgets``. Exit status 0 when all pass, else 1.
"""

from __future__ import annotations

import argparse
import json
import re
import subprocess
import sys
from collections.abc import Sequence
from importlib import resources
from pathlib import Path

from mate.sdk.errors import ModuleManifestError
from mate.sdk.guide import _module_dirs, check_guide  # pyright: ignore[reportPrivateUsage]
from mate.sdk.layout import LAYOUT_FILENAME, ModuleLayout
from mate.sdk.manifest import Manifest

# Stricter than the manifest's check (ASCII, starts with a letter) so the id is also a
# valid Python identifier part and folder name; every id it accepts passes the manifest.
_ID_RE = re.compile(r"[a-z][a-z0-9_]*")

_PANEL_FRONTEND = "\nfrontend:\n  panel: ./panel/index.tsx\n"


def _new(args: argparse.Namespace) -> int:
    module_id: str = args.id
    if not _ID_RE.fullmatch(module_id):
        print(
            f"error: module id {module_id!r} must be lowercase snake_case "
            "(a letter first, then letters, digits, underscores).",
            file=sys.stderr,
        )
        return 2
    target = Path(args.dir) / module_id
    if target.exists():
        print(f"error: {target} already exists; refusing to overwrite it.", file=sys.stderr)
        return 1
    words = module_id.split("_")
    name: str = args.name or " ".join(words).capitalize()
    values = {
        "__ID__": module_id,
        "__NAME_JSON__": json.dumps(name),  # a JSON string is valid YAML
        "__JOB_TITLE__": json.dumps(f"{name} - recount"),  # ... and a valid Python literal
        "__NAME__": name,
        "__CLASS__": "".join(w.capitalize() for w in words) + "Module",
        "__FRONTEND__": _PANEL_FRONTEND if args.panel else "",
    }
    files = {
        "manifest.yaml": "manifest.yaml.tmpl",
        "module.py": "module.py.tmpl",
        "HOW_TO_USE.md": "HOW_TO_USE.md.tmpl",
        LAYOUT_FILENAME: "layout.json.tmpl",
        # Named after the module so several modules' tests never clash on pytest's
        # basename import when run together.
        f"tests/test_{module_id}.py": "test_module.py.tmpl",
    }
    if args.panel:
        files["panel/index.tsx"] = "panel.tsx.tmpl"
    templates = resources.files("mate.sdk").joinpath("templates")
    for dest, source in files.items():
        text = templates.joinpath(source).read_text(encoding="utf-8")
        for key, value in values.items():
            text = text.replace(key, value)
        out = target / dest
        out.parent.mkdir(parents=True, exist_ok=True)
        out.write_text(text, encoding="utf-8")
    print(
        f"created {target}\n"
        f"next: fill in the TODOs, then `make module-test ID={module_id}` and "
        f"`make module-validate`."
    )
    return 0


def check_module(folder: Path) -> list[str]:
    """Every problem with the module in *folder* (empty = valid)."""
    try:
        manifest = Manifest.load_yaml(folder / "manifest.yaml")
    except (ModuleManifestError, OSError) as exc:
        return [f"manifest.yaml is invalid: {exc}"]
    return check_guide(folder, manifest.name) + _layout_problems(folder, manifest)


def _layout_problems(folder: Path, manifest: Manifest) -> list[str]:
    try:
        layout = ModuleLayout.load(folder)
    except (ValueError, OSError) as exc:  # bad JSON and pydantic errors are ValueErrors
        return [f"{LAYOUT_FILENAME} is invalid: {exc}"]
    if layout is None:
        return []
    datasets = {d.id for d in manifest.datasets}
    widgets = {w.id for w in manifest.frontend.widgets}
    problems: list[str] = []
    for item in layout.items:
        where = f"{LAYOUT_FILENAME}: card {item.i!r}"
        owner = item.dataset_ref.module_id if item.dataset_ref else item.module_id
        if owner not in (None, manifest.id):
            problems.append(f"{where} points at module {owner!r}; use this module's own data.")
        elif item.dataset_ref and item.dataset_ref.dataset_id not in datasets:
            problems.append(
                f"{where} uses dataset {item.dataset_ref.dataset_id!r}, which manifest "
                f"`datasets` does not declare (declared: {', '.join(sorted(datasets)) or 'none'})."
            )
        elif item.kind == "widget" and item.widget_id not in widgets:
            problems.append(
                f"{where} uses widget {item.widget_id!r}, which `frontend.widgets` does not "
                f"declare (declared: {', '.join(sorted(widgets)) or 'none'})."
            )
    return problems


def _validate(args: argparse.Namespace) -> int:
    failed = checked = 0
    for folder in _module_dirs(args.paths or ["modules"]):
        checked += 1
        problems = check_module(folder)
        if problems:
            failed += 1
            print(f"FAIL {folder.name}")
            for problem in problems:
                print(f"  - {problem}")
        else:
            print(f"ok   {folder.name}")
    if not checked:
        print("error: no module folder (with a manifest.yaml) found.", file=sys.stderr)
        return 1
    print(f"\n{checked - failed}/{checked} module(s) valid.")
    return 1 if failed else 0


def _test(args: argparse.Namespace) -> int:
    tests = Path(args.dir) / args.id / "tests"
    if not tests.is_dir():
        print(f"error: {tests} does not exist.", file=sys.stderr)
        return 1
    # No cache dir: a module with its own pyproject.toml becomes pytest's rootdir, and a
    # .pytest_cache written there would trigger hot reload.
    cmd = [sys.executable, "-m", "pytest", "-p", "no:cacheprovider", str(tests)]
    return subprocess.call([*cmd, *args.pytest_args])


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="mate-module", description="Scaffold, check and test Mate modules."
    )
    sub = parser.add_subparsers(dest="command", required=True)

    new = sub.add_parser("new", help="scaffold a minimal in_process Python module")
    new.add_argument("id", help="module id, lowercase snake_case")
    new.add_argument("--name", help="display name (default: from the id)")
    new.add_argument("--panel", action="store_true", help="also write a TSX panel")
    new.add_argument("--dir", default="modules", help="parent folder (default: modules)")
    new.set_defaults(func=_new)

    validate = sub.add_parser("validate", help="check manifest, HOW_TO_USE.md and layout.json")
    validate.add_argument("paths", nargs="*", help="module folders or roots (default: modules)")
    validate.set_defaults(func=_validate)

    test = sub.add_parser("test", help="run a module's tests with this Python (extra args: pytest)")
    test.add_argument("id")
    test.add_argument("--dir", default="modules", help="modules root (default: modules)")
    test.set_defaults(func=_test)

    # Unknown options after `test <id>` go to pytest (e.g. `-q`, `-k name`).
    args, extra = parser.parse_known_args(argv)
    if extra and args.command != "test":
        parser.error(f"unrecognized arguments: {' '.join(extra)}")
    args.pytest_args = extra
    return args.func(args)


if __name__ == "__main__":
    raise SystemExit(main())
