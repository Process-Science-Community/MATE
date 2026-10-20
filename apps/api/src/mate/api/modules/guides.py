"""Per-module ``HOW_TO_USE.md`` guides: where they live, how they are read, and
the soft check run when a module is loaded.

The format and its validator live in the SDK (``mate.sdk.guide``). The
platform's policy around it:

- **Upload is a hard gate** (``install_jobs``): no valid guide, no install.
- **Boot and hot reload only warn.** A module already on disk - an upload that
  predates the rule - keeps loading; it simply has no "How to use" panel until
  its owner re-uploads it with a guide.
"""

from __future__ import annotations

from pathlib import Path

import structlog

from mate.sdk.guide import GUIDE_FILENAME, GUIDE_MAX_BYTES, check_guide
from mate.sdk.manifest import Manifest

log = structlog.get_logger(__name__)


def guide_path(folder: Path) -> Path | None:
    """The module's guide file, or ``None`` when it has none.

    The resolved path must stay inside *folder*, so a symlinked guide can't turn
    the guide route into an arbitrary-file read.
    """
    try:
        candidate = (folder / GUIDE_FILENAME).resolve()
        candidate.relative_to(folder.resolve())
    except (OSError, ValueError):
        return None
    return candidate if candidate.is_file() else None


def has_guide(folder: Path) -> bool:
    return guide_path(folder) is not None


def read_guide(folder: Path) -> str | None:
    """The guide's markdown, or ``None`` when the module has none.

    Read from disk on every call (no cache): editing a guide shows up on the
    next request with no module reload. The read is bounded because a folder on
    disk may predate the 64 KiB upload limit.
    """
    path = guide_path(folder)
    if path is None:
        return None
    try:
        with path.open("rb") as fh:
            raw = fh.read(GUIDE_MAX_BYTES * 2)
    except OSError:
        return None
    return raw.decode("utf-8", errors="replace")


def warn_on_guide_problems(folder: Path, manifest: Manifest) -> None:
    """Log (never raise) when a module being loaded has a missing or invalid guide."""
    try:
        problems = check_guide(folder, manifest.name)
    except OSError as exc:
        problems = [f"could not read {GUIDE_FILENAME}: {exc}"]
    if problems:
        log.warning(
            "modules.guide.invalid",
            module_id=manifest.id,
            folder=str(folder),
            problems=problems,
        )
