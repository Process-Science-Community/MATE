"""The import wizard's archive step.

An upload holding several event logs (a BPI Challenge download, say) stages as
an archive; the user picks members, each is unpacked into a staging slot of its
own and then confirmed exactly like a single file.
"""

from __future__ import annotations

import asyncio
import gzip
import io
import struct
import tarfile
import zipfile
from pathlib import Path
from typing import Any

import pytest
from httpx import AsyncClient, Response

from .conftest import TEST_USER_ID

FIXTURES = Path(__file__).parent / "fixtures"
CSV = (FIXTURES / "sample.csv").read_bytes()
XES = (FIXTURES / "sample.xes").read_bytes()


def _staging_dir(token: str) -> Path:
    from mate.api.ingest.staging import staging_root

    return staging_root(TEST_USER_ID, token)


def _staged_dirs() -> set[Path]:
    from mate.api.config import get_settings

    root = get_settings().staging_dir_for(TEST_USER_ID)
    return set(root.iterdir()) if root.is_dir() else set()


def _bpi_zip() -> bytes:
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as zf:
        zf.writestr("BPI/", b"")
        zf.writestr("BPI/permits.csv", CSV)
        zf.writestr("BPI/declarations.xes.gz", gzip.compress(XES))
        zf.writestr("BPI/LICENSE.txt", b"CC BY 4.0")
        zf.writestr("__MACOSX/BPI/._permits.csv", b"junk")
    return buf.getvalue()


def _tar_gz(members: dict[str, bytes]) -> bytes:
    buf = io.BytesIO()
    with tarfile.open(fileobj=buf, mode="w:gz") as tf:
        for name, data in members.items():
            info = tarfile.TarInfo(name)
            info.size = len(data)
            tf.addfile(info, io.BytesIO(data))
    return buf.getvalue()


async def _stage(client: AsyncClient, filename: str, data: bytes) -> Response:
    return await client.post(
        "/api/v1/event-logs/stage",
        files={"file": (filename, data, "application/octet-stream")},
    )


async def _stage_archive(client: AsyncClient, data: bytes | None = None) -> dict[str, Any]:
    resp = await _stage(client, "BPI Challenge.zip", data or _bpi_zip())
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["kind"] == "archive"
    return body


async def _extract(client: AsyncClient, token: str, member: str) -> Response:
    return await client.post(f"/api/v1/event-logs/stage/{token}/extract", json={"member": member})


async def _wait_until_ready(
    client: AsyncClient, log_id: str, timeout: float = 5.0
) -> dict[str, Any]:
    deadline = asyncio.get_event_loop().time() + timeout
    last: dict[str, Any] = {}
    while asyncio.get_event_loop().time() < deadline:
        last = (await client.get(f"/api/v1/event-logs/{log_id}")).json()
        if last["status"] == "ready":
            return last
        if last["status"] == "failed":
            raise AssertionError(f"Import failed: {last.get('error')}")
        await asyncio.sleep(0.05)
    raise AssertionError(f"Import did not finish in {timeout}s - last state: {last}")


@pytest.mark.asyncio
async def test_multi_log_zip_stages_as_an_archive(client: AsyncClient) -> None:
    body = await _stage_archive(client)

    assert body["archive_format"] == "zip"
    assert body["filename"] == "BPI Challenge.zip"
    assert body["limit_bytes"] > 0
    members = {m["path"]: m for m in body["members"]}
    assert set(members) == {"BPI/permits.csv", "BPI/declarations.xes.gz", "BPI/LICENSE.txt"}
    assert members["BPI/permits.csv"]["importable"] is True
    assert members["BPI/permits.csv"]["format"] == "csv"
    assert members["BPI/permits.csv"]["size_bytes"] == len(CSV)
    assert members["BPI/declarations.xes.gz"]["format"] == "xes.gz"
    assert members["BPI/LICENSE.txt"]["importable"] is False
    assert members["BPI/LICENSE.txt"]["reason"] == "Not an event log."
    # Nothing is unpacked until the user picks.
    root = _staging_dir(body["staging_token"])
    assert sorted(p.name for p in root.iterdir()) == ["original.zip", "staged.json"]


@pytest.mark.asyncio
async def test_picked_members_import_as_logs_of_their_own(client: AsyncClient) -> None:
    token = (await _stage_archive(client))["staging_token"]

    csv_resp = await _extract(client, token, "BPI/permits.csv")
    assert csv_resp.status_code == 200, csv_resp.text
    csv_probe = csv_resp.json()
    assert csv_probe["kind"] == "log"
    assert csv_probe["filename"] == "permits.csv"
    assert csv_probe["source_format"] == "csv"
    assert csv_probe["quality"]["case_id"] == "exact"

    xes_resp = await _extract(client, token, "BPI/declarations.xes.gz")
    assert xes_resp.status_code == 200, xes_resp.text
    xes_probe = xes_resp.json()
    assert xes_probe["source_format"] == "xes.gz"
    assert xes_probe["events_sampled"] > 0
    # Stored the way the archive held it - still gzipped.
    assert (_staging_dir(xes_probe["staging_token"]) / "original.xes.gz").exists()

    for probe, name in ((csv_probe, "Permits"), (xes_probe, "Declarations")):
        resp = await client.post(
            "/api/v1/event-logs",
            data={"staging_token": probe["staging_token"], "name": name},
        )
        assert resp.status_code == 202, resp.text
        detail = await _wait_until_ready(client, resp.json()["log_id"])
        assert detail["name"] == name
        assert detail["source_filename"] == probe["filename"]
        assert detail["events_count"] > 0


