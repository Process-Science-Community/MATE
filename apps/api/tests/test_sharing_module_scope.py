"""A shared dashboard exposes only the modules it actually places, not every
module's outputs for the whole log (security fix for the share over-grant).

Unit-level: seeds a log, a dashboard bound to it that shows ONE module, and a
share to a recipient, then checks the module-scoped read gate directly.
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime

import pytest

from .conftest import TEST_USER_ID  # the owner

RECIPIENT = "share-scope-recipient"
STRANGER = "share-scope-stranger"


def _now() -> datetime:
    return datetime.now(UTC).replace(tzinfo=None)


async def _seed(shared_module: str) -> str:
    """Owner uploads a log; a dashboard bound to it shows only ``shared_module`` (a
    widget card) and ``conformance`` (a viz card that names its module only through
    ``output_ref``, as MCP ``create_dashboard`` may save it); the board is shared
    with RECIPIENT. Returns the log id."""
    from mate.api.db.engine import get_sessionmaker
    from mate.api.db.models import Dashboard, DashboardShare, EventLog, User

    log_id = str(uuid.uuid4())
    sm = get_sessionmaker()
    async with sm() as s:
        for uid in (RECIPIENT, STRANGER):
            if await s.get(User, uid) is None:
                s.add(User(id=uid, email=f"{uid}@m.local", preferred_username=uid, name=uid))
        s.add(
            EventLog(
                id=log_id,
                user_id=TEST_USER_ID,
                name="shared-log",
                status="ready",
                source_format="csv",
                created_at=_now(),
            )
        )
        await s.flush()  # the dashboard's event_log_id FK needs the log row first
        dash_id = str(uuid.uuid4())
        s.add(
            Dashboard(
                id=dash_id,
                user_id=TEST_USER_ID,
                name="one-module board",
                event_log_id=log_id,
                layout_json={
                    "items": [
                        {"i": "c1", "kind": "widget", "module_id": shared_module},
                        {
                            "i": "c2",
                            "kind": "viz",
                            "viz_id": "table",
                            "output_ref": {"module_id": "conformance", "output_id": "rows"},
                        },
                    ]
                },
            )
        )
        s.add(
            DashboardShare(
                id=str(uuid.uuid4()),
                dashboard_id=dash_id,
                target_user_id=RECIPIENT,
                created_by=TEST_USER_ID,
                created_at=_now(),
            )
        )
        await s.commit()
    return log_id


@pytest.mark.asyncio
async def test_share_grants_only_the_shared_board_module() -> None:
    from mate.api.db.engine import get_sessionmaker
    from mate.api.sharing import user_can_access_log_module

    log_id = await _seed(shared_module="discovery")
    sm = get_sessionmaker()
    async with sm() as s:
        # Owner reads any module on their own log.
        assert await user_can_access_log_module(s, log_id, TEST_USER_ID, "discovery") is True
        assert await user_can_access_log_module(s, log_id, TEST_USER_ID, "performance") is True
        # Recipient reads ONLY the module the shared board places.
        assert await user_can_access_log_module(s, log_id, RECIPIENT, "discovery") is True
        # A viz card's module counts even without a top-level module_id.
        assert await user_can_access_log_module(s, log_id, RECIPIENT, "conformance") is True
        assert await user_can_access_log_module(s, log_id, RECIPIENT, "performance") is False
        # A stranger reads nothing.
        assert await user_can_access_log_module(s, log_id, STRANGER, "discovery") is False
