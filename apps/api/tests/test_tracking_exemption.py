"""Per-account tracking exemption (services/tracking_exemption.py).

``USER_TRACKING_ONBOARDING=force`` (the repo default) records everyone. An
account with an exempt realm role (default ``reviewer``), one listed in
``USER_TRACKING_EXEMPT_USERS``, or one carrying the persisted flag is never
recorded. That holds for every recorder: client ingest, the request
middleware's batched writer, and server events such as job outcomes.
"""

from __future__ import annotations

import os
from collections.abc import AsyncIterator, Iterator
from datetime import UTC, datetime
from types import SimpleNamespace

import pytest
from httpx import ASGITransport, AsyncClient

from .conftest import TEST_USER_ID

REVIEWER_ID = "00000000-0000-7000-8000-0000000000r1"
REVIEWER_USERNAME = "reviewer1"


def _batch(anon_user_id: str, event_name: str) -> dict[str, object]:
    now = datetime.now(UTC).isoformat()
    return {
        "session": {"id": f"exempt-{event_name}", "anon_user_id": anon_user_id, "started_at": now},
        "events": [
            {
                "event_type": "click",
                "event_name": event_name,
                "occurred_at": now,
                "path": "/x",
                "properties": {"kind": "click"},
            }
        ],
    }


async def _events_for(user_id: str) -> int:
    from sqlalchemy import func, select

    from mate.api.db.engine import get_sessionmaker
    from mate.api.db.models import AnalyticsEvent

    async with get_sessionmaker()() as session:
        return int(
            await session.scalar(
                select(func.count())
                .select_from(AnalyticsEvent)
                .where(AnalyticsEvent.user_id == user_id)
            )
            or 0
        )


async def _purge(user_ids: tuple[str, ...]) -> None:
    from sqlalchemy import delete

    from mate.api.db.engine import get_sessionmaker
    from mate.api.db.models import (
        AnalyticsEvent,
        AnalyticsObject,
        AnalyticsObjectRelation,
        AnalyticsSession,
        UserSetting,
    )
    from mate.api.routes.analytics import ANALYTICS_CONFIG_KEY, _consent_cache
    from mate.api.services.tracking_exemption import TRACKING_EXEMPT_KEY

    _consent_cache.clear()
    async with get_sessionmaker()() as session:
        for model in (AnalyticsEvent, AnalyticsObject, AnalyticsObjectRelation, AnalyticsSession):
            await session.execute(delete(model).where(model.user_id.in_(user_ids)))
        await session.execute(
            delete(UserSetting).where(
                UserSetting.user_id.in_(user_ids),
                UserSetting.key.in_([ANALYTICS_CONFIG_KEY, TRACKING_EXEMPT_KEY]),
            )
        )
        await session.commit()


def _set_env(name: str, value: str | None) -> None:
    from mate.api import config as cfg

    if value is None:
        os.environ.pop(name, None)
    else:
        os.environ[name] = value
    cfg.get_settings.cache_clear()


@pytest.fixture
def env() -> Iterator[dict[str, str | None]]:
    """Set env vars for one test (``env["X"] = "y"``) and restore them afterwards."""
    touched: dict[str, str | None] = {}

    class _Env(dict[str, str | None]):
        def __setitem__(self, key: str, value: str | None) -> None:
            touched.setdefault(key, os.environ.get(key))
            _set_env(key, value)
            super().__setitem__(key, value)

    yield _Env()
    for key, prev in touched.items():
        _set_env(key, prev)


@pytest.fixture
async def reviewer_client(client: AsyncClient) -> AsyncIterator[AsyncClient]:
    """The app (booted by ``client``), signed in as a ``reviewer``-role account.

    The ``users`` row and the persisted flag come from the real JIT sync, the
    same thing a real login runs.
    """
    from mate.api.auth.dependencies import (
        CurrentUser,
        _jit_sync_user,
        _seen_user_ids,
        get_current_user,
    )
    from mate.api.db.engine import get_sessionmaker
    from mate.api.main import create_app

    reviewer = CurrentUser(
        id=REVIEWER_ID,
        email=None,
        preferred_username=REVIEWER_USERNAME,
        name=None,
        roles=("user", "reviewer"),
    )
    _seen_user_ids.discard(REVIEWER_ID)
    async with get_sessionmaker()() as session:
        await _jit_sync_user(session, reviewer)
        await session.commit()

    async def _reviewer() -> CurrentUser:
        return reviewer

    app = create_app()
    app.dependency_overrides[get_current_user] = _reviewer
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://testserver") as c:
        yield c


