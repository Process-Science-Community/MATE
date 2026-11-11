"""``mate-module``: scaffold, check, test, try and pack modules from a shell.

    mate-module new my_module [--name "My module"] [--panel] [--dir modules]
    mate-module validate [PATH ...]      # default: every module under ./modules
    mate-module list [PATH ...]          # id, version, name, runtime, UI, folder
    mate-module test my_module [--dir modules] [PYTEST_ARGS ...]
    mate-module widget my_module my-widget [--dir modules] [--title T] [--dataset D]
    mate-module run HANDLER [PATH] [--log FILE] [--param KEY=VALUE ...] [--input PORT=FILE ...]
    mate-module pack [PATH] [-o OUT]     # zip for POST /api/v1/modules/install

``widget`` writes ``widgets/MyWidget.tsx`` and adds its ``frontend.widgets`` entry to
``manifest.yaml`` as text (comments kept); on any failure the manifest is restored.

``validate`` checks ``manifest.yaml`` (incl. that widget entry files exist),
``HOW_TO_USE.md`` (``mate.sdk.guide``) and, when present, ``layout.json``: its schema,
that every card points at one of the module's own manifest ``datasets`` /
``frontend.widgets`` (a viz card without data is a problem), that ``controls`` keys are declared
(``params_schema`` / ``config_schema``) and that ``action`` routes exist in the source.
It also checks the artifact contract (``mate.sdk.artifacts.check_contract``) against the
modules in the sibling folders: an input's ``from:`` module must be one of them, and a
``requirements.modules`` specifier (``discovery>=1,<2``) must accept the sibling's version.
Exit status 0 when all pass, else 1.

``run`` invokes one handler against ``mate.sdk.testing``'s fake context (the bundled
sample log unless ``--log`` names a CSV/Parquet log) and prints the result and the
cache as JSON. A route gets ``--param`` values as arguments, validated against each
parameter's annotation like the platform does; an event handler gets them in its
payload, parsed as JSON, else kept as strings. ``--input`` feeds a manifest ``inputs:``
port from a ``.json`` or ``.parquet`` file, as the producer would have cached it.

``pack`` keeps ``.dist/``: the platform serves an uploaded module's frontend from the
bundles in the archive and never builds them itself.
"""

from __future__ import annotations

import argparse
import asyncio
import contextlib
import importlib
import inspect
import json
import os
import re
import subprocess
import sys
import zipfile
from collections.abc import Callable, Sequence
from importlib import resources
from pathlib import Path
from typing import Any, get_type_hints

from pydantic import TypeAdapter, ValidationError
from pydantic.fields import FieldInfo
from pydantic_core import PydanticUndefined
from structlog.testing import capture_logs

from mate.sdk.artifacts import check_contract
from mate.sdk.decorators import get_event_sub, get_job_spec, get_route_spec
from mate.sdk.errors import ModuleManifestError
from mate.sdk.guide import _module_dirs, check_guide  # pyright: ignore[reportPrivateUsage]
from mate.sdk.layout import LAYOUT_FILENAME, ModuleLayout
from mate.sdk.manifest import Manifest
from mate.sdk.testing import MemoryCache, build_test_context, load_log, load_module, sample_log

# BUILD_ARTIFACT_NAMES in apps/api/src/mate/api/storage/module_archive.py minus `.dist`
# (an upload is never bundled server-side, so the archive must carry it), plus tool caches.
PACK_EXCLUDE = {
    ".venv",
    "node_modules",
    ".installed-hash",
    "__pycache__",
    ".git",
    ".gradle",
    ".pytest_cache",
    ".ruff_cache",
    ".mypy_cache",
    ".DS_Store",
}

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
        f"next: fill in the TODOs, then `make test ID={module_id}` and "
        f"`make validate`."
    )
    return 0


_WIDGET_ID_RE = re.compile(r"[a-z][a-z0-9-]*")


