"""MODULE_UPLOAD_ENABLED switches the in-platform module upload off.

Uploaded module code is not sandboxed (it runs inside the API with every
account's data), so shared instances keep it off; modules ship by reviewed
pull request, and authors run unpublished modules locally in lite mode.
"""

from __future__ import annotations

from collections.abc import Iterator

import pytest
from httpx import AsyncClient


def _set(monkeypatch: pytest.MonkeyPatch, value: str) -> None:
    from mate.api import config as cfg

    monkeypatch.setenv("MODULE_UPLOAD_ENABLED", value)
    cfg.get_settings.cache_clear()


@pytest.fixture
def _restore_settings() -> Iterator[None]:
    yield
    from mate.api import config as cfg

    cfg.get_settings.cache_clear()


def test_upload_is_off_by_default(monkeypatch: pytest.MonkeyPatch, _restore_settings: None) -> None:
    from mate.api.config import Settings

    monkeypatch.delenv("MODULE_UPLOAD_ENABLED", raising=False)
    assert Settings().module_upload_enabled is False


@pytest.mark.asyncio
async def test_disabled_upload_is_refused(
    client: AsyncClient, monkeypatch: pytest.MonkeyPatch, _restore_settings: None
) -> None:
    _set(monkeypatch, "false")

    status = await client.get("/api/v1/modules/install/status")
    assert status.status_code == 200
    body = status.json()
    assert body["enabled"] is False
    assert "pull request" in body["detail"]

    resp = await client.post(
        "/api/v1/modules/install",
        files={"file": ("evil.zip", b"PK\x05\x06" + b"\x00" * 18, "application/zip")},
    )
    assert resp.status_code == 403, resp.text
    assert "turned off" in resp.json()["detail"]


@pytest.mark.asyncio
async def test_enabled_status(
    client: AsyncClient, monkeypatch: pytest.MonkeyPatch, _restore_settings: None
) -> None:
    _set(monkeypatch, "true")
    body = (await client.get("/api/v1/modules/install/status")).json()
    assert body == {"enabled": True, "detail": None}
