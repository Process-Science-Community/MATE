"""Import-from-URL downloads: streamed to disk, capped, never inflated in memory.

httpx transparently undoes a `Content-Encoding`, so a hostile server could
otherwise answer with a few KB of gzip that expand to gigabytes of RAM.
"""

from __future__ import annotations

import gzip
from collections.abc import Callable
from pathlib import Path
from typing import Any

import httpx
import pytest
from fastapi import HTTPException

from mate.api.services import log_aggregates

_RealAsyncClient = httpx.AsyncClient


def _serve(
    monkeypatch: pytest.MonkeyPatch, handler: Callable[[httpx.Request], httpx.Response]
) -> None:
    def client(**kwargs: Any) -> httpx.AsyncClient:
        return _RealAsyncClient(transport=httpx.MockTransport(handler), **kwargs)

    monkeypatch.setattr(log_aggregates.httpx, "AsyncClient", client)


@pytest.mark.asyncio
async def test_gzip_encoded_download_is_stored_raw(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    bomb = gzip.compress(bytes(16 * 1024 * 1024))  # 16 MiB of zeros, ~16 KB on the wire

    def handler(request: httpx.Request) -> httpx.Response:
        assert request.headers["accept-encoding"] == "identity"
        return httpx.Response(
            200, stream=httpx.ByteStream(bomb), headers={"content-encoding": "gzip"}
        )

    _serve(monkeypatch, handler)
    dest = tmp_path / "original.csv"
    await log_aggregates._download("https://logs.example/log.csv", dest)  # pyright: ignore[reportPrivateUsage]

    # Still compressed: the importer unpacks it later, behind the zip-bomb guards.
    assert dest.read_bytes() == bomb


@pytest.mark.asyncio
async def test_download_is_capped(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    from mate.api.config import get_settings

    monkeypatch.setattr(get_settings(), "ingest_max_decompressed_bytes", 1024)
    _serve(monkeypatch, lambda _request: httpx.Response(200, stream=httpx.ByteStream(b"x" * 4096)))

    with pytest.raises(HTTPException) as caught:
        await log_aggregates._download("https://logs.example/log.csv", tmp_path / "o.csv")  # pyright: ignore[reportPrivateUsage]
    assert caught.value.status_code == 413
    assert len((tmp_path / "o.csv").read_bytes()) <= 1024


@pytest.mark.asyncio
async def test_undecodable_encoding_and_http_errors_are_refused(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _serve(
        monkeypatch,
        lambda _request: httpx.Response(200, content=b"\x1b", headers={"content-encoding": "br"}),
    )
    with pytest.raises(HTTPException) as caught:
        await log_aggregates._download("https://logs.example/log.csv", tmp_path / "a.csv")  # pyright: ignore[reportPrivateUsage]
    assert caught.value.status_code == 415

    _serve(monkeypatch, lambda _request: httpx.Response(404))
    with pytest.raises(HTTPException) as caught:
        await log_aggregates._download("https://logs.example/log.csv", tmp_path / "b.csv")  # pyright: ignore[reportPrivateUsage]
    assert caught.value.status_code == 400