def _widget_entry_yaml(manifest: Manifest, widget_id: str, file: str, title: str) -> list[str]:
    """The manifest list item (unindented lines) for a new widget."""
    lines = [
        f"- id: {widget_id}",
        f"  entry: ./widgets/{file}",
        f"  title: {json.dumps(title)}",
        "  description: TODO - one line on what this card shows.",
        "  icon: BarChart3",
        "  default_w: 4",
        "  default_h: 8",
        "  min_px_w: 280",
        "  min_px_h: 200",
    ]
    if manifest.requirements.event_log.log_model == "object_centric":
        lines.append("  log_models: [object_centric]")
    return lines


def _indent(line: str) -> int:
    return len(line) - len(line.lstrip(" "))


def _is_content(line: str) -> bool:
    s = line.strip()
    return bool(s) and not s.startswith("#")


def _insert_widget(text: str, item: list[str]) -> str:
    """*text* with *item* appended to ``frontend.widgets``, edited as text so comments and
    key order survive. Handles: no ``frontend:``, ``frontend:`` without ``widgets:``,
    and an existing ``widgets:`` list (any key order)."""
    lines = text.splitlines()
    top = re.compile(r"frontend:[ \t]*(#.*)?")
    f = next((i for i, ln in enumerate(lines) if top.fullmatch(ln)), None)
    if f is None and any(re.match(r"frontend:[ \t]*[^ \t#]", ln) for ln in lines):
        # Flow style (`frontend: {...}`): appending a block would duplicate the key and
        # YAML keeps the last one, silently dropping the original frontend.
        raise ModuleManifestError("`frontend:` is not a block mapping; edit it by hand")
    if f is None:
        while lines and not lines[-1].strip():
            lines.pop()
        block = ["", "frontend:", "  widgets:", *(f"    {ln}" for ln in item)]
        return "\n".join(lines + block) + "\n"
    end = next(
        (i for i in range(f + 1, len(lines)) if _is_content(lines[i]) and _indent(lines[i]) == 0),
        len(lines),
    )
    children = [i for i in range(f + 1, end) if _is_content(lines[i])]
    child = _indent(lines[children[0]]) if children else 2
    key = re.compile(rf" {{{child}}}widgets:[ \t]*(#.*)?")
    w = next((i for i in children if key.fullmatch(lines[i])), None)
    if w is None:
        block = [f"{' ' * child}widgets:", *(f"{' ' * (child + 2)}{ln}" for ln in item)]
        return "\n".join(lines[: f + 1] + block + lines[f + 1 :]) + "\n"
    last, item_indent = w, child + 2
    for i in range(w + 1, end):
        if not _is_content(lines[i]):
            continue
        ind, s = _indent(lines[i]), lines[i].strip()
        if last == w:  # first entry: YAML allows `- ` at the key's own indent
            item_indent = ind if s.startswith("-") else child + 2
        if ind > child or (ind == child and s.startswith("-")):
            last = i
        else:
            break
    block = [f"{' ' * item_indent}{ln}" for ln in item]
    return "\n".join(lines[: last + 1] + block + lines[last + 1 :]) + "\n"