@pytest.mark.asyncio
async def test_picking_a_member_twice_reuses_its_slot(client: AsyncClient) -> None:
    token = (await _stage_archive(client))["staging_token"]
    first = (await _extract(client, token, "BPI/permits.csv")).json()
    second = (await _extract(client, token, "BPI/permits.csv")).json()
    assert second["staging_token"] == first["staging_token"]


@pytest.mark.asyncio
async def test_an_archive_token_is_never_imported_itself(client: AsyncClient) -> None:
    token = (await _stage_archive(client))["staging_token"]
    resp = await client.post("/api/v1/event-logs", data={"staging_token": token})
    assert resp.status_code == 409
    assert _staging_dir(token).exists()


@pytest.mark.asyncio
async def test_extract_rejects_what_it_cannot_unpack(client: AsyncClient) -> None:
    token = (await _stage_archive(client))["staging_token"]

    assert (await _extract(client, token, "BPI/missing.csv")).status_code == 404
    readme = await _extract(client, token, "BPI/LICENSE.txt")
    assert readme.status_code == 422
    assert "Not an event log" in readme.json()["detail"]

    unknown = await _extract(client, "00000000-0000-7000-8000-00000000dead", "BPI/permits.csv")
    assert unknown.status_code == 404

    # A single log's token isn't an archive to extract from.
    single = (await _stage(client, "sample.csv", CSV)).json()
    assert (await _extract(client, single["staging_token"], "x")).status_code == 404


@pytest.mark.asyncio
async def test_discard_drops_a_staged_upload(client: AsyncClient) -> None:
    token = (await _stage_archive(client))["staging_token"]
    assert _staging_dir(token).exists()

    assert (await client.delete(f"/api/v1/event-logs/stage/{token}")).status_code == 204
    assert not _staging_dir(token).exists()
    # Idempotent: the wizard discards on every cancel without checking first.
    assert (await client.delete(f"/api/v1/event-logs/stage/{token}")).status_code == 204


@pytest.mark.asyncio
async def test_multi_log_tar_gz_goes_through_the_picker(client: AsyncClient) -> None:
    data = _tar_gz({"export/a.csv": CSV, "export/b.xes": XES, "export/notes.md": b"# n"})
    resp = await _stage(client, "export.tgz", data)
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["kind"] == "archive"
    assert body["archive_format"] == "tar.gz"
    importable = [m["path"] for m in body["members"] if m["importable"]]
    assert importable == ["export/a.csv", "export/b.xes"]

    probe = (await _extract(client, body["staging_token"], "export/b.xes")).json()
    assert probe["source_format"] == "xes"
    resp = await client.post("/api/v1/event-logs", data={"staging_token": probe["staging_token"]})
    assert resp.status_code == 202, resp.text
    await _wait_until_ready(client, resp.json()["log_id"])


@pytest.mark.asyncio
async def test_single_log_tar_is_unpacked_at_stage_time(client: AsyncClient) -> None:
    before = _staged_dirs()
    resp = await _stage(client, "export.tar.gz", _tar_gz({"export/log.csv": CSV, "README": b"r"}))
    assert resp.status_code == 200, resp.text
    body = resp.json()

    assert body["kind"] == "log"
    assert body["source_format"] == "csv"
    assert body["filename"] == "log.csv"
    # One new slot - the member's. The tar itself is gone.
    assert _staged_dirs() - before == {_staging_dir(body["staging_token"])}
    assert (_staging_dir(body["staging_token"]) / "original.csv").exists()


@pytest.mark.asyncio
async def test_single_log_zip_keeps_its_existing_behaviour(client: AsyncClient) -> None:
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as zf:
        zf.writestr("log.csv", CSV)
        zf.writestr("readme.txt", b"r")
    resp = await _stage(client, "data.zip", buf.getvalue())
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["kind"] == "log"
    assert body["source_format"] == "csv"
    assert (_staging_dir(body["staging_token"]) / "original.zip").exists()


@pytest.mark.asyncio
async def test_zip_bomb_is_413_and_leaves_nothing_behind(client: AsyncClient) -> None:
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as zf:
        zf.writestr("a.csv", b"x" * 100_000)
        zf.writestr("b.csv", b"y")
    data = bytearray(buf.getvalue())
    second = data.index(b"PK\x01\x02", data.index(b"PK\x01\x02") + 4)
    struct.pack_into("<L", data, second + 42, 0)  # overlap b.csv onto a.csv's data
    before = _staged_dirs()

    resp = await _stage(client, "bomb.zip", bytes(data))

    assert resp.status_code == 413
    assert "zip bomb" in resp.json()["detail"]
    assert _staged_dirs() == before


@pytest.mark.asyncio
async def test_members_share_the_archive_budget(
    client: AsyncClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    from mate.api.config import get_settings

    # Either member fits on its own; both together don't.
    monkeypatch.setattr(get_settings(), "ingest_max_decompressed_bytes", len(CSV) * 3 // 2)
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as zf:
        zf.writestr("a.csv", CSV)
        zf.writestr("b.csv", CSV)
    body = await _stage_archive(client, buf.getvalue())
    assert all(m["importable"] for m in body["members"])

    assert (await _extract(client, body["staging_token"], "a.csv")).status_code == 200
    over = await _extract(client, body["staging_token"], "b.csv")
    assert over.status_code == 413
    assert "limit" in over.json()["detail"]
