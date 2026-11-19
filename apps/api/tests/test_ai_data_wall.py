"""MATE AI data wall.

The chat assistant only ever sees module *outputs* and curated *aggregate*
metadata - never raw XES/parquet event rows. Coverage:

* ``_RestrictedEventLog`` denies every raw accessor.
* ``_make_context(restrict_event_log=True)`` wires that stub while the module
  cache (its own outputs) still works; the default path keeps full access.
* ``_activities_variants_block`` surfaces aggregate activities/variants only.
"""

from __future__ import annotations

import asyncio
from pathlib import Path

import pytest
from httpx import AsyncClient

from .conftest import TEST_USER_ID

FIXTURES = Path(__file__).parent / "fixtures"


async def _seed_log(client: AsyncClient, timeout: float = 10.0) -> str:
    with (FIXTURES / "sample.csv").open("rb") as f:
        resp = await client.post(
            "/api/v1/event-logs",
            files={"file": ("sample.csv", f, "text/csv")},
            data={"name": "Sample CSV"},
        )
    log_id = resp.json()["log_id"]
    deadline = asyncio.get_event_loop().time() + timeout
    while asyncio.get_event_loop().time() < deadline:
        r = await client.get(f"/api/v1/event-logs/{log_id}")
        status = r.json()["status"]
        if status == "ready":
            return log_id
        if status == "failed":
            raise AssertionError(f"import failed: {r.json()}")
        await asyncio.sleep(0.05)
    raise AssertionError("import did not finish in time")


@pytest.mark.asyncio
async def test_restricted_event_log_denies_all_raw_access() -> None:
    from mate.api.modules.loader import _RestrictedEventLog

    el = _RestrictedEventLog()

    for coro in (
        el.pandas(),
        el.polars(),
        el.pm4py(),
        el.duckdb_fetch("SELECT 1"),
        el.materialize_parquet(),
    ):
        with pytest.raises(PermissionError):
            await coro

    for prop in ("events_path", "cases_path", "active_filter", "some_future_reader"):
        with pytest.raises(PermissionError):
            getattr(el, prop)

    # Entering it as an async context manager raises too.
    with pytest.raises(PermissionError):
        async with el:
            pass


@pytest.mark.asyncio
async def test_activities_variants_block_is_aggregate_only(client: AsyncClient) -> None:
    from mate.api.routes.ai import _activities_variants_block

    log_id = await _seed_log(client)
    block = await _activities_variants_block(log_id, TEST_USER_ID, None)

    assert block
    # sample.csv activities surface with their event counts.
    assert "register order" in block
    assert "Activities (" in block
    # Variant sequences appear as activity paths with case counts.
    assert "Top variants" in block
    assert "→" in block
    assert "cases:" in block


@pytest.mark.asyncio
async def test_make_context_restrict_flag(client_with_sample_mod: AsyncClient) -> None:
    from mate.api.modules import get_module_loader
    from mate.api.modules.event_log_access import EventLogAccess
    from mate.api.modules.loader import _RestrictedEventLog

    log_id = await _seed_log(client_with_sample_mod)
    loader = get_module_loader()
    mod_id = next(iter(loader.loaded))

    restricted = await loader._make_context(mod_id, log_id, TEST_USER_ID, restrict_event_log=True)
    assert isinstance(restricted.event_log, _RestrictedEventLog)
    with pytest.raises(PermissionError):
        await restricted.event_log.pandas()
    # The module's own cache still works under the wall.
    assert await restricted.cache.exists("missing-key") is False

    # Default (non-AI) path keeps full raw access.
    full = await loader._make_context(mod_id, log_id, TEST_USER_ID)
    assert isinstance(full.event_log, EventLogAccess)


# --------------------------------------------------------------------------- #
# Dataset license: a copy whose dataset forbids external AI never reaches it
# --------------------------------------------------------------------------- #