def _widget(args: argparse.Namespace) -> int:
    widget_id: str = args.widget_id
    if not _WIDGET_ID_RE.fullmatch(widget_id):
        print(
            f"error: widget id {widget_id!r} must be lowercase kebab-case "
            "(a letter first, then letters, digits, dashes).",
            file=sys.stderr,
        )
        return 2
    folder = Path(args.dir) / args.module_id
    path = folder / "manifest.yaml"
    try:
        manifest = Manifest.load_yaml(path)
    except (ModuleManifestError, OSError) as exc:
        print(f"error: cannot load {path}: {exc}", file=sys.stderr)
        return 1
    if any(w.id == widget_id for w in manifest.frontend.widgets):
        print(f"error: {path} already declares widget {widget_id!r}.", file=sys.stderr)
        return 1
    component = "".join(p.capitalize() for p in widget_id.split("-"))
    tsx = folder / "widgets" / f"{component}.tsx"
    if tsx.exists():
        print(f"error: {tsx} already exists; refusing to overwrite it.", file=sys.stderr)
        return 1
    datasets = {d.id: d for d in manifest.datasets}
    if args.dataset and args.dataset not in datasets:
        print(
            f"error: dataset {args.dataset!r} is not declared "
            f"(declared: {', '.join(datasets) or 'none'}).",
            file=sys.stderr,
        )
        return 1
    dataset = datasets.get(args.dataset) if args.dataset else next(iter(datasets.values()), None)
    title: str = args.title or " ".join(widget_id.split("-")).capitalize()
    route_note = (
        f"Reads the `{dataset.id}` dataset's route (manifest `datasets`)."
        if dataset and dataset.route
        else "TODO: point this at one of your module's @route.get paths."
    )
    values = {
        "__MODULE_ID__": args.module_id,
        "__WIDGET_ID__": widget_id,
        "__COMPONENT__": component,
        # A cache-only dataset has no route to read.
        "__ROUTE__": dataset.route if dataset and dataset.route else "/TODO",
        "__ROUTE_NOTE__": route_note,
        "__TITLE__": title,
    }
    text = resources.files("mate.sdk").joinpath("templates", "widget.tsx.tmpl").read_text("utf-8")
    for k, v in values.items():
        text = text.replace(k, v)

    item = _widget_entry_yaml(manifest, widget_id, tsx.name, title)
    original = path.read_text(encoding="utf-8")
    try:
        path.write_text(_insert_widget(original, item), encoding="utf-8")
        reloaded = Manifest.load_yaml(path)
        if not any(
            w.id == widget_id and w.entry == f"./widgets/{tsx.name}"
            for w in reloaded.frontend.widgets
        ):
            raise ModuleManifestError("the new entry did not land under frontend.widgets")
    except Exception as exc:  # restore whatever went wrong, the manifest is the user's file
        path.write_text(original, encoding="utf-8")
        snippet = "\n".join(f"    {ln}" for ln in item)
        print(
            f"error: could not add the widget to {path} ({exc}); it is unchanged.\n"
            f"Add this under `frontend:` -> `widgets:` yourself:\n\n{snippet}",
            file=sys.stderr,
        )
        return 1
    tsx.parent.mkdir(parents=True, exist_ok=True)
    tsx.write_text(text, encoding="utf-8")
    print(
        f"created {tsx}\nadded widget {widget_id!r} to {path}\n"
        "next: edit the TSX; in `make lite` it shows up in the Studio palette's module group "
        "within a few seconds (switch back to the tab or press Reload). "
        "Then `make validate`."
    )
    return 0


def check_module(folder: Path) -> list[str]:
    """Every problem with the module in *folder* (empty = valid)."""
    try:
        manifest = Manifest.load_yaml(folder / "manifest.yaml")
    except (ModuleManifestError, OSError) as exc:
        return [f"manifest.yaml is invalid: {exc}"]
    missing = [
        f"manifest.yaml: widget {w.id!r} {key} {rel!r} does not exist."
        for w in manifest.frontend.widgets
        for key, rel in (("entry", w.entry), ("settings_entry", w.settings_entry))
        if rel and not (folder / rel).is_file()
    ]
    return (
        check_guide(folder, manifest.name)
        + missing
        + _layout_problems(folder, manifest)
        + _sibling_problems(folder, manifest)
    )


