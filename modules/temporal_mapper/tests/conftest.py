"""Put the repo root on ``sys.path`` so ``modules.temporal_mapper`` resolves.

The module tests import the implementation under the ``modules.temporal_mapper``
package name, which requires the repository root on ``sys.path``.
"""

from __future__ import annotations

import sys
from pathlib import Path

_REPO_ROOT = Path(__file__).resolve().parents[3]
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))
