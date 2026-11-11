"""Sample-content seeding (services/sample_content.py) and reviewer isolation.

The seeder imports the manifest's logs and creates the starter dashboard for
an account that has never signed in. It runs through the real job runtime, as
a browser upload would. It must be idempotent, mark the account
tracking-exempt before any job runs, and leave the content visible to that
account only.
"""

from __future__ import annotations

import contextlib
import json
import shutil
from collections.abc import AsyncIterator
from pathlib import Path

import pytest
from httpx import ASGITransport, AsyncClient

SEED_USER_ID = "00000000-0000-7000-8000-0000000000s1"
OTHER_SEED_USER_ID = "00000000-0000-7000-8000-0000000000s2"
FIXTURE_CSV = Path(__file__).parent / "fixtures" / "sample.csv"


def _manifest(tmp_path: Path) -> Path:
    shutil.copyfile(FIXTURE_CSV, tmp_path / "orders.csv")
    path = tmp_path / "manifest.json"
    mapping = {
        "case_id": "case_id",
        "activity": "activity",
        "timestamp": "timestamp",
        "resource": "resource",
    }
    path.write_text(
        json.dumps(
            {
                "tracking_exempt": True,
                "users": [
                    {"id": SEED_USER_ID, "username": "reviewer-s1"},
                    {"id": OTHER_SEED_USER_ID, "username": "reviewer-s2"},
                ],
                "logs": [
                    {"file": "orders.csv", "name": "Orders A", "csv_mapping": mapping},
                    {"file": "orders.csv", "name": "Orders B", "csv_mapping": mapping},
                ],
                "dashboard": {"name": "Example dashboard", "template_id": "process-overview"},
            }
        )
    )
    return path


async def _seed_all(manifest_path: Path) -> list[bool]:
    """Phase 1 for every account (True = seeded now, False = already seeded)."""
    from mate.api.db.engine import get_sessionmaker
    from mate.api.jobs.runtime import get_job_runtime
    from mate.api.modules import get_module_loader
    from mate.api.services.sample_content import load_manifest, seed_user

    manifest = load_manifest(manifest_path)
    return [
        await seed_user(
            get_sessionmaker(),
            get_job_runtime(),
            get_module_loader(),
            manifest,
            manifest_path.parent,
            user,
        )
        is not None
        for user in manifest.users
    ]