def _sibling_problems(folder: Path, manifest: Manifest) -> list[str]:
    """*manifest*'s artifact-contract problems and unmet `requirements.modules`
    version specifiers, looked up among the modules in the sibling folders (a broken
    sibling manifest is skipped; a hard dep that isn't a sibling is the platform's
    to check at load)."""
    others: list[Manifest] = []
    for path in sorted(folder.resolve().parent.glob("*/manifest.yaml")):
        with contextlib.suppress(ModuleManifestError, OSError):
            other = Manifest.load_yaml(path)
            if other.id != manifest.id:
                others.append(other)
    prefix = f"{manifest.id}: "
    problems = [
        p.removeprefix(prefix) for p in check_contract([manifest, *others]) if p.startswith(prefix)
    ]
    versions = {m.id: m.version for m in others}
    for req in manifest.requirements.module_requirements:
        found = versions.get(req.id)
        if found is not None and not req.allows(found):
            problems.append(
                f"requirements.modules: {req} is not met by the sibling {req.id} {found}."
            )
    return problems


# shortcut: mirrors the first `accepts` shape of the web vizzes (lib/visualizations);
# unknown ids say "table". Upgrade to a generated list if vizzes keep growing.
_VIZ_SHAPE = {
    "kpi-grid": "kpi",
    "number-tile": "kpi",
    "gauge": "kpi",
    "radial-bar": "kpi",
    "process-map": "graph",
    "petri-net": "graph",
    "tree": "tree",
    "tree-diagram": "tree",
    "dfg-view": "graph",
    "petri-net-view": "graph",
    "process-tree-view": "tree",
    "bpmn-view": "blob",
}


def _layout_problems(folder: Path, manifest: Manifest) -> list[str]:
    try:
        layout = ModuleLayout.load(folder)
    except (ValueError, OSError) as exc:  # bad JSON and pydantic errors are ValueErrors
        return [f"{LAYOUT_FILENAME} is invalid: {exc}"]
    if layout is None:
        return []
    datasets = {d.id for d in manifest.datasets}
    widgets = {w.id for w in manifest.frontend.widgets}
    params = {k for d in manifest.datasets for k in _properties(d.params_schema)}
    config_keys = set(_properties(manifest.config_schema))
    problems: list[str] = []
    for item in layout.items:
        where = f"{LAYOUT_FILENAME}: card {item.i!r}"
        owner = item.dataset_ref.module_id if item.dataset_ref else item.module_id
        if owner not in (None, manifest.id):
            problems.append(f"{where} points at module {owner!r}; use this module's own data.")
        elif item.kind == "viz" and item.dataset_ref is None:
            shape = _VIZ_SHAPE.get(item.viz_id or "", "table")
            problems.append(
                f"{where} has no data yet; declare a {shape} dataset and bind it "
                f"(Studio: inspector -> Data)."
            )
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
        elif item.kind == "controls":
            config_source = item.config.get("source", "params") == "config"
            known = config_keys if config_source else params
            schema = "`config_schema`" if config_source else "a dataset's `params_schema`"
            keys: list[str] = item.config.get("keys", [])
            if not known:
                problems.append(f"{where} has nothing to control; declare {schema}.")
            problems += [
                f"{where} controls {key!r}, which {schema} does not declare "
                f"(declared: {', '.join(sorted(known))})."
                for key in keys
                if known and key not in known
            ]
        elif item.kind == "action":
            route: str = item.config["route"]
            # shortcut: static source scan, upgrade to importing the class if routes are
            # built dynamically. Only the module's own source (not .venv, tests) and only
            # the quoted literal count.
            literals = (f'"{route}"', f"'{route}'")
            if not any(
                any(lit in p.read_text(encoding="utf-8", errors="replace") for lit in literals)
                for p in folder.rglob("*.py")
                if not _SCAN_SKIP.intersection(p.relative_to(folder).parts[:-1])
            ):
                problems.append(
                    f"{where} runs route {route!r}, which no .py file in the module mentions; "
                    f"add `@route.post({route!r})`."
                )
    return problems


_SCAN_SKIP = {".venv", "venv", ".dist", "node_modules", "__pycache__", "tests"}


def _properties(schema: dict[str, Any] | None) -> dict[str, Any]:
    props = (schema or {}).get("properties")
    return props if isinstance(props, dict) else {}


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


