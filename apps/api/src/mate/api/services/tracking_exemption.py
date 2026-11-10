"""Per-account exemption from usage tracking.

``USER_TRACKING_ONBOARDING`` sets one policy for every account, and ``force``
gives no opt-out. Some accounts must never be recorded, whatever that policy
says. The anonymous conference-reviewer accounts are the example: whatever
they click must not end up in the platform's own usage log. An account is
exempt when:

- its token carries a realm role listed in ``USER_TRACKING_EXEMPT_ROLES``
  (default ``reviewer``), or
- its username or Keycloak id is listed in ``USER_TRACKING_EXEMPT_USERS``, or
- it carries the persisted ``analytics.exempt`` flag. That flag is written on
  the account's first request in a process when it holds an exempt role, and
  by the sample-content seeder before it runs jobs for the account.

Roles live only on the request-time ``CurrentUser``. Most recorders (the request
middleware's batched writer, job outcomes, MCP audit) only know a ``user_id``, so
the role is persisted as the flag above. That way every recorder can see it
through ``routes.analytics.cached_config``, which is the single gate they all
share.
"""

from __future__ import annotations

from collections.abc import Iterable
from typing import Literal

from sqlalchemy.ext.asyncio import AsyncSession

from mate.api.config import get_settings
from mate.api.db.models import User, UserSetting

TRACKING_EXEMPT_KEY = "analytics.exempt"

ExemptSource = Literal["role", "manifest"]


def roles_exempt(roles: Iterable[str]) -> bool:
    """True if any of ``roles`` is configured as tracking-exempt."""
    exempt = get_settings().tracking_exempt_roles
    return bool(exempt) and any(r in exempt for r in roles)


def identity_listed(user_id: str, username: str | None) -> bool:
    """True if the account's id or username is on the env allowlist."""
    listed = get_settings().tracking_exempt_users
    if not listed:
        return False
    return user_id in listed or (username is not None and username in listed)


async def is_tracking_exempt(session: AsyncSession, user_id: str) -> bool:
    """Whether nothing may be recorded for ``user_id`` (flag or allowlist)."""
    if await session.get(UserSetting, (user_id, TRACKING_EXEMPT_KEY)) is not None:
        return True
    if not get_settings().tracking_exempt_users:
        return False
    user = await session.get(User, user_id)
    return identity_listed(user_id, user.preferred_username if user is not None else None)


async def mark_tracking_exempt(
    session: AsyncSession, user_id: str, *, source: ExemptSource
) -> bool:
    """Persist the exemption flag for ``user_id``; True if it was added.

    Flushes, does not commit. The caller commits and then calls
    :func:`invalidate_tracking_cache`: invalidating before the commit would let
    a recorder in another session re-cache the pre-flag state.
    """
    row = await session.get(UserSetting, (user_id, TRACKING_EXEMPT_KEY))
    if row is not None:
        return False
    session.add(
        UserSetting(user_id=user_id, key=TRACKING_EXEMPT_KEY, value_json={"source": source})
    )
    await session.flush()
    return True


async def sync_role_exemption(session: AsyncSession, user_id: str, roles: Iterable[str]) -> bool:
    """Align the persisted flag with the token's roles; True if it changed.

    Flushes, does not commit (see :func:`mark_tracking_exempt`).

    An exempt role sets the flag. A flag that came from a role is cleared when
    the role is gone. A flag the seeder wrote (``source=manifest``) is never
    cleared here: that account was declared exempt explicitly.
    """
    row = await session.get(UserSetting, (user_id, TRACKING_EXEMPT_KEY))
    if roles_exempt(roles):
        if row is None:
            return await mark_tracking_exempt(session, user_id, source="role")
        return False
    if (
        row is not None
        and isinstance(row.value_json, dict)
        and row.value_json.get("source") == "role"
    ):
        await session.delete(row)
        await session.flush()
        return True
    return False


def invalidate_tracking_cache(user_id: str) -> None:
    """Drop the cached consent state for ``user_id``; call after committing a change."""
    # Imported lazily: routes.analytics depends on the auth layer, which calls
    # into this module, so a top-level import would be circular.
    from mate.api.routes.analytics import invalidate_consent_cache

    invalidate_consent_cache(user_id)
