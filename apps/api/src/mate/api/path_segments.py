"""Guard for ids that become a single path segment under ``data/``.

Log ids (and similar ids) are joined straight into on-disk paths such as
``users/<user>/event_logs/<log_id>`` and ``module_results/<log_id>/<module>``.
Several routes accept them as free strings, so a value like ``../../<other
user>/...`` would leave the caller's own folder. Every such join goes through
:func:`safe_segment`. Real ids (uuid7, the ``log-…`` style ids used in tests)
pass unchanged.
"""

from __future__ import annotations

import re

_SEGMENT = re.compile(r"[A-Za-z0-9_][A-Za-z0-9._-]{0,127}")


def safe_segment(value: str, what: str = "id") -> str:
    """Return ``value`` if it is a plain path segment, else raise ``ValueError``."""
    if not _SEGMENT.fullmatch(value) or ".." in value:
        raise ValueError(f"Invalid {what} {value!r}.")
    return value