def _count(n: int, noun: str) -> str:
    return f"{n} {noun}{'' if n == 1 else 's'}" if n else ""


def _list(args: argparse.Namespace) -> int:
    rows = [("ID", "VERSION", "NAME", "RUNTIME", "UI", "FOLDER")]
    for folder in _module_dirs(args.paths or ["modules"]):
        try:
            m = Manifest.load_yaml(folder / "manifest.yaml")
        except (ModuleManifestError, OSError):
            rows.append((folder.name, "?", "invalid manifest (see validate)", "", "", str(folder)))
            continue
        py = m.dependencies.python
        runtime = "jvm" if m.runtime.kind == "jvm" else f"{py.isolation}/{py.execution}"
        ui = [
            "panel" if m.frontend and m.frontend.panel else "",
            "layout" if (folder / LAYOUT_FILENAME).is_file() else "",
            _count(len(m.frontend.widgets) if m.frontend else 0, "widget"),
            _count(len(m.datasets), "dataset"),
        ]
        rows.append(
            (m.id, m.version, m.name, runtime, ", ".join(u for u in ui if u) or "-", str(folder))
        )
    if len(rows) == 1:
        print("error: no module folder (with a manifest.yaml) found.", file=sys.stderr)
        return 1
    widths = [max(len(r[i]) for r in rows) for i in range(len(rows[0]) - 1)]
    for r in rows:
        print("  ".join(c.ljust(w) for c, w in zip(r, widths, strict=False)) + "  " + r[-1])
    print(f"\n{len(rows) - 1} module(s).")
    return 0


def _test(args: argparse.Namespace) -> int:
    tests = Path(args.dir) / args.id / "tests"
    if not tests.is_dir():
        print(f"error: {tests} does not exist.", file=sys.stderr)
        return 1
    # No cache dir: a module with its own pyproject.toml becomes pytest's rootdir, and a
    # .pytest_cache written there would trigger hot reload.
    cmd = [sys.executable, "-m", "pytest", "-p", "no:cacheprovider", str(tests)]
    return subprocess.call([*cmd, *args.pytest_args])


class _UsageError(Exception):
    pass


def _param(text: str) -> tuple[str, str]:
    key, sep, raw = text.partition("=")
    if not key or not sep:
        raise argparse.ArgumentTypeError(f"expected KEY=VALUE, got {text!r}")
    return key, raw


def _json_or_text(raw: str) -> Any:
    try:
        return json.loads(raw)
    except json.JSONDecodeError:
        return raw


def _coerce(fn: Any, name: str, hints: dict[str, Any], raw: str) -> Any:
    """*raw* validated against the parameter's annotation, as the platform does
    (FastAPI coercion for query params, ``model_validate`` for a body model) - so a
    ``str`` param keeps ``"007"`` and a model param gets a model, not a dict.
    Unannotated: JSON, else the text."""
    hint = hints.get(name)
    if hint is None:
        return _json_or_text(raw)
    adapter: TypeAdapter[Any] = TypeAdapter(hint)
    try:
        return adapter.validate_python(raw)  # scalars: "5" -> 5, "true" -> True
    except ValidationError:
        try:
            return adapter.validate_json(raw)  # lists, dicts, models
        except ValidationError as exc:
            msg = exc.errors()[0]["msg"]
            raise _UsageError(f"handler {fn.__name__}: --param {name}: {msg}") from None


