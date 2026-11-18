"""The demo auth bypass must never be constructible under ENV=prod."""

from __future__ import annotations

import pytest


def test_prod_plus_demo_is_refused() -> None:
    from mate.api.config import Settings

    with pytest.raises(ValueError, match="DEMO_MODE"):
        Settings(env="prod", demo_mode=True)


def test_prod_without_demo_is_fine() -> None:
    from mate.api.config import Settings

    assert Settings(env="prod", demo_mode=False).demo_mode is False


def test_dev_with_demo_is_fine() -> None:
    from mate.api.config import Settings

    assert Settings(env="dev", demo_mode=True).demo_mode is True
