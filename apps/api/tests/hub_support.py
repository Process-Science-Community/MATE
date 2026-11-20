"""Shared helpers for the dataset-hub tests (not a test module)."""

from __future__ import annotations

import hashlib
from pathlib import Path
from typing import Any
from uuid import uuid4

from httpx import AsyncClient

from .conftest import TEST_USER_ID, act_as
from .test_module_processing import import_sample, wait_until

CURATOR = TEST_USER_ID
ADMIN = ("user", "admin")
STUDENT = "00000000-0000-7000-8000-0000000000b1"
OTHER = "00000000-0000-7000-8000-0000000000b2"
REVIEWER = "00000000-0000-7000-8000-0000000000b3"
HUB_USERS = (STUDENT, OTHER, REVIEWER)
ROLES = {CURATOR: ADMIN, STUDENT: ("user",), OTHER: ("user",), REVIEWER: ("user", "reviewer")}


def uniq(prefix: str = "t") -> str:
    return f"{prefix}{uuid4().hex[:10]}"


def card(**overrides: Any) -> dict[str, Any]:
    """A card valid for every visibility (license, citation, publisher set)."""
    base: dict[str, Any] = {
        "title": "Course log",
        "summary": "Loan applications",
        "license_id": "cc-by-4.0",
        "citation": "Doe, J. (2026). Course log. 4TU.",
        "publisher_name": "TU/e",
        "visibility": "public",
    }
    base.update(overrides)
    return base


def be(state: dict[str, Any], uid: str) -> None:
    act_as(state, uid, ROLES[uid])


async def import_log(c: AsyncClient, state: dict[str, Any], uid: str = CURATOR) -> str:
    be(state, uid)
    return await import_sample(c, name=uniq("log-"))


async def publish(
    c: AsyncClient,
    state: dict[str, Any],
    log_id: str,
    *,
    ns: str | None = None,
    name: str = "log",
    expect: int = 201,
    **fields: Any,
) -> dict[str, Any]:
    be(state, CURATOR)
    body = {"source_log_id": log_id, "namespace": ns or uniq("ns-"), "name": name, **card()}
    body.update(fields)
    resp = await c.post("/api/v1/hub/datasets", json=body)
    assert resp.status_code == expect, resp.text
    return resp.json()


async def use(
    c: AsyncClient, state: dict[str, Any], uid: str, slug: str, *, new_copy: bool = False
) -> Any:
    be(state, uid)
    return await c.post(f"/api/v1/hub/datasets/{slug}/use", json={"new_copy": new_copy})


async def make_team(c: AsyncClient, state: dict[str, Any], members: tuple[str, ...]) -> str:
    be(state, CURATOR)
    resp = await c.post("/api/v1/admin/teams", json={"name": uniq("team-")})
    assert resp.status_code == 201, resp.text
    team_id = resp.json()["id"]
    for uid in members:
        r = await c.post(f"/api/v1/admin/teams/{team_id}/members", json={"user_id": uid})
        assert r.status_code == 201, r.text
    return team_id


async def run_state(c: AsyncClient, log_id: str, module_id: str) -> str | None:
    resp = await c.get(f"/api/v1/event-logs/{log_id}/module-runs")
    assert resp.status_code == 200, resp.text
    return next((s["status"] for s in resp.json() if s["module_id"] == module_id), None)


async def warm(c: AsyncClient, log_id: str, module_id: str) -> None:
    """Run a precompute module for a log (as the current user) and wait for it."""
    resp = await c.post(f"/api/v1/event-logs/{log_id}/module-runs/{module_id}")
    assert resp.status_code == 200, resp.text

    async def _ready() -> bool:
        return await run_state(c, log_id, module_id) == "ready"

    await wait_until(_ready, what=f"{module_id} to compute")


def results_dir(uid: str, log_id: str, module_id: str) -> Path:
    from mate.api.config import get_settings

    d = get_settings().module_results_dir_for(uid) / log_id / module_id
    d.mkdir(parents=True, exist_ok=True)
    return d


def revision_root(dataset_id: str) -> Path:
    from mate.api.config import get_settings

    return get_settings().datasets_dir / dataset_id / "r1"


def tree_digest(root: Path) -> dict[str, str]:
    return {
        str(p.relative_to(root)): hashlib.sha256(p.read_bytes()).hexdigest()
        for p in sorted(root.rglob("*"))
        if p.is_file()
    }


async def set_log(log_id: str, **values: Any) -> None:
    from mate.api.db.engine import get_sessionmaker
    from mate.api.db.models import EventLog

    async with get_sessionmaker()() as s:
        row = await s.get(EventLog, log_id)
        assert row is not None
        for k, v in values.items():
            setattr(row, k, v)
        await s.commit()


async def get_log(log_id: str) -> Any:
    from mate.api.db.engine import get_sessionmaker
    from mate.api.db.models import EventLog

    async with get_sessionmaker()() as s:
        return await s.get(EventLog, log_id)