@pytest.fixture(autouse=True)
async def _clean() -> AsyncIterator[None]:
    await _purge((TEST_USER_ID, REVIEWER_ID))
    yield
    await _purge((TEST_USER_ID, REVIEWER_ID))


@pytest.mark.asyncio
async def test_reviewer_role_disables_client_tracking_under_force(
    reviewer_client: AsyncClient,
) -> None:
    cfg = (await reviewer_client.get("/api/v1/usage/config")).json()
    assert cfg["onboarding_mode"] == "force"
    assert cfg["enabled"] is False
    assert cfg["tracking_exempt"] is True

    # The onboarding flow PUTs enabled=true under force; it must not stick.
    put = await reviewer_client.put("/api/v1/usage/config", json={**cfg, "enabled": True})
    assert put.json()["enabled"] is False

    resp = await reviewer_client.post(
        "/api/v1/usage/sync", json=_batch(cfg["anon_user_id_seed"], "reviewer.click")
    )
    assert resp.status_code == 204
    assert await _events_for(REVIEWER_ID) == 0


@pytest.mark.asyncio
async def test_reviewer_server_events_not_recorded(reviewer_client: AsyncClient) -> None:
    from mate.api.db.engine import get_sessionmaker
    from mate.api.middleware.usage import UsageTrackingMiddleware
    from mate.api.routes.analytics import record_server_event
    from mate.api.services.usage_recorder import flush_pending_for_tests

    # A job outcome / MCP audit event (only the user id is known there)...
    async with get_sessionmaker()() as session:
        await record_server_event(session, user_id=REVIEWER_ID, event_name="job.completed")
        await session.commit()
    # ...and a request seen by the all-requests middleware.
    mw = UsageTrackingMiddleware(app=None)
    scope = {
        "route": SimpleNamespace(path_format="/api/v1/event-logs/{log_id}"),
        "path_params": {"log_id": "log-1"},
    }
    mw._enqueue(scope, "GET", "/api/v1/event-logs/log-1", REVIEWER_ID, 200, 5)
    await flush_pending_for_tests()

    assert await _events_for(REVIEWER_ID) == 0


@pytest.mark.asyncio
async def test_regular_user_still_tracked(client: AsyncClient) -> None:
    from mate.api.db.engine import get_sessionmaker
    from mate.api.routes.analytics import record_server_event

    cfg = (await client.get("/api/v1/usage/config")).json()
    assert cfg["enabled"] is True
    assert cfg["tracking_exempt"] is False
    resp = await client.post(
        "/api/v1/usage/sync", json=_batch(cfg["anon_user_id_seed"], "regular.click")
    )
    assert resp.status_code == 202
    async with get_sessionmaker()() as session:
        await record_server_event(session, user_id=TEST_USER_ID, event_name="job.completed")
        await session.commit()
    assert await _events_for(TEST_USER_ID) == 2


@pytest.mark.asyncio
async def test_username_allowlist(client: AsyncClient, env: dict[str, str | None]) -> None:
    from mate.api.db.engine import get_sessionmaker
    from mate.api.routes.analytics import cached_config

    # The shared test user (username "test") has no exempt role.
    async with get_sessionmaker()() as session:
        assert (await cached_config(session, TEST_USER_ID)).enabled is True

    env["USER_TRACKING_EXEMPT_USERS"] = "someone-else, test"
    await _purge((TEST_USER_ID,))
    async with get_sessionmaker()() as session:
        cfg = await cached_config(session, TEST_USER_ID)
    assert cfg.enabled is False
    assert cfg.tracking_exempt is True


