"""``mate-module`` - scaffold, check, try and pack a module from a shell.

    mate-module new my_module [--dir PATH]     # minimal in_process Python module
    mate-module validate [PATH]                # manifest.yaml, artifact contract, HOW_TO_USE.md
    mate-module run precompute [PATH] [--log FILE] [--param KEY=VALUE ...] [--input PORT=FILE ...]
    mate-module pack [PATH] [-o OUT]           # zip for POST /api/v1/modules/install

``run`` invokes one handler against ``mate.sdk.testing``'s fake context (the bundled
sample log unless ``--log`` names a CSV/Parquet log) and prints the result and the
cache as JSON. A route gets ``--param`` values as arguments, validated against each
parameter's annotation like the platform does; an event handler gets them in its
payload, parsed as JSON, else kept as strings. ``--input`` feeds a manifest ``inputs:``
port from a ``.json`` or ``.parquet`` file, as the producer would have cached it.

``validate`` checks the artifact contract (``mate.sdk.artifacts.check_contract``) against
the modules in the sibling folders: an input's ``from:`` module must be one of them, and a
``requirements.modules`` specifier (``discovery>=1,<2``) must accept the sibling's version.
A manifest ``version`` that isn't SemVer 2.0 fails the manifest itself.

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
import sys
import zipfile
from collections.abc import Callable, Sequence
from importlib import resources
from pathlib import Path
from string import Template
from typing import Any, get_type_hints

from pydantic import TypeAdapter, ValidationError
from pydantic.fields import FieldInfo
from pydantic_core import PydanticUndefined
from structlog.testing import capture_logs

from mate.sdk.artifacts import check_contract
from mate.sdk.decorators import get_event_sub, get_job_spec, get_route_spec
from mate.sdk.errors import ModuleManifestError
from mate.sdk.guide import check_guide
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

_ID_RE = re.compile(r"[a-z][a-z0-9_]*")

# Template (in mate/sdk/templates) -> path inside the new module folder.
_SCAFFOLD = {
    "manifest.yaml.tmpl": "manifest.yaml",
    "module.py.tmpl": "module.py",
    "HOW_TO_USE.md.tmpl": "HOW_TO_USE.md",
    # Named after the module so two modules' tests never clash on pytest's basename import.
    "test_module.py.tmpl": "tests/test_{id}.py",
}


def _new(args: argparse.Namespace) -> int:
    module_id: str = args.id
    if not _ID_RE.fullmatch(module_id):
        print(f"error: module id {module_id!r} must be lowercase snake_case.", file=sys.stderr)
        return 2
    target = Path(args.dir) / module_id
    if target.exists():
        print(f"error: {target} already exists.", file=sys.stderr)
        return 1
    words = module_id.split("_")
    fields = {
        "id": module_id,
        "name": " ".join(words).title(),
        "class_name": "".join(w.capitalize() for w in words) + "Module",
    }
    templates = resources.files("mate.sdk").joinpath("templates")
    for source, dest in _SCAFFOLD.items():
        out = target / dest.format(id=module_id)
        out.parent.mkdir(parents=True, exist_ok=True)
        text = templates.joinpath(source).read_text(encoding="utf-8")
        out.write_text(Template(text).substitute(fields), encoding="utf-8")
    print(f"created {target}\nnext: fill in the TODOs, then `mate-module validate {target}`.")
    return 0


def _check(folder: Path) -> Manifest | None:
    """Print the verdict for *folder*; return its manifest only when everything passes."""
    label = folder.resolve().name
    try:
        manifest = Manifest.load_yaml(folder / "manifest.yaml")
    except (ModuleManifestError, OSError) as exc:
        print(f"FAIL {label}\n  - manifest.yaml is invalid: {exc}")
        return None
    problems = _sibling_problems(folder, manifest) + check_guide(folder, manifest.name)
    if problems:
        print(f"FAIL {label}")
        for problem in problems:
            print(f"  - {problem}")
        return None
    print(f"ok   {label}")
    return manifest


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


def _validate(args: argparse.Namespace) -> int:
    return 0 if _check(Path(args.path)) else 1


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
    manifest = _check(folder)
    if manifest is None:
        return 1
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
        prog="mate-module", description="Scaffold, check, try and pack Mate modules."
    )
    sub = parser.add_subparsers(dest="command", required=True)

    new = sub.add_parser("new", help="scaffold a minimal in_process Python module")
    new.add_argument("id", help="module id, lowercase snake_case")
    new.add_argument("--dir", default=".", help="parent folder (default: current folder)")
    new.set_defaults(func=_new)

    validate = sub.add_parser(
        "validate", help="check manifest.yaml, its artifact contract and HOW_TO_USE.md"
    )
    validate.add_argument("path", nargs="?", default=".")
    validate.set_defaults(func=_validate)

    run = sub.add_parser("run", help="invoke one handler against a fake context")
    run.add_argument("handler", help="route or job method name, e.g. precompute")
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

    args = parser.parse_args(argv)
    return args.func(args)


if __name__ == "__main__":
    raise SystemExit(main())
