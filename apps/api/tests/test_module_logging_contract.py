"""The compulsory ``ctx.logger`` contract (`mate.sdk.logcheck`).

Two halves, like the how-to-use guide: the static checker (one case per rule, each
starting from a compliant module and breaking exactly one thing) and the platform's
policy around it - uploading a module that never logs is rejected, loading one that
is already on disk only warns, and every bundled module complies.
"""

from __future__ import annotations

import textwrap
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest
from httpx import AsyncClient
from structlog.testing import capture_logs

from mate.api.modules.logging_contract import warn_on_logging_problems
from mate.sdk.errors import ModuleManifestError
from mate.sdk.logcheck import check_logging, main, require_logging
from mate.sdk.manifest import Manifest
from tests.test_modules_per_user import _upload  # pyright: ignore[reportPrivateUsage]

_REPO = Path(__file__).resolve().parents[3]
_BUNDLED = sorted(p.parent for p in (_REPO / "modules").glob("*/manifest.yaml"))

_MANIFEST = (
    "id: m\nname: M\nversion: 0.0.1\ncategory: foundation\n"
    "requirements:\n  event_log:\n    required_columns: [case_id, activity, timestamp]\n"
    "provides: []\nconsumes: []\n"
    "dependencies:\n  python:\n    inherit: []\n    isolation: in_process\n"
)

_HEADER = "from mate.sdk import Module, ModuleContext, job, on_event, route\n\n\n"


def _module(
    root: Path, body: str, *, imports: str = "", files: dict[str, str] | None = None
) -> tuple[Path, Manifest]:
    """A throwaway module ``<root>/m`` whose ``module.py`` holds the given class body.

    ``files`` maps a folder-relative path to the text of an extra file.
    """
    folder = root / "m"
    folder.mkdir(parents=True)
    (folder / "manifest.yaml").write_text(_MANIFEST, encoding="utf-8")
    source = (
        imports
        + _HEADER
        + 'class M(Module):\n    id = "m"\n\n'
        + textwrap.indent(textwrap.dedent(body), "    ")
    )
    (folder / "module.py").write_text(source, encoding="utf-8")
    for rel, text in (files or {}).items():
        target = folder / rel
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(textwrap.dedent(text), encoding="utf-8")
    return folder, Manifest.load_yaml(folder / "manifest.yaml")


def _problems(
    tmp_path: Path, body: str, *, imports: str = "", files: dict[str, str] | None = None
) -> list[str]:
    folder, manifest = _module(tmp_path, body, imports=imports, files=files)
    return check_logging(folder, manifest)


# -- compliant shapes -----------------------------------------------------------


def test_a_logging_job_is_compliant(tmp_path: Path) -> None:
    body = """
        @route.post("/run")
        @job(progress=True)
        async def run(self, ctx: ModuleContext) -> None:
            ctx.logger.info("m.started")
    """
    assert _problems(tmp_path, body) == []


def test_a_get_only_module_needs_just_one_logging_handler(tmp_path: Path) -> None:
    body = """
        @route.get("/a")
        async def a(self, ctx: ModuleContext) -> dict:
            return {}

        @route.get("/b")
        async def b(self, ctx: ModuleContext) -> dict:
            ctx.logger.warning("m.degraded")
            return {}
    """
    assert _problems(tmp_path, body) == []


def test_logging_reached_through_a_helper_in_another_file_counts(tmp_path: Path) -> None:
    body = """
        @on_event("log.imported")
        @job()
        async def precompute(self, ctx: ModuleContext, payload: dict) -> None:
            await compute(ctx)
    """
    helper = """
        async def compute(ctx):
            ctx.logger.info("m.computed")
    """
    problems = _problems(
        tmp_path, body, imports="from helpers import compute\n", files={"helpers.py": helper}
    )
    assert problems == []


def test_a_function_passed_to_to_thread_is_followed(tmp_path: Path) -> None:
    body = """
        @route.post("/run")
        async def run(self, ctx: ModuleContext) -> None:
            await asyncio.to_thread(self._work, ctx)

        def _work(self, ctx: ModuleContext) -> None:
            ctx.logger.info("m.worked")
    """
    assert _problems(tmp_path, body, imports="import asyncio\n") == []


def test_a_name_bound_to_ctx_logger_counts(tmp_path: Path) -> None:
    body = """
        @route.post("/run")
        async def run(self, ctx: ModuleContext) -> None:
            log = ctx.logger.bind(stage="load")
            log.info("m.loading")
    """
    assert _problems(tmp_path, body) == []


