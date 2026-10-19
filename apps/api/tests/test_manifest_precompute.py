"""Manifest `precompute` - whether a module also computes in the background at import.

Module processing is lazy: a module computes the first time a log needs it. A
manifest can recommend `eager` for the one or two modules whose output is the first
thing people open, and an admin can override that. The invariants that matter:
  * absent `precompute` == `lazy` (every pre-existing manifest keeps working),
  * only `eager` / `lazy` are accepted - a typo must fail loudly, not silently
    become lazy,
  * the field never touches `dependencies_hash()` - a changed hash would rebuild
    every deployed module venv on upgrade,
  * the bundled eager set is pinned: `eager` costs CPU on every import of every
    user, so adding to it must be a deliberate, reviewed edit.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest
import yaml
from pydantic import ValidationError

from mate.sdk.manifest import Manifest

# The modules that precompute in the background on every import. Pinned so a
# manifest edit that makes another one eager is a test failure, not a bill.
EAGER = {"discovery"}

MODULES_DIR = Path(__file__).resolve().parents[3] / "modules"


def _manifest(**overrides: Any) -> Manifest:
    data: dict[str, Any] = {"id": "pc_test", "name": "PC", "version": "1.0.0", "category": "other"}
    data.update(overrides)
    return Manifest.model_validate(data)


def test_precompute_defaults_to_lazy() -> None:
    assert _manifest().precompute == "lazy"


@pytest.mark.parametrize("mode", ["eager", "lazy"])
def test_precompute_accepts_the_two_modes(mode: str) -> None:
    assert _manifest(precompute=mode).precompute == mode


@pytest.mark.parametrize("bad", ["Eager", "always", "on_import", True, 1])
def test_precompute_rejects_anything_else(bad: Any) -> None:
    with pytest.raises(ValidationError):
        _manifest(precompute=bad)


def test_precompute_does_not_affect_dependencies_hash() -> None:
    assert _manifest(precompute="eager").dependencies_hash() == _manifest().dependencies_hash()


def _bundled() -> list[tuple[str, Manifest]]:
    return [
        (m.id, m)
        for m in (
            Manifest.model_validate(yaml.safe_load(p.read_text()))
            for p in sorted(MODULES_DIR.glob("*/manifest.yaml"))
        )
    ]


@pytest.mark.parametrize(
    "module_id,manifest", _bundled(), ids=lambda v: v if isinstance(v, str) else ""
)
def test_bundled_eager_set_is_pinned(module_id: str, manifest: Manifest) -> None:
    assert (manifest.precompute == "eager") is (module_id in EAGER)