def _bind(fn: Any, params: dict[str, str]) -> Callable[[Any], Any]:
    """The call ``run`` makes with ``--param`` *params*: an event handler gets them in
    its payload, a route as arguments (falling back to each parameter's default)."""
    if get_event_sub(fn) is not None:
        payload = {k: _json_or_text(v) for k, v in params.items()}
        return lambda ctx: fn(ctx, {"log_id": ctx.log_id, "user_id": ctx.user_id, **payload})
    signature = list(inspect.signature(fn).parameters.values())[1:]
    try:
        hints = get_type_hints(fn)
    except Exception:  # an unresolvable annotation - pass values through untyped
        hints = {}
    kwargs: dict[str, Any] = {}
    for p in signature:
        if p.kind in (p.VAR_POSITIONAL, p.VAR_KEYWORD):
            continue
        default = p.default
        if isinstance(default, FieldInfo):  # FastAPI's Query(...) / Body(...)
            default = default.get_default(call_default_factory=True)
        if p.name in params:
            kwargs[p.name] = _coerce(fn, p.name, hints, params[p.name])
        elif default is p.empty or default is PydanticUndefined:
            raise _UsageError(f"handler {fn.__name__} needs --param {p.name}=...")
        else:
            kwargs[p.name] = default
    unknown = [k for k in params if k not in kwargs]
    if unknown and not any(p.kind is p.VAR_KEYWORD for p in signature):
        raise _UsageError(f"handler {fn.__name__} takes no parameter {', '.join(unknown)}.")
    kwargs.update({k: _json_or_text(v) for k, v in params.items() if k not in kwargs})
    return lambda ctx: fn(ctx, **kwargs)


def _load_inputs(manifest: Manifest, raw: dict[str, str]) -> dict[str, Any]:
    """``--input PORT=FILE`` values: a declared port's value read from JSON or Parquet."""
    ports = [p.id for p in manifest.inputs]
    values: dict[str, Any] = {}
    for port, file in raw.items():
        if port not in ports:
            raise _UsageError(
                f"{manifest.id} has no input port {port!r} (ports: {', '.join(ports) or 'none'})."
            )
        path = Path(file)
        if path.suffix == ".json":
            values[port] = json.loads(path.read_text(encoding="utf-8"))
        elif path.suffix == ".parquet":
            values[port] = importlib.import_module("pandas").read_parquet(path)
        else:
            raise _UsageError(f"--input {port}: {path.name} must be a .json or .parquet file.")
    return values


def _run(args: argparse.Namespace) -> int:
    module = load_module(Path(args.path))
    handlers = sorted(
        name
        for name in dir(module)
        if not name.startswith("_")
        and any(get(getattr(module, name)) for get in (get_route_spec, get_event_sub, get_job_spec))
    )
    if args.handler not in handlers:
        print(
            f"error: {module.id} has no handler {args.handler!r} "
            f"(handlers: {', '.join(handlers) or 'none'}).",
            file=sys.stderr,
        )
        return 2
    try:
        call = _bind(getattr(module, args.handler), dict(args.param))
        log = load_log(args.log) if args.log else sample_log()
        inputs = _load_inputs(
            Manifest.load_yaml(Path(args.path) / "manifest.yaml"), dict(args.input)
        )
    except (_UsageError, OSError, ValueError) as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2

    async def go() -> tuple[Any, MemoryCache]:
        ctx = await build_test_context(log, module_id=module.id, inputs=inputs)
        out = call(ctx)
        result = await out if inspect.isawaitable(out) else out
        assert isinstance(ctx.cache, MemoryCache)
        return result, ctx.cache

    # Captured so module log lines don't interleave with the JSON on stdout.
    with capture_logs() as logs:
        result, cache = asyncio.run(go())
    print(json.dumps({"result": result, "cache": cache.data, "logs": logs}, indent=2, default=str))
    return 0


def _bundles(manifest: Manifest) -> list[str]:
    """The ``.dist/`` files apps/web/scripts/bundle-modules.mjs builds for *manifest*."""
    fe = manifest.frontend
    names = ["panel.js"] if fe.panel else []
    for w in fe.widgets:
        names.append(f"widget-{w.id}.js")
        if w.settings_entry:
            names.append(f"widget-{w.id}-settings.js")
    return names


