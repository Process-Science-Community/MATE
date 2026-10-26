"""The platform's policy around the compulsory ``ctx.logger`` contract.

The contract and its checker live in the SDK (``mate.sdk.logcheck``). Where it is
enforced:

- **Upload is a hard gate** (``install_jobs``): a module that never logs through
  ``ctx.logger`` is not installed, and the install job's error lists every handler
  that is missing it.
- **Loading is lenient**: a module already on disk keeps working, but boot,
  discovery and hot reload log a ``modules.logging.missing`` warning, so the gap is
  visible to whoever runs the platform and the module's author.
"""

from __future__ import annotations

from pathlib import Path

import structlog

from mate.sdk.logcheck import check_logging
from mate.sdk.manifest import Manifest

log = structlog.get_logger(__name__)


def warn_on_logging_problems(folder: Path, manifest: Manifest) -> None:
    """Log (never raise) when a module being loaded breaks the logging contract."""
    try:
        problems = check_logging(folder, manifest)
    except Exception as exc:  # an advisory check must never take discovery down
        problems = [f"could not analyse the module sources: {exc}"]
    if problems:
        log.warning(
            "modules.logging.missing",
            module_id=manifest.id,
            folder=str(folder),
            problems=problems,
        )
