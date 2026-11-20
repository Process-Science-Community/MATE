"""Dataset takedown: a disabled/purged dataset's copies stop serving data everywhere.

The route test *discovers* every log-scoped route instead of listing them, so a
new route that bypasses the gates (``get_owned_event_log`` and the module-context
gate) fails here.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest
from fastapi.dependencies.utils import get_flat_dependant
from fastapi.routing import APIRoute

from .conftest import multi_user_client
from .hub_support import (
    CURATOR,
    HUB_USERS,
    OTHER,
    REVIEWER,
    STUDENT,
    be,
    import_log,
    make_team,
    publish,
    results_dir,
    revision_root,
    uniq,
    use,
)

pytestmark = pytest.mark.asyncio

MOD = "sample_mod"

# Exempt on purpose: the log's metadata (the web renders the "withdrawn" panel
# from it) and deleting the copy. The admin download is operator tooling behind
# the admin role (a student gets 403 before any gate).
EXEMPT = {
    ("GET", "/api/v1/event-logs/{log_id}"),
    ("DELETE", "/api/v1/event-logs/{log_id}"),
    ("GET", "/api/v1/admin/insights/event-logs/{log_id}/download"),
    # Curator-only (403 for a student before any gate); a derived log can never
    # be published, and the owner gate inside 410s a withdrawn one anyway.
    ("GET", "/api/v1/hub/publish-candidates/{log_id}"),
}


def _value(field: Any) -> Any:
    name = field.name
    if name == "module_id":
        return MOD
    if name == "field":
        return "activity"
    ann = field.field_info.annotation
    if ann is int:
        return 1
    if ann is bool:
        return "false"
    return "x"


def _log_scoped_get_routes(app: Any) -> list[tuple[str, dict[str, Any]]]:
    """``(path template, params)`` of every GET route taking a ``log_id``."""
    out: list[tuple[str, dict[str, Any]]] = []
    for route in app.routes:
        if not isinstance(route, APIRoute) or "GET" not in route.methods:
            continue
        flat = get_flat_dependant(route.dependant)
        params = flat.path_params + flat.query_params
        if "log_id" not in {p.name for p in params}:
            continue
        if ("GET", route.path) in EXEMPT:
            continue
        query = {
            p.name: _value(p)
            for p in flat.query_params
            if p.field_info.is_required() and p.name != "log_id"
        }
        out.append(
            (route.path, {"path": {p.name: _value(p) for p in flat.path_params}, "query": query})
        )
    return out


async def _derived_and_disabled(c: Any, state: dict[str, Any]) -> tuple[dict[str, Any], str]:
    log_id = await import_log(c, state)
    ds = await publish(c, state, log_id)
    new_id = (await use(c, state, STUDENT, ds["slug"])).json()["log"]["id"]
    be(state, CURATOR)
    r = await c.post(f"/api/v1/hub/datasets/{ds['slug']}/disable", json={"reason": "takedown"})
    assert r.status_code == 200, r.text
    return ds, new_id


async def test_takedown_gates_every_log_scoped_route(tmp_path: Path) -> None:
    async with multi_user_client(HUB_USERS, mods=(MOD,), tmp_path=tmp_path) as (c, state):
        _ds, new_id = await _derived_and_disabled(c, state)
        app = c._transport.app  # type: ignore[attr-defined]
        routes = _log_scoped_get_routes(app)
        paths = {p for p, _ in routes}
        # Discovery must not silently shrink.
        for must in (
            "/api/v1/event-logs/{log_id}/events",
            "/api/v1/event-logs/{log_id}/variants",
            "/api/v1/event-logs/{log_id}/activities",
            "/api/v1/event-logs/{log_id}/data-quality",
            "/api/v1/event-logs/{log_id}/module-runs",
            "/api/v1/module-outputs/{module_id}/{output_id}",
            f"/api/v1/modules/{MOD}/count",
        ):
            assert must in paths, must

        be(state, STUDENT)
        for template, params in routes:
            path_params = {**params["path"], "log_id": new_id}
            url = template.format(**path_params)
            query = dict(params["query"])
            if "{log_id}" not in template:
                query["log_id"] = new_id
            r = await c.get(url, params=query)
            assert r.status_code == 410, f"GET {template}: {r.status_code} {r.text}"

        bodies: list[tuple[str, str, dict[str, Any] | None, dict[str, Any] | None]] = [
            ("PUT", f"/api/v1/event-logs/{new_id}/active-filter", {"filter": []}, None),
            (
                "PATCH",
                f"/api/v1/event-logs/{new_id}/events/0",
                {"field": "resource", "value": "z"},
                None,
            ),
            ("POST", f"/api/v1/event-logs/{new_id}/module-runs/{MOD}", None, None),
            ("POST", f"/api/v1/event-logs/{new_id}/duplicate", None, None),
            ("POST", f"/api/v1/ai/guidance/module/{MOD}", {}, {"log_id": new_id}),
        ]
        for method, url, body, query in bodies:
            r = await c.request(method, url, json=body, params=query)
            assert r.status_code == 410, f"{method} {url}: {r.status_code} {r.text}"

        # The exemptions: metadata says why, and the copy can be deleted.
        meta = await c.get(f"/api/v1/event-logs/{new_id}")
        assert meta.status_code == 200
        assert meta.json()["source_dataset"]["status"] == "disabled"
        assert meta.json()["source_dataset"]["disabled_reason"] == "takedown"
        assert (await c.delete(f"/api/v1/event-logs/{new_id}")).status_code == 204


async def test_takedown_reaches_jobs_shared_dashboards_mcp_and_ai_chat(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from mate.api.ai_config import AiConfigPayload, ProviderConfig
    from mate.api.mcp.errors import MCPToolError
    from mate.api.mcp.toolsets import analysis, processes
    from mate.api.modules import get_module_loader
    from mate.api.routes import ai as ai_routes

    from .test_mcp_analysis import _ctx, _principal, _seed_log
    from .test_module_processing import wait_until

    async with multi_user_client(HUB_USERS, mods=(MOD, "precompute_mod"), tmp_path=tmp_path) as (
        c,
        state,
    ):
        log_id = await import_log(c, state)
        ds = await publish(c, state, log_id, title="Withdrawn course log")
        new_id = (await use(c, state, STUDENT, ds["slug"])).json()["log"]["id"]
        (results_dir(STUDENT, new_id, MOD) / "secret_result.json").write_text('{"k": "LEAK"}')
        await make_team(c, state, (STUDENT, OTHER))
        be(state, STUDENT)
        board = await c.post(
            "/api/v1/dashboards",
            json={
                "name": "b",
                "event_log_id": new_id,
                "items": [
                    {"i": "a", "module_id": MOD, "widget_id": "w", "x": 0, "y": 0, "w": 4, "h": 4}
                ],
            },
        )
        bid = board.json()["id"]
        r = await c.post(f"/api/v1/dashboards/{bid}/shares", json={"target_user_id": OTHER})
        assert r.status_code == 201, r.text
        be(state, OTHER)
        assert (
            await c.get(f"/api/v1/modules/{MOD}/count", params={"log_id": new_id})
        ).status_code == 200

        be(state, CURATOR)
        await c.post(f"/api/v1/hub/datasets/{ds['slug']}/disable", json={"reason": "x"})

        # A share recipient's module read.
        be(state, OTHER)
        r = await c.get(f"/api/v1/modules/{MOD}/count", params={"log_id": new_id})
        assert r.status_code == 410, r.text

        # Someone with no access gets the same answer as for an unknown id: a
        # 410 would disclose that a (withdrawn dataset) copy exists.
        # (The not-found path surfaces as the module's FileNotFoundError.)
        be(state, REVIEWER)
        for lid in (new_id, uniq("nolog")):
            with pytest.raises(FileNotFoundError):
                await c.get(f"/api/v1/modules/{MOD}/count", params={"log_id": lid})

        # A job that reaches the worker anyway fails in the module context.
        job_id = await get_module_loader().submit_precompute(
            "precompute_mod", "log.imported", STUDENT, new_id, {"log_id": new_id}
        )
        be(state, STUDENT)

        async def _failed() -> bool:
            return (await c.get(f"/api/v1/jobs/{job_id}")).json()["status"] == "failed"

        await wait_until(_failed, what="the job on a withdrawn log to fail")

        # MCP: gone.
        await _seed_log(new_id, STUDENT)
        with pytest.raises(MCPToolError, match=r"\[gone\]"):
            await processes.get_process(_ctx(_principal(STUDENT)), new_id)
        with pytest.raises(MCPToolError, match=r"\[gone\]"):
            await analysis.get_module_output(_ctx(_principal(STUDENT)), new_id, MOD)

        # AI chat: neither the cached results nor the log's name reach the prompt.
        cfg = AiConfigPayload(
            anthropic=ProviderConfig(api_key="k"),
            selected_provider="anthropic",
            selected_model="m",
            allow_process_data=True,
        )
        prompts: list[str] = []

        async def _load(*_a: Any, **_k: Any) -> AiConfigPayload:
            return cfg

        async def _capture(cfg_: AiConfigPayload, _messages: Any) -> Any:
            prompts.append(cfg_.system_prompt)
            yield "data: {}\n\n"

        monkeypatch.setattr(ai_routes, "load_ai_config", _load)
        monkeypatch.setattr(ai_routes, "_stream_chat", _capture)
        r = await c.post(
            "/api/v1/ai/chat",
            json={
                "messages": [{"role": "user", "content": "hi"}],
                "context": {"log_id": new_id, "module_ids": [MOD]},
            },
        )
        assert r.status_code == 200, r.text
        assert prompts and "LEAK" not in prompts[0]
        assert "Activities (" not in prompts[0]
        assert "Withdrawn course log" not in prompts[0]
        assert new_id not in prompts[0]


async def test_enable_restores_access_and_purge_blocks_permanently(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from mate.api.storage import sync as storage_sync

    remote_deleted: list[Path] = []
    real = storage_sync.delete_dir_remote_sync

    def _spy(local_dir: Path) -> None:
        remote_deleted.append(local_dir)
        real(local_dir)

    monkeypatch.setattr(storage_sync, "delete_dir_remote_sync", _spy)

    async with multi_user_client(HUB_USERS) as (c, state):
        log_id = await import_log(c, state)
        ds = await publish(c, state, log_id)
        slug = ds["slug"]
        new_id = (await use(c, state, STUDENT, slug)).json()["log"]["id"]
        variants = f"/api/v1/event-logs/{new_id}/variants"

        be(state, CURATOR)
        # Purge needs a disabled dataset and the typed slug.
        assert (
            await c.post(f"/api/v1/hub/datasets/{slug}/purge", json={"confirm": slug})
        ).status_code == 409
        await c.post(f"/api/v1/hub/datasets/{slug}/disable", json={"reason": "x"})
        be(state, STUDENT)
        assert (await c.get(variants)).status_code == 410

        be(state, CURATOR)
        assert (await c.post(f"/api/v1/hub/datasets/{slug}/enable")).status_code == 200
        be(state, STUDENT)
        assert (await c.get(variants)).status_code == 200

        be(state, CURATOR)
        await c.post(f"/api/v1/hub/datasets/{slug}/disable", json={"reason": "x"})
        assert (
            await c.post(f"/api/v1/hub/datasets/{slug}/purge", json={"confirm": "nope"})
        ).status_code == 422
        root = revision_root(ds["id"])
        assert root.exists()

        # A purge that cannot delete the files records nothing.
        from mate.api.hub import storage as hub_storage

        def _fail(*_a: Any, **_k: Any) -> None:
            raise OSError("busy")

        with monkeypatch.context() as m:
            m.setattr(hub_storage.shutil, "rmtree", _fail)
            r = await c.post(f"/api/v1/hub/datasets/{slug}/purge", json={"confirm": slug})
        assert r.status_code == 500, r.text
        assert root.exists()
        detail = (await c.get(f"/api/v1/hub/datasets/{slug}")).json()
        assert detail["revision"]["purged"] is False

        r = await c.post(f"/api/v1/hub/datasets/{slug}/purge", json={"confirm": slug})
        assert r.status_code == 200 and r.json()["revision"]["purged"] is True
        assert not root.exists()
        assert root in remote_deleted

        be(state, STUDENT)
        assert (await c.get(variants)).status_code == 410
        meta = (await c.get(f"/api/v1/event-logs/{new_id}")).json()
        assert meta["source_dataset"]["status"] == "purged"

        be(state, CURATOR)
        assert (await c.post(f"/api/v1/hub/datasets/{slug}/enable")).status_code == 409