@pytest.mark.asyncio
async def test_no_egress_derived_log_never_reaches_external_ai(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from typing import Any

    from mate.api.ai_config import AiConfigPayload, ProviderConfig
    from mate.api.ai_nav import list_user_processes
    from mate.api.db.engine import get_sessionmaker
    from mate.api.modules import get_module_loader
    from mate.api.routes import ai as ai_routes

    from .conftest import multi_user_client
    from .hub_support import CURATOR, HUB_USERS, STUDENT, be, import_log, publish, use

    mod = "sample_mod"
    async with multi_user_client(HUB_USERS, mods=(mod,), tmp_path=tmp_path) as (c, st):
        log_id = await import_log(c, st)
        ds = await publish(c, st, log_id, title="No-egress course log", allow_external_ai=False)
        new_id = (await use(c, st, STUDENT, ds["slug"])).json()["log"]["id"]
        be(st, STUDENT)

        for url, params in (
            (f"/api/v1/ai/guidance/module/{mod}", {"log_id": new_id}),
            (f"/api/v1/ai/guidance/module/{mod}/stream", {"log_id": new_id}),
            (f"/api/v1/ai/guidance/process/{new_id}", None),
            (f"/api/v1/ai/guidance/import/quality/{new_id}", None),
        ):
            r = await c.post(url, json={}, params=params)
            assert r.status_code == 403, f"{url}: {r.status_code} {r.text}"
        # Clearing stored guidance sends nothing anywhere and stays allowed.
        r = await c.delete(f"/api/v1/ai/guidance/module/{mod}", params={"log_id": new_id})
        assert r.status_code == 200, r.text

        async def listed() -> set[str]:
            async with get_sessionmaker()() as s:
                return {p.id for p in await list_user_processes(s, STUDENT)}

        assert new_id not in await listed()

        # Chat: a module whose curated payload would carry the log's results.
        instance = get_module_loader().loaded[mod].instance
        monkeypatch.setattr(
            instance, "guidance_payload", lambda ctx: {"k": "RESULT-LEAK"}, raising=False
        )
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

        async def chat() -> str:
            r = await c.post(
                "/api/v1/ai/chat",
                json={
                    "messages": [{"role": "user", "content": "hi"}],
                    "context": {"log_id": new_id, "module_ids": [mod]},
                },
            )
            assert r.status_code == 200, r.text
            return prompts[-1]

        prompt = await chat()
        assert "RESULT-LEAK" not in prompt
        assert "No-egress course log" not in prompt
        assert "Activities (" not in prompt

        # The flag is read live: allowing it applies to the existing copy.
        be(st, CURATOR)
        r = await c.patch(f"/api/v1/hub/datasets/{ds['slug']}", json={"allow_external_ai": True})
        assert r.status_code == 200 and r.json()["allow_external_ai"] is True
        be(st, STUDENT)
        assert new_id in await listed()
        prompt = await chat()
        assert "RESULT-LEAK" in prompt
        assert "No-egress course log" in prompt


@pytest.mark.asyncio
async def test_ai_card_module_refused_on_no_egress_log(
    admin_client_with_sample_cards: AsyncClient,
) -> None:
    from uuid import uuid4

    c = admin_client_with_sample_cards
    log_id = await _seed_log(c)
    body = {
        "source_log_id": log_id,
        "namespace": f"ns-{uuid4().hex[:8]}",
        "name": "log",
        "title": "No-egress",
        "allow_external_ai": False,
    }
    r = await c.post("/api/v1/hub/datasets", json=body)
    assert r.status_code == 201, r.text
    slug = r.json()["slug"]
    new_id = (await c.post(f"/api/v1/hub/datasets/{slug}/use", json={})).json()["log"]["id"]

    # The AI-card module would send the log's data to a provider: refused.
    r = await c.get("/api/v1/modules/sample_cards/echo-config", params={"log_id": new_id})
    assert r.status_code == 403, r.text
    # The same module on the curator's own (ordinary) log is fine.
    r = await c.get("/api/v1/modules/sample_cards/echo-config", params={"log_id": log_id})
    assert r.status_code == 200, r.text
