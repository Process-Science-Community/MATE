"""End-to-end tests for the watched-folder import source (local backend)."""

from __future__ import annotations

import asyncio
import shutil
from collections.abc import AsyncIterator
from pathlib import Path

import pytest
from httpx import ASGITransport, AsyncClient

from .conftest import TEST_USER_EMAIL, TEST_USER_ID

FIXTURES = Path(__file__).parent / "fixtures"


async def _wait_until_ready(client: AsyncClient, log_id: str, timeout: float = 5.0) -> dict:
    deadline = asyncio.get_event_loop().time() + timeout
    last: dict = {}
    while asyncio.get_event_loop().time() < deadline:
        resp = await client.get(f"/api/v1/event-logs/{log_id}")
        assert resp.status_code == 200
        last = resp.json()
        if last["status"] == "ready":
            return last
        if last["status"] == "failed":
            raise AssertionError(f"Import failed: {last.get('error')}")
        await asyncio.sleep(0.05)
    raise AssertionError(f"Import did not finish in {timeout}s - last state: {last}")


@pytest.mark.asyncio
async def test_watched_folder_scan_dedup_and_reimport(
    admin_client: AsyncClient, tmp_path: Path
) -> None:
    src = tmp_path / "watch-src"
    src.mkdir()

    # Create a manual watch pointing at the temp dir, with a new dest folder.
    resp = await admin_client.post(
        "/api/v1/watched-folders",
        json={
            "name": "Test watch",
            "source_path": str(src),
            "mode": "manual",
            "create_dest_folder": True,
        },
    )
    assert resp.status_code == 201, resp.text
    watch = resp.json()
    wid = watch["id"]
    assert watch["dest_folder_id"], "a destination folder should have been created"

    # Drop one CSV into the source.
    shutil.copy(FIXTURES / "sample.csv", src / "log1.csv")

    # First scan imports it.
    r = await admin_client.post(f"/api/v1/watched-folders/{wid}/scan")
    assert r.status_code == 200, r.text
    first = r.json()
    assert first["found"] == 1
    assert first["imported"] == 1
    assert first["skipped"] == 0

    # The imported log lands in the destination folder and becomes ready.
    detail = (await admin_client.get(f"/api/v1/watched-folders/{wid}")).json()
    assert detail["imported_count"] == 1
    assert len(detail["files"]) == 1
    log_id = detail["files"][0]["log_id"]
    assert log_id
    log = await _wait_until_ready(admin_client, log_id)
    assert log["folder_id"] == watch["dest_folder_id"]

    # Second scan is a no-op (dedup by fingerprint).
    second = (await admin_client.post(f"/api/v1/watched-folders/{wid}/scan")).json()
    assert second["imported"] == 0
    assert second["skipped"] == 1

    # Changing the file (size bumps) re-imports it as a new log.
    with (src / "log1.csv").open("a") as f:
        f.write("\n")
    third = (await admin_client.post(f"/api/v1/watched-folders/{wid}/scan")).json()
    assert third["imported"] == 1


@pytest.mark.asyncio
async def test_watched_folder_lifecycle(admin_client: AsyncClient, tmp_path: Path) -> None:
    src = tmp_path / "watch-life"
    src.mkdir()
    resp = await admin_client.post(
        "/api/v1/watched-folders",
        json={"name": "Lifecycle", "source_path": str(src), "mode": "continuous"},
    )
    assert resp.status_code == 201, resp.text
    wid = resp.json()["id"]

    # Pause, then it shows up paused in the listing.
    patched = (
        await admin_client.patch(f"/api/v1/watched-folders/{wid}", json={"status": "paused"})
    ).json()
    assert patched["status"] == "paused"

    listing = (await admin_client.get("/api/v1/watched-folders")).json()
    assert any(w["id"] == wid and w["status"] == "paused" for w in listing)

    # Switching to interval requires a cadence.
    bad = await admin_client.patch(f"/api/v1/watched-folders/{wid}", json={"mode": "interval"})
    assert bad.status_code == 422

    # Soft-delete removes it from the listing.
    deleted = await admin_client.delete(f"/api/v1/watched-folders/{wid}")
    assert deleted.status_code == 204
    listing2 = (await admin_client.get("/api/v1/watched-folders")).json()
    assert all(w["id"] != wid for w in listing2)


@pytest.mark.asyncio
async def test_create_interval_requires_cadence(admin_client: AsyncClient, tmp_path: Path) -> None:
    resp = await admin_client.post(
        "/api/v1/watched-folders",
        json={"name": "No cadence", "source_path": str(tmp_path / "x"), "mode": "interval"},
    )
    assert resp.status_code == 422, resp.text


@pytest.fixture
async def admin_client() -> AsyncIterator[AsyncClient]:
    """The shared test user, but holding the ``admin`` realm role - custom
    watched-folder paths are admin-only."""
    from mate.api.auth.dependencies import CurrentUser, get_current_user
    from mate.api.main import create_app

    app = create_app()
    admin = CurrentUser(
        id=TEST_USER_ID,
        email=TEST_USER_EMAIL,
        preferred_username="test",
        name="Test User",
        roles=("user", "admin"),
    )

    async def _admin_user() -> CurrentUser:
        return admin

    app.dependency_overrides[get_current_user] = _admin_user
    async with (
        AsyncClient(transport=ASGITransport(app=app), base_url="http://testserver") as c,
        app.router.lifespan_context(app),
    ):
        yield c


@pytest.mark.asyncio
async def test_custom_path_requires_admin(client: AsyncClient, tmp_path: Path) -> None:
    # A plain user must not point a watch at an arbitrary path (e.g. another
    # account's data dir) - it would import those files into their account.
    resp = await client.post(
        "/api/v1/watched-folders",
        json={"name": "Snoop", "source_path": str(tmp_path), "mode": "manual"},
    )
    assert resp.status_code == 403, resp.text

    # The managed per-account folder stays available to everyone.
    managed = await client.post("/api/v1/watched-folders", json={"name": "Mine", "mode": "manual"})
    assert managed.status_code == 201, managed.text
    assert (await client.delete(f"/api/v1/watched-folders/{managed.json()['id']}")).status_code in (
        200,
        204,
    )
