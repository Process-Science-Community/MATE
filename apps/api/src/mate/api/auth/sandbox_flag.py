"""Persisted "this account is sandboxed" flag.

Roles live only on the request-time ``CurrentUser``. A share recipient is known
by user id alone (``mate.api.sharing``), so the sandboxed state - a realm role in
``SANDBOXED_ROLES``, e.g. the anonymous ``reviewer`` accounts - is mirrored into
a ``UserSetting`` on every first request per process (``_jit_sync_user``), the
same way the tracking exemption is (``services.tracking_exemption``).

A recipient reading through a share is making a request, so the flag is synced
in this process before the read is checked.
"""

from __future__ import annotations

from sqlalchemy.ext.asyncio import AsyncSession

from mate.api.db.models import UserSetting

SANDBOXED_KEY = "auth.sandboxed"


async def sync_sandbox_flag(session: AsyncSession, user_id: str, sandboxed: bool) -> None:
    """Align the persisted flag with the token's roles. Flushes, does not commit."""
    row = await session.get(UserSetting, (user_id, SANDBOXED_KEY))
    if sandboxed and row is None:
        session.add(UserSetting(user_id=user_id, key=SANDBOXED_KEY, value_json={"source": "role"}))
        await session.flush()
    elif not sandboxed and row is not None:
        await session.delete(row)
        await session.flush()


async def user_is_sandboxed(session: AsyncSession, user_id: str) -> bool:
    return await session.get(UserSetting, (user_id, SANDBOXED_KEY)) is not None