@pytest.mark.asyncio
async def test_seed_from_settings_runs_both_phases(
    client: AsyncClient, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The entry point imports every account first, then warms only the listed
    modules - in that order, so warm-up jobs never sit in front of an import."""
    from mate.api import config as cfg
    from mate.api.db.engine import get_sessionmaker
    from mate.api.jobs.runtime import get_job_runtime
    from mate.api.modules import get_module_loader
    from mate.api.modules.processing import get_coordinator
    from mate.api.services import sample_content

    manifest = _manifest(tmp_path)
    data = json.loads(manifest.read_text())
    data["users"] = [{"id": "00000000-0000-7000-8000-0000000000s4", "username": "r4"}]
    data["warm_modules"] = ["discovery"]
    manifest.write_text(json.dumps(data))

    calls: list[str] = []
    real_seed, real_warm = sample_content.seed_user, sample_content.warm_modules

    async def seed(*a: object, **k: object) -> list[str] | None:
        calls.append("seed")
        return await real_seed(*a, **k)  # type: ignore[arg-type]

    async def warm(*a: object, **k: object) -> None:
        calls.append("warm")
        await real_warm(*a, **k)  # type: ignore[arg-type]

    monkeypatch.setattr(sample_content, "seed_user", seed)
    monkeypatch.setattr(sample_content, "warm_modules", warm)
    monkeypatch.setenv("SAMPLE_CONTENT_MANIFEST", str(manifest))
    cfg.get_settings.cache_clear()
    try:
        coordinator = get_coordinator()
        assert coordinator is not None
        await sample_content.seed_from_settings(
            get_sessionmaker(), get_job_runtime(), get_module_loader(), coordinator
        )
    finally:
        monkeypatch.delenv("SAMPLE_CONTENT_MANIFEST")
        cfg.get_settings.cache_clear()
    assert calls == ["seed", "warm"]


@contextlib.asynccontextmanager
async def _as_user(user_id: str, username: str) -> AsyncIterator[AsyncClient]:
    """A client signed in as ``user_id`` (needs the app booted by ``client``)."""
    from mate.api.auth.dependencies import CurrentUser, get_current_user
    from mate.api.main import create_app

    principal = CurrentUser(
        id=user_id, email=None, preferred_username=username, name=None, roles=("user", "reviewer")
    )

    async def _user() -> CurrentUser:
        return principal

    app = create_app()
    app.dependency_overrides[get_current_user] = _user
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://testserver") as c:
        yield c


@pytest.mark.asyncio
async def test_seed_imports_logs_and_dashboard_once(client: AsyncClient, tmp_path: Path) -> None:
    from sqlalchemy import func, select

    from mate.api.db.engine import get_sessionmaker
    from mate.api.db.models import AnalyticsEvent, Dashboard, EventLog, UserSetting
    from mate.api.services.sample_content import SEEDED_KEY
    from mate.api.services.tracking_exemption import is_tracking_exempt

    manifest = _manifest(tmp_path)
    assert await _seed_all(manifest) == [True, True]
    # Second start: already seeded, nothing new.
    assert await _seed_all(manifest) == [False, False]

    async with get_sessionmaker()() as session:
        for uid in (SEED_USER_ID, OTHER_SEED_USER_ID):
            logs = (
                await session.execute(
                    select(EventLog.name, EventLog.status).where(EventLog.user_id == uid)
                )
            ).all()
            assert sorted(logs) == [("Orders A", "ready"), ("Orders B", "ready")]
            boards = (
                await session.scalars(select(Dashboard).where(Dashboard.user_id == uid))
            ).all()
            assert [b.name for b in boards] == ["Example dashboard"]
            assert boards[0].event_log_id is not None
            assert (boards[0].layout_json or {}).get("items")
            assert await session.get(UserSetting, (uid, SEEDED_KEY)) is not None
            assert await is_tracking_exempt(session, uid)
            # Not even the seeding jobs were recorded.
            recorded = await session.scalar(
                select(func.count())
                .select_from(AnalyticsEvent)
                .where(AnalyticsEvent.user_id == uid)
            )
            assert recorded == 0


@pytest.mark.asyncio
async def test_seeded_content_is_isolated(client: AsyncClient, tmp_path: Path) -> None:
    from sqlalchemy import select

    from mate.api.db.engine import get_sessionmaker
    from mate.api.db.models import Dashboard, EventLog

    await _seed_all(_manifest(tmp_path))
    async with get_sessionmaker()() as session:
        own_log = await session.scalar(
            select(EventLog.id).where(EventLog.user_id == SEED_USER_ID).limit(1)
        )
        own_board = await session.scalar(
            select(Dashboard.id).where(Dashboard.user_id == SEED_USER_ID).limit(1)
        )
    assert own_log and own_board

    async with _as_user(SEED_USER_ID, "reviewer-s1") as me:
        assert (await me.get(f"/api/v1/event-logs/{own_log}")).status_code == 200
        assert (await me.get(f"/api/v1/dashboards/{own_board}")).status_code == 200

    # Another reviewer and an ordinary account see neither the log nor the board.
    async with _as_user(OTHER_SEED_USER_ID, "reviewer-s2") as other:
        listed = (await other.get("/api/v1/event-logs")).json()
        assert own_log not in json.dumps(listed)
        assert (await other.get(f"/api/v1/event-logs/{own_log}")).status_code == 404
        assert (await other.get(f"/api/v1/dashboards/{own_board}")).status_code == 404
        boards = (await other.get("/api/v1/dashboards")).json()
        assert own_board not in json.dumps(boards)
    # `client` is the shared non-reviewer test account.
    assert (await client.get(f"/api/v1/event-logs/{own_log}")).status_code == 404
    assert (await client.get(f"/api/v1/dashboards/{own_board}")).status_code == 404
    assert own_log not in json.dumps((await client.get("/api/v1/event-logs")).json())


def test_manifest_rejects_paths_outside_its_folder(tmp_path: Path) -> None:
    from mate.api.services.sample_content import load_manifest

    (tmp_path / "sub").mkdir()
    path = tmp_path / "sub" / "manifest.json"
    shutil.copyfile(FIXTURE_CSV, tmp_path / "outside.csv")
    path.write_text(json.dumps({"users": [], "logs": [{"file": "../outside.csv", "name": "x"}]}))
    with pytest.raises(ValueError, match="outside"):
        load_manifest(path)


@pytest.mark.asyncio
async def test_dead_import_is_replaced_not_awaited(client: AsyncClient, tmp_path: Path) -> None:
    """A same-named log stuck in ``importing`` (a failed or interrupted import never
    flips to ``failed``) is removed and imported afresh."""
    from datetime import UTC, datetime

    from sqlalchemy import select

    from mate.api.db.engine import get_sessionmaker
    from mate.api.db.models import EventLog, User

    uid = "00000000-0000-7000-8000-0000000000s3"
    manifest = _manifest(tmp_path)
    data = json.loads(manifest.read_text())
    data["users"] = [{"id": uid, "username": "reviewer-s3"}]
    manifest.write_text(json.dumps(data))
    async with get_sessionmaker()() as session:
        session.add(User(id=uid, preferred_username="reviewer-s3"))
        await session.flush()
        session.add(
            EventLog(
                id="dead-import-s3",
                user_id=uid,
                name="Orders A",
                source_format="csv",
                source_filename="orders.csv",
                status="importing",
                created_at=datetime.now(UTC).replace(tzinfo=None),
            )
        )
        await session.commit()

    assert await _seed_all(manifest) == [True]
    async with get_sessionmaker()() as session:
        rows = (
            await session.execute(
                select(EventLog.id, EventLog.status, EventLog.deleted_at).where(
                    EventLog.user_id == uid, EventLog.name == "Orders A"
                )
            )
        ).all()
    live = [r for r in rows if r.deleted_at is None]
    assert len(live) == 1 and live[0].status == "ready" and live[0].id != "dead-import-s3"
