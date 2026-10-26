"""The compulsory per-module logging contract.

Every module must write to ``ctx.logger``: it is the only thing that feeds the
*Logs* panel on the module's settings page (``print()`` and a module-level
``logging`` / ``structlog`` logger go to the server's stdout, where the module's
user never sees them). Without it a module that misbehaves is a black box: the
user sees an empty panel and the author gets a bug report that says "it doesn't
work".

The contract, checked statically (no import, no execution)::

    - every ``@job``, ``@on_event`` and mutating ``@route.post/put/patch/delete``
      handler reaches a ``ctx.logger`` call;
    - and so does at least one handler of every module (a module made only of
      ``@route.get`` handlers still logs when it computes, degrades or fails).

"Reaches" means the handler body, or any function it references by name anywhere
in the module's own Python files, contains a ``<expr>.logger.<level>(...)`` call
(or a call on a name bound to ``ctx.logger`` / ``ctx.logger.bind(...)``). The
analysis is by name, so it can be lenient about which same-named function is
meant; it is never stricter than the code. It cannot see inside a JVM module's
jar, so ``runtime: jvm`` modules are exempt here (their SDK has ``ctx.logger()``;
see ``modules/README.md`` §13).

``check_logging`` returns *every* problem in one pass; ``require_logging`` raises
``ModuleManifestError`` listing them. ``python -m mate.sdk.logcheck <module dir |
modules root>...`` runs the same check from a shell or CI and exits ``1`` when any
module fails.

Deliberately **not** re-exported from ``mate.sdk`` (same reason as
``mate.sdk.guide``: ``runpy`` warns when ``-m`` runs an already-imported module).
"""

from __future__ import annotations

import ast
import sys
from collections.abc import Iterator, Sequence
from dataclasses import dataclass, field
from pathlib import Path

from mate.sdk.errors import ModuleManifestError
from mate.sdk.guide import module_dirs
from mate.sdk.manifest import Manifest

LOG_METHODS = frozenset({"debug", "info", "warning", "warn", "error", "critical", "exception"})
MUTATING_VERBS = frozenset({"post", "put", "patch", "delete"})

# Folders that hold someone else's code or build output, never the module's own.
_SKIP_DIRS = frozenset(
    {"tests", "test", "node_modules", "build", "dist", "site-packages", "__pycache__"}
)


_KIND_RANK = {"job": 3, "event": 2, "mutating route": 1, "route": 0}


@dataclass
class _Func:
    name: str
    where: str  # "module.py:42"
    logs: bool = False
    # Every name and attribute the body mentions - a superset of what it calls,
    # which also covers `asyncio.to_thread(self._compute, ctx)`.
    refs: set[str] = field(default_factory=lambda: set[str]())


@dataclass
class _Handler:
    func: _Func
    kind: str  # "job" | "event" | "mutating route" | "route"
    required: bool
    reaches_log: bool = False


def _dotted(node: ast.expr) -> str:
    parts: list[str] = []
    while isinstance(node, ast.Attribute):
        parts.append(node.attr)
        node = node.value
    if isinstance(node, ast.Name):
        parts.append(node.id)
    return ".".join(reversed(parts))


def _handler_kind(func: ast.FunctionDef | ast.AsyncFunctionDef) -> tuple[str, bool] | None:
    """``(kind, required)`` for a function decorated with an SDK handler decorator."""
    kinds: list[tuple[str, bool]] = []
    for deco in func.decorator_list:
        name = _dotted(deco.func if isinstance(deco, ast.Call) else deco)
        if name == "job":
            kinds.append(("job", True))
        elif name == "on_event":
            kinds.append(("event", True))
        elif name.startswith("route."):
            verb = name.split(".", 1)[1]
            kinds.append(("mutating route", True) if verb in MUTATING_VERBS else ("route", False))
    if not kinds:
        return None
    # A `@route.get` / `@route.post` stacked with `@job` is a job: name the decorator
    # that says the most about what the handler is.
    return max(kinds, key=lambda k: _KIND_RANK[k[0]])


def _is_logger_expr(node: ast.expr, aliases: set[str]) -> bool:
    """``<expr>.logger``, ``<expr>.logger.bind(...)`` or a name bound to either."""
    if isinstance(node, ast.Attribute):
        return node.attr == "logger"
    if isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute):
        return node.func.attr == "bind" and _is_logger_expr(node.func.value, aliases)
    if isinstance(node, ast.Name):
        return node.id in aliases
    return False