def test_exception_logging_counts(tmp_path: Path) -> None:
    body = """
        @route.delete("/things/{thing}")
        async def drop(self, ctx: ModuleContext, thing: str) -> None:
            try:
                pass
            except OSError:
                ctx.logger.exception("m.drop_failed", thing=thing)
    """
    assert _problems(tmp_path, body) == []


# -- violations -----------------------------------------------------------------


def test_a_silent_job_is_reported_with_its_location(tmp_path: Path) -> None:
    body = """
        @route.post("/run")
        @job(progress=True)
        async def run(self, ctx: ModuleContext) -> None:
            await ctx.progress.update(1.0)
    """
    problems = _problems(tmp_path, body)
    assert len(problems) == 1
    assert problems[0].startswith("module.py:") and "job handler run()" in problems[0]
    assert "ctx.logger" in problems[0]


def test_a_silent_event_handler_is_reported(tmp_path: Path) -> None:
    body = """
        @on_event("log.imported")
        async def on_import(self, ctx: ModuleContext, payload: dict) -> None:
            return None
    """
    assert "event handler on_import()" in _problems(tmp_path, body)[0]


@pytest.mark.parametrize("verb", ["post", "put", "patch", "delete"])
def test_a_silent_mutating_route_is_reported(tmp_path: Path, verb: str) -> None:
    body = f"""
        @route.{verb}("/x")
        async def change(self, ctx: ModuleContext) -> dict:
            return {{}}
    """
    assert "mutating route handler change()" in _problems(tmp_path, body)[0]


def test_every_silent_handler_is_listed_in_one_pass(tmp_path: Path) -> None:
    body = """
        @route.post("/a")
        async def a(self, ctx: ModuleContext) -> dict:
            return {}

        @route.post("/b")
        async def b(self, ctx: ModuleContext) -> dict:
            return {}

        @route.get("/c")
        async def c(self, ctx: ModuleContext) -> dict:
            ctx.logger.info("m.c")
            return {}
    """
    problems = _problems(tmp_path, body)
    assert [("a()" in p, "b()" in p) for p in problems] == [(True, False), (False, True)]


def test_a_get_only_module_that_never_logs_is_reported(tmp_path: Path) -> None:
    body = """
        @route.get("/a")
        async def a(self, ctx: ModuleContext) -> dict:
            return {}
    """
    problems = _problems(tmp_path, body)
    assert len(problems) == 1 and "no handler logs through ctx.logger" in problems[0]


def test_a_silent_get_route_is_fine_when_the_writes_log(tmp_path: Path) -> None:
    body = """
        @route.get("/read")
        async def read(self, ctx: ModuleContext) -> dict:
            return {}

        @route.post("/write")
        async def write(self, ctx: ModuleContext) -> dict:
            ctx.logger.info("m.write")
            return {}
    """
    assert _problems(tmp_path, body) == []


@pytest.mark.parametrize(
    "line",
    [
        'print("computing")',
        'logging.getLogger(__name__).info("computing")',
        'structlog.get_logger().info("computing")',
        'LOG.info("computing")',
    ],
)
def test_other_loggers_do_not_count(tmp_path: Path, line: str) -> None:
    """`print` and a module-level logger go to the server's stdout - never the Logs tab."""
    body = f"""
        @route.post("/run")
        async def run(self, ctx: ModuleContext) -> None:
            {line}
    """
    assert "never reaches a ctx.logger call" in _problems(tmp_path, body)[0]


def test_a_logging_call_in_tests_or_a_venv_does_not_count(tmp_path: Path) -> None:
    body = """
        @route.post("/run")
        async def run(self, ctx: ModuleContext) -> None:
            return None
    """
    leak = "def run(ctx):\n    ctx.logger.info('x')\n"
    problems = _problems(
        tmp_path,
        body,
        files={"tests/test_it.py": leak, ".venv/lib/x.py": leak, "node_modules/y.py": leak},
    )
    assert len(problems) == 1 and "run()" in problems[0]


def test_an_unparseable_file_is_reported_not_raised(tmp_path: Path) -> None:
    body = """
        @route.get("/a")
        async def a(self, ctx: ModuleContext) -> dict:
            ctx.logger.info("m.a")
            return {}
    """
    problems = _problems(tmp_path, body, files={"broken.py": "def oops(:\n"})
    assert len(problems) == 1 and problems[0].startswith("broken.py: cannot be parsed")


