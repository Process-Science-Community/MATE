"""A minimal valid ``HOW_TO_USE.md`` for tests that build throwaway modules.

Every uploaded module must carry a guide in the fixed format
(``mate.sdk.guide``); test modules that go through the upload path or the guide
validator build theirs from here so the format lives in one place.
"""

from __future__ import annotations

from mate.sdk.guide import GUIDE_FILENAME

__all__ = ["GUIDE_FILENAME", "minimal_guide"]


def minimal_guide(name: str) -> str:
    """The shortest guide that passes ``check_guide`` for a module called *name*."""
    return (
        f"# How to use {name}\n"
        "\n"
        "A test module that answers one question, so its guide stays short.\n"
        "\n"
        "## When to use it\n"
        "\n"
        "- When a test needs a module that has a guide.\n"
        "\n"
        "## Before you start\n"
        "\n"
        "- Any event log.\n"
        "\n"
        "## Settings\n"
        "\n"
        "This module has no settings.\n"
        "\n"
        "## Reading the results\n"
        "\n"
        "There is nothing to read.\n"
        "\n"
        "## Tips and limits\n"
        "\n"
        "- It is a fixture.\n"
        "\n"
        "## Method\n"
        "\n"
        "None - this is a test double.\n"
    )
