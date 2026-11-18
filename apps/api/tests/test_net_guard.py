"""SSRF guard: server-side fetches of user URLs may only reach public addresses."""

from __future__ import annotations

import pytest
from fastapi import HTTPException

from mate.api.net_guard import assert_public_url


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "url",
    [
        "http://127.0.0.1/x.csv",  # loopback
        "http://169.254.169.254/latest/meta.csv",  # cloud metadata (link-local)
        "http://10.0.0.5/a.csv",  # private
        "http://192.168.1.1/a.csv",  # private
        "http://[::1]/a.csv",  # IPv6 loopback
        "http://0.0.0.0/a.csv",  # unspecified
        "file:///etc/passwd",  # non-http scheme
        "gopher://x/a",  # non-http scheme
        "http:///a.csv",  # no host
    ],
)
async def test_internal_and_bad_urls_are_blocked(url: str) -> None:
    with pytest.raises(HTTPException) as caught:
        await assert_public_url(url)
    assert caught.value.status_code == 400


@pytest.mark.asyncio
async def test_public_ip_literal_is_allowed() -> None:
    # A literal public IP needs no DNS and must pass.
    assert await assert_public_url("https://93.184.216.34/a.csv") == ["93.184.216.34"]


@pytest.mark.asyncio
async def test_localhost_hostname_resolving_to_loopback_is_blocked() -> None:
    with pytest.raises(HTTPException):
        await assert_public_url("http://localhost/a.csv")