def test_a_jvm_module_is_exempt_from_the_static_check(tmp_path: Path) -> None:
    # The jar is opaque to a source scan; the JVM SDK's `ctx.logger()` is documented.
    jvm = SimpleNamespace(runtime=SimpleNamespace(kind="jvm"))
    assert check_logging(tmp_path, jvm) == []  # type: ignore[arg-type]


def test_a_module_without_handlers_has_nothing_to_log(tmp_path: Path) -> None:
    assert _problems(tmp_path, "pass\n") == []


# -- require_logging + CLI ------------------------------------------------------


def test_require_logging_raises_listing_every_problem(tmp_path: Path) -> None:
    body = """
        @route.post("/a")
        async def a(self, ctx: ModuleContext) -> dict:
            return {}

        @route.put("/b")
        async def b(self, ctx: ModuleContext) -> dict:
            return {}
    """
    folder, manifest = _module(tmp_path, body)
    with pytest.raises(ModuleManifestError) as info:
        require_logging(folder, manifest)
    message = str(info.value)
    assert "must log through ctx.logger" in message and "a()" in message and "b()" in message


def test_require_logging_passes_a_compliant_module(tmp_path: Path) -> None:
    folder, manifest = _module(
        tmp_path,
        """
        @route.get("/a")
        async def a(self, ctx: ModuleContext) -> dict:
            ctx.logger.info("m.a")
            return {}
        """,
    )
    require_logging(folder, manifest)


def test_cli_exit_codes(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    good, _ = _module(
        tmp_path / "good",
        """
        @route.get("/a")
        async def a(self, ctx: ModuleContext) -> dict:
            ctx.logger.info("m.a")
            return {}
        """,
    )
    assert main([str(good)]) == 0
    assert "ok   m" in capsys.readouterr().out

    bad, _ = _module(
        tmp_path / "bad",
        """
        @route.post("/a")
        async def a(self, ctx: ModuleContext) -> dict:
            return {}
        """,
    )
    assert main([str(bad)]) == 1
    out = capsys.readouterr().out
    assert "FAIL m" in out and "0/1 modules log through ctx.logger." in out

    empty = tmp_path / "empty"
    empty.mkdir()
    assert main([str(empty)]) == 2
    assert main([]) == 2
    assert main(["--help"]) == 0


# -- platform policy ------------------------------------------------------------


def test_loading_a_silent_module_only_warns(tmp_path: Path) -> None:
    folder, manifest = _module(
        tmp_path,
        """
        @route.post("/a")
        async def a(self, ctx: ModuleContext) -> dict:
            return {}
        """,
    )
    with capture_logs() as logs:
        warn_on_logging_problems(folder, manifest)
    warned = [e for e in logs if e["event"] == "modules.logging.missing"]
    assert len(warned) == 1 and warned[0]["module_id"] == "m"
    assert "a()" in warned[0]["problems"][0]


def test_a_compliant_module_loads_without_a_warning(tmp_path: Path) -> None:
    folder, manifest = _module(
        tmp_path,
        """
        @route.get("/a")
        async def a(self, ctx: ModuleContext) -> dict:
            ctx.logger.info("m.a")
            return {}
        """,
    )
    with capture_logs() as logs:
        warn_on_logging_problems(folder, manifest)
    assert logs == []


@pytest.mark.asyncio
async def test_upload_of_a_module_that_never_logs_is_rejected(
    client_with_sample_mod: AsyncClient,
) -> None:
    body: dict[str, Any] = await _upload(client_with_sample_mod, "silent_mod", with_logging=False)
    assert body["status"] == "failed", body
    message = (body.get("message") or "") + (body.get("error") or "")
    assert "must log through ctx.logger" in message
    assert "no handler logs through ctx.logger" in message

    listed = (await client_with_sample_mod.get("/api/v1/modules")).json()
    assert "silent_mod" not in [m["id"] for m in listed]


@pytest.mark.asyncio
async def test_upload_of_a_module_that_logs_is_accepted(
    client_with_sample_mod: AsyncClient,
) -> None:
    body = await _upload(client_with_sample_mod, "chatty_mod")
    assert body["status"] == "completed", body


# -- every bundled module complies ----------------------------------------------


@pytest.mark.parametrize("folder", _BUNDLED, ids=lambda p: p.name)
def test_bundled_module_logs_through_ctx_logger(folder: Path) -> None:
    manifest = Manifest.load_yaml(folder / "manifest.yaml")
    assert check_logging(folder, manifest) == []