def _pack(args: argparse.Namespace) -> int:
    folder = Path(args.path)
    problems = check_module(folder)
    if problems:
        print(f"FAIL {folder.resolve().name}")
        for problem in problems:
            print(f"  - {problem}")
        return 1
    manifest = Manifest.load_yaml(folder / "manifest.yaml")
    missing = [n for n in _bundles(manifest) if not (folder / ".dist" / n).is_file()]
    if missing:
        print(
            f"error: .dist/ lacks {', '.join(missing)} - the platform serves an uploaded "
            "module's frontend from the archive and never bundles it. Build it from a Mate "
            "checkout (after `pnpm install`), then pack again:\n"
            f"  FF_MODULES_DIR={folder.resolve().parent} "
            f"node apps/web/scripts/bundle-modules.mjs {manifest.id}",
            file=sys.stderr,
        )
        return 1
    out = Path(args.output or f"{manifest.id}-{manifest.version}.zip").resolve()
    with zipfile.ZipFile(out, "w", zipfile.ZIP_DEFLATED) as zf:
        for root, dirs, files in os.walk(folder):
            dirs[:] = sorted(d for d in dirs if d not in PACK_EXCLUDE)
            for name in sorted(files):
                path = Path(root) / name
                rel = path.relative_to(folder)
                # Older archives left in the module folder (the default output lands
                # there when pack runs inside it) never ride along.
                stale_zip = len(rel.parts) == 1 and rel.suffix.lower() == ".zip"
                if name not in PACK_EXCLUDE and not stale_zip and path.resolve() != out:
                    zf.write(path, rel.as_posix())
    print(f"wrote {out}")
    return 0


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="mate-module", description="Scaffold, check, test, try and pack Mate modules."
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

    lst = sub.add_parser("list", help="list modules: id, version, runtime, UI")
    lst.add_argument("paths", nargs="*", help="module folders or roots (default: modules)")
    lst.set_defaults(func=_list)

    widget = sub.add_parser("widget", help="scaffold a TSX widget and add it to the manifest")
    widget.add_argument("module_id")
    widget.add_argument("widget_id", help="widget id, lowercase kebab-case")
    widget.add_argument("--dir", default="modules", help="modules root (default: modules)")
    widget.add_argument("--title", help="card title (default: from the id)")
    widget.add_argument("--dataset", help="dataset whose route it reads (default: the first)")
    widget.set_defaults(func=_widget)

    test = sub.add_parser("test", help="run a module's tests with this Python (extra args: pytest)")
    test.add_argument("id")
    test.add_argument("--dir", default="modules", help="modules root (default: modules)")
    test.set_defaults(func=_test)

    run = sub.add_parser("run", help="invoke one handler against a fake context")
    run.add_argument("handler", help="route or job method name, e.g. recount")
    run.add_argument("path", nargs="?", default=".")
    run.add_argument("--log", help="a .csv or .parquet log with canonical columns")
    run.add_argument(
        "--param",
        action="append",
        default=[],
        type=_param,
        metavar="KEY=VALUE",
        help="handler argument, VALUE parsed as JSON else a string (repeatable)",
    )
    run.add_argument(
        "--input",
        action="append",
        default=[],
        type=_param,
        metavar="PORT=FILE",
        help="feed an input port from a .json or .parquet file (repeatable)",
    )
    run.set_defaults(func=_run)

    pack = sub.add_parser("pack", help="zip the module for upload, with its .dist bundles")
    pack.add_argument("path", nargs="?", default=".")
    pack.add_argument("-o", "--output", help="archive path (default: <id>-<version>.zip)")
    pack.set_defaults(func=_pack)

    # Unknown options after `test <id>` go to pytest (e.g. `-q`, `-k name`).
    args, extra = parser.parse_known_args(argv)
    if extra and args.command != "test":
        parser.error(f"unrecognized arguments: {' '.join(extra)}")
    args.pytest_args = extra
    return args.func(args)


if __name__ == "__main__":
    raise SystemExit(main())