def _analyse(func: ast.FunctionDef | ast.AsyncFunctionDef, where: str) -> _Func:
    out = _Func(name=func.name, where=where)
    nodes = list(ast.walk(func))
    aliases: set[str] = set()
    for node in nodes:  # `log = ctx.logger` / `log = ctx.logger.bind(...)`
        if isinstance(node, ast.Assign) and _is_logger_expr(node.value, aliases):
            aliases.update(t.id for t in node.targets if isinstance(t, ast.Name))
        elif (
            isinstance(node, ast.AnnAssign)
            and node.value is not None
            and isinstance(node.target, ast.Name)
            and _is_logger_expr(node.value, aliases)
        ):
            aliases.add(node.target.id)
    for node in nodes:
        if isinstance(node, ast.Name):
            out.refs.add(node.id)
        elif isinstance(node, ast.Attribute):
            out.refs.add(node.attr)
        if (
            isinstance(node, ast.Call)
            and isinstance(node.func, ast.Attribute)
            and node.func.attr in LOG_METHODS
            and _is_logger_expr(node.func.value, aliases)
        ):
            out.logs = True
    return out


def _python_files(folder: Path) -> Iterator[Path]:
    for path in sorted(folder.rglob("*.py")):
        parts = path.relative_to(folder).parts[:-1]
        if any(p in _SKIP_DIRS or p.startswith(".") for p in parts):
            continue
        yield path


def check_logging(folder: Path, manifest: Manifest) -> list[str]:
    """Return every way the module breaks the logging contract (empty = compliant)."""
    if manifest.runtime.kind != "python":
        return []

    problems: list[str] = []
    funcs: dict[str, list[_Func]] = {}
    handlers: list[_Handler] = []
    for path in _python_files(folder):
        rel = path.relative_to(folder).as_posix()
        try:
            tree = ast.parse(path.read_text(encoding="utf-8"), filename=rel)
        except (SyntaxError, UnicodeDecodeError, ValueError) as exc:
            problems.append(f"{rel}: cannot be parsed ({exc}).")
            continue
        for node in ast.walk(tree):
            if not isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                continue
            func = _analyse(node, f"{rel}:{node.lineno}")
            funcs.setdefault(func.name, []).append(func)
            kind = _handler_kind(node)
            if kind is not None:
                handlers.append(_Handler(func, kind[0], kind[1]))

    def reaches_log(start: _Func) -> bool:
        seen: set[int] = set()
        stack = [start]
        while stack:
            cur = stack.pop()
            if id(cur) in seen:
                continue
            seen.add(id(cur))
            if cur.logs:
                return True
            for ref in cur.refs:
                stack.extend(funcs.get(ref, ()))
        return False

    for h in handlers:
        h.reaches_log = reaches_log(h.func)
        if h.required and not h.reaches_log:
            problems.append(
                f"{h.func.where}: {h.kind} handler {h.func.name}() never reaches a "
                "ctx.logger call - log what it does (ctx.logger.info) and why it fails "
                "(ctx.logger.exception)."
            )
    if (
        handlers
        and not any(h.reaches_log for h in handlers)
        and not any(h.required for h in handlers)
    ):
        problems.append(
            "no handler logs through ctx.logger - log when a route computes, degrades or "
            "fails (ctx.logger.info / .warning / .exception)."
        )
    return problems


def require_logging(folder: Path, manifest: Manifest) -> None:
    """Raise :class:`ModuleManifestError` listing every logging-contract problem."""
    problems = check_logging(folder, manifest)
    if problems:
        bullets = "\n".join(f"  - {p}" for p in problems)
        raise ModuleManifestError(
            "A module must log through ctx.logger so its Logs tab shows what it did "
            f"(see 'Logging' in modules/README.md):\n{bullets}"
        )


# ---------------------------------------------------------------------------
# CLI: python -m mate.sdk.logcheck <module dir | modules root>...
# ---------------------------------------------------------------------------


def main(argv: Sequence[str] | None = None) -> int:
    args = list(sys.argv[1:] if argv is None else argv)
    if not args or args[0] in {"-h", "--help"}:
        print(
            "usage: python -m mate.sdk.logcheck <module folder | modules root> [...]\n\n"
            "Checks each module logs through ctx.logger (see 'Logging' in modules/README.md).\n"
            "Exit status: 0 all compliant, 1 at least one problem, 2 nothing to check."
        )
        return 0 if args else 2

    checked = 0
    failed = 0
    for folder in module_dirs(args):
        checked += 1
        try:
            manifest = Manifest.load_yaml(folder / "manifest.yaml")
        except ModuleManifestError as exc:
            failed += 1
            print(f"FAIL {folder.name}\n  - manifest.yaml is invalid: {exc}")
            continue
        problems = check_logging(folder, manifest)
        if problems:
            failed += 1
            print(f"FAIL {folder.name}")
            for problem in problems:
                print(f"  - {problem}")
        else:
            print(f"ok   {folder.name}")

    if checked == 0:
        print("error: no module folders found", file=sys.stderr)
        return 2
    print(f"\n{checked - failed}/{checked} modules log through ctx.logger.")
    return 1 if failed else 0


if __name__ == "__main__":
    raise SystemExit(main())
