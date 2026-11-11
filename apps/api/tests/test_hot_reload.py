"""Hot-reload watcher path filter.

Regression: the watcher used to ignore only `.venv/.dist/node_modules` at depth
1, so a reload's own writes (`.installed-hash`, `__pycache__/*.pyc`) re-triggered
it → infinite reload loop on a prod box left in `env=dev`.
"""

from pathlib import Path

import pytest

from mate.api.modules.hot_reload import _is_ignored, _module_relative, _symlinked_entries


@pytest.mark.parametrize(
    "rel",
    [
        "conformance/.installed-hash",  # installer rewrites this every rebuild
        "conformance/pyproject.toml",  # synthesised by the installer
        "conformance/__pycache__/module.cpython-312.pyc",  # re-import bytecode
        "conformance/sub/__pycache__/x.pyc",  # nested __pycache__ (deeper than parts[1])
        "conformance/module.cpython-312.pyc",  # stray bytecode by suffix
        "cv4cdd/.venv/lib/python3.12/site-packages/x.py",
        "cv4cdd/.dist/panel.js",
        "ocel/node_modules/dep/index.js",
        "conformance/.DS_Store",
    ],
)
def test_artifacts_ignored(rel: str) -> None:
    assert _is_ignored(Path(rel)) is True


@pytest.mark.parametrize(
    "rel",
    [
        "conformance/module.py",  # the source we DO want to reload on
        "conformance/manifest.yaml",
        "conformance/conformance.py",
        "cv4cdd/panel/index.tsx",
    ],
)
def test_source_not_ignored(rel: str) -> None:
    assert _is_ignored(Path(rel)) is False


def test_symlinked_entry_maps_real_paths_back(tmp_path: Path) -> None:
    """A symlinked module folder: watchdog reports edits under
    the real folder, which must map back to the staged entry (and keep the
    artifact filter)."""
    real = tmp_path / "repo" / "complexity"
    real.mkdir(parents=True)
    (real / "manifest.yaml").write_text("id: complexity\n")
    stage = tmp_path / "stage"
    stage.mkdir()
    (stage / "complexity").symlink_to(real)
    (stage / "plain").mkdir()
    # Not a module (the tracked `modules/node_modules` link): never watched.
    (tmp_path / "web_deps").mkdir()
    (stage / "node_modules").symlink_to(tmp_path / "web_deps")

    links = _symlinked_entries(stage)
    assert links == {real.resolve(): "complexity"}
    assert _module_relative(real / "module.py", stage, links) == Path("complexity/module.py")
    venv_file = _module_relative(real / ".venv" / "x.py", stage, links)
    assert venv_file is not None and _is_ignored(venv_file)
    assert _module_relative(stage / "plain" / "module.py", stage, links) == Path("plain/module.py")
    assert _module_relative(tmp_path / "elsewhere.py", stage, links) is None