@pytest.mark.asyncio
async def test_role_flag_follows_role_but_manifest_flag_sticks(client: AsyncClient) -> None:
    from mate.api.db.engine import get_sessionmaker
    from mate.api.services.tracking_exemption import (
        is_tracking_exempt,
        mark_tracking_exempt,
        sync_role_exemption,
    )

    sm = get_sessionmaker()
    async with sm() as session:
        await sync_role_exemption(session, TEST_USER_ID, ("user", "reviewer"))
        await session.commit()
        assert await is_tracking_exempt(session, TEST_USER_ID)
        # Role revoked: the role-derived flag goes with it.
        await sync_role_exemption(session, TEST_USER_ID, ("user",))
        await session.commit()
        assert not await is_tracking_exempt(session, TEST_USER_ID)
        # A flag the seeder wrote is explicit and survives a role-less login.
        await mark_tracking_exempt(session, TEST_USER_ID, source="manifest")
        await sync_role_exemption(session, TEST_USER_ID, ("user",))
        await session.commit()
        assert await is_tracking_exempt(session, TEST_USER_ID)


@pytest.mark.asyncio
async def test_exempt_roles_configurable(client: AsyncClient, env: dict[str, str | None]) -> None:
    from mate.api.services.tracking_exemption import roles_exempt

    assert roles_exempt(("user", "reviewer"))
    assert not roles_exempt(("user", "admin"))
    env["USER_TRACKING_EXEMPT_ROLES"] = ""
    assert not roles_exempt(("user", "reviewer"))


@pytest.mark.asyncio
async def test_module_upload_blocked_for_configured_role(
    reviewer_client: AsyncClient, env: dict[str, str | None]
) -> None:
    files = {"file": ("mod.zip", b"PK\x05\x06" + b"\x00" * 18, "application/zip")}
    env["SANDBOXED_ROLES"] = "reviewer"  # the default, set explicitly
    resp = await reviewer_client.post("/api/v1/modules/install", files=files)
    assert resp.status_code == 403


@pytest.mark.asyncio
async def test_jit_flag_survives_a_failing_first_request(client: AsyncClient) -> None:
    """The flag is committed by the JIT sync itself, not by the (failing) request."""
    from mate.api.auth.dependencies import CurrentUser, _jit_sync_user, _seen_user_ids
    from mate.api.db.engine import get_sessionmaker
    from mate.api.services.tracking_exemption import is_tracking_exempt

    reviewer = CurrentUser(
        id=REVIEWER_ID, email=None, preferred_username="r", name=None, roles=("reviewer",)
    )
    _seen_user_ids.discard(REVIEWER_ID)
    async with get_sessionmaker()() as session:
        await _jit_sync_user(session, reviewer)
        await session.rollback()  # what _yield_session does when the handler raises
    async with get_sessionmaker()() as session:
        assert await is_tracking_exempt(session, REVIEWER_ID)


@pytest.mark.asyncio
async def test_sandboxed_role_stays_in_its_account(reviewer_client: AsyncClient) -> None:
    # Default SANDBOXED_ROLES=reviewer.
    custom = await reviewer_client.post(
        "/api/v1/watched-folders", json={"name": "w", "source_path": "/"}
    )
    assert custom.status_code == 403
    managed = await reviewer_client.post("/api/v1/watched-folders", json={"name": "mine"})
    assert managed.status_code == 201
    await reviewer_client.delete(f"/api/v1/watched-folders/{managed.json()['id']}")
    token = await reviewer_client.post("/api/v1/api-tokens", json={"name": "t"})
    assert token.status_code == 403


@pytest.mark.asyncio
async def test_dataset_routes_refuse_foreign_and_crafted_log_ids(client: AsyncClient) -> None:
    from mate.api.path_segments import safe_segment

    for bad in ("../x", "a/b", "..", "", "x/../../y"):
        with pytest.raises(ValueError):
            safe_segment(bad)
    assert safe_segment("01a1c648-b005-7206-9eaa-b764228a3076") == (
        "01a1c648-b005-7206-9eaa-b764228a3076"
    )
    # The read gate the dataset + AI-context routes now apply before a log id
    # reaches the module context / cache paths.
    from mate.api.db.engine import get_sessionmaker
    from mate.api.sharing import user_can_access_log

    async with get_sessionmaker()() as session:
        assert not await user_can_access_log(
            session, f"../../{REVIEWER_ID}/event_logs/x", TEST_USER_ID
        )
        assert not await user_can_access_log(session, "no-such-log", TEST_USER_ID)
