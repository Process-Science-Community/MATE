"""Dataset hub: publish, catalog, copy-on-use and the isolation rules around them.

Every test drives the real HTTP surface with several identities on one app
(``multi_user_client``): the curator (admin), a team member, a non-member and a
sandboxed reviewer.
"""

from __future__ import annotations

import os
from pathlib import Path
from typing import Any

import pytest

from .conftest import multi_user_client
from .hub_support import (
    ADMIN,
    CURATOR,
    HUB_USERS,
    OTHER,
    REVIEWER,
    STUDENT,
    be,
    get_log,
    import_log,
    make_team,
    publish,
    results_dir,
    revision_root,
    set_log,
    tree_digest,
    uniq,
    use,
    warm,
)

pytestmark = pytest.mark.asyncio


def _readable(vis: str, lic: str, viewer: str) -> bool:
    """The access rule, stated independently of the implementation."""
    if viewer == CURATOR:
        return True
    if viewer == REVIEWER:
        return vis == "public" and lic == "cc-by-4.0"
    if viewer == STUDENT:  # team member
        return vis in ("public", "restricted")
    return vis == "public"  # OTHER: not a member


# ── visibility ─────────────────────────────────────────────────────────────


async def test_visibility_matrix() -> None:
    """{private, restricted, public} x {curator, member, non-member, reviewer} x
    {open, 4TU} - list membership and detail status follow one rule. One app for
    all 24 cells (the cells only differ in data, not setup)."""
    async with multi_user_client(HUB_USERS) as (c, state):
        log_id = await import_log(c, state)
        team = await make_team(c, state, (STUDENT,))
        token = uniq("vm")
        slugs: dict[tuple[str, str], str] = {}
        for vis in ("private", "restricted", "public"):
            for lic in ("cc-by-4.0", "4tu-general-terms"):
                ds = await publish(
                    c,
                    state,
                    log_id,
                    visibility=vis,
                    license_id=lic,
                    summary=token,
                    audience_team_id=team if vis == "restricted" else None,
                )
                slugs[(vis, lic)] = ds["slug"]

        for viewer in (CURATOR, STUDENT, OTHER, REVIEWER):
            be(state, viewer)
            listed = await c.get("/api/v1/hub/datasets", params={"q": token, "limit": 50})
            assert listed.status_code == 200, listed.text
            listed_slugs = {d["slug"] for d in listed.json()["items"]}
            for (vis, lic), slug in slugs.items():
                want = _readable(vis, lic, viewer)
                cell = f"{vis}/{lic}/{viewer[-2:]}"
                assert (slug in listed_slugs) is want, cell
                detail = await c.get(f"/api/v1/hub/datasets/{slug}")
                assert detail.status_code == (200 if want else 404), cell
            assert listed.json()["total"] == len(listed_slugs)


async def test_restricted_dataset_unreadable_when_team_soft_deleted() -> None:
    async with multi_user_client(HUB_USERS) as (c, state):
        log_id = await import_log(c, state)
        team = await make_team(c, state, (STUDENT,))
        ds = await publish(c, state, log_id, visibility="restricted", audience_team_id=team)
        be(state, STUDENT)
        assert (await c.get(f"/api/v1/hub/datasets/{ds['slug']}")).status_code == 200

        be(state, CURATOR)
        assert (await c.delete(f"/api/v1/admin/teams/{team}")).status_code == 204
        be(state, STUDENT)
        assert (await c.get(f"/api/v1/hub/datasets/{ds['slug']}")).status_code == 404
        assert (await use(c, state, STUDENT, ds["slug"])).status_code == 404


async def test_disabled_dataset_hidden_from_non_curators_visible_to_curator() -> None:
    async with multi_user_client(HUB_USERS) as (c, state):
        log_id = await import_log(c, state)
        token = uniq("dis")
        ds = await publish(c, state, log_id, summary=token)
        r = await c.post(f"/api/v1/hub/datasets/{ds['slug']}/disable", json={"reason": "GDPR"})
        assert r.status_code == 200 and r.json()["status"] == "disabled"

        be(state, STUDENT)
        assert (await c.get(f"/api/v1/hub/datasets/{ds['slug']}")).status_code == 404
        listed = await c.get("/api/v1/hub/datasets", params={"q": token})
        assert listed.json()["items"] == []

        be(state, CURATOR)
        detail = await c.get(f"/api/v1/hub/datasets/{ds['slug']}")
        assert detail.status_code == 200
        assert detail.json()["disabled_reason"] == "GDPR"


# ── publish ────────────────────────────────────────────────────────────────


@pytest.mark.parametrize(
    ("roles", "expect"),
    [(("user",), 403), (("admin", "reviewer"), 403), (("admin",), 201)],
)
async def test_only_non_sandboxed_admin_can_publish(roles: tuple[str, ...], expect: int) -> None:
    from .conftest import act_as

    async with multi_user_client(HUB_USERS) as (c, state):
        log_id = await import_log(c, state)
        act_as(state, CURATOR, roles)
        body = {"source_log_id": log_id, "namespace": uniq("ns-"), "name": "log", "title": "T"}
        resp = await c.post("/api/v1/hub/datasets", json=body)
        assert resp.status_code == expect, resp.text
        status = (await c.get("/api/v1/hub/status")).json()
        assert status == {"enabled": True, "can_curate": expect == 201}


@pytest.mark.parametrize(
    ("fields", "expect"),
    [
        ({"license_id": "unknown"}, 422),
        ({"citation": "  "}, 422),
        ({"publisher_name": None}, 422),
        ({"license_id": "other", "license_name": "Mine"}, 422),
        ({"license_id": "no-such-license", "visibility": "private"}, 422),
        ({"visibility": "restricted", "audience_team_id": "no-such-team"}, 422),
        ({"name": "Bad_Slug"}, 422),
        ({"tags": ["UPPER CASE!"]}, 422),
        ({"source_doi": "doi.org/10.4121/1"}, 422),
        # Rendered as href: absolute http(s) only.
        ({"source_url": "javascript:alert(1)"}, 422),
        ({"publisher_url": "www.4tu.nl"}, 422),
        ({"license_id": "other", "license_name": "Mine", "license_url": "ftp://x/y"}, 422),
        # Private drafts may stay incomplete.
        ({"license_id": "unknown", "citation": None, "visibility": "private"}, 201),
    ],
)
async def test_publish_rejects_invalid_cards(fields: dict[str, Any], expect: int) -> None:
    async with multi_user_client(HUB_USERS) as (c, state):
        log_id = await import_log(c, state)
        await publish(c, state, log_id, expect=expect, **fields)


async def test_publish_rejects_duplicate_slug() -> None:
    async with multi_user_client(HUB_USERS) as (c, state):
        log_id = await import_log(c, state)
        ns = uniq("ns-")
        await publish(c, state, log_id, ns=ns)
        r = await publish(c, state, log_id, ns=ns, expect=409)
        assert r["detail"] == "Dataset slug already exists."


@pytest.mark.parametrize("case", ["foreign", "filter", "ocel", "derived", "importing"])
async def test_publish_refuses_bad_sources(case: str) -> None:
    async with multi_user_client(HUB_USERS) as (c, state):
        if case == "foreign":
            log_id = await import_log(c, state, STUDENT)
            await publish(c, state, log_id, expect=404)
            return
        log_id = await import_log(c, state)
        if case == "filter":
            r = await c.put(
                f"/api/v1/event-logs/{log_id}/active-filter",
                json={"filter": [{"field": "activity", "op": "equals", "value": "ship"}]},
            )
            assert r.status_code == 200, r.text
        elif case == "ocel":
            await set_log(log_id, log_model="object_centric")
        elif case == "importing":
            await set_log(log_id, status="importing")
        elif case == "derived":
            ds = await publish(c, state, log_id)
            r = await use(c, state, CURATOR, ds["slug"])
            log_id = r.json()["log"]["id"]
        await publish(c, state, log_id, expect=409)


async def test_publish_leaves_source_log_untouched(tmp_path: Path) -> None:
    async with multi_user_client(HUB_USERS, mods=("precompute_mod",), tmp_path=tmp_path) as (
        c,
        state,
    ):
        from mate.api.ingest.storage import log_paths

        log_id = await import_log(c, state)
        await warm(c, log_id, "precompute_mod")
        (results_dir(CURATOR, log_id, "precompute_mod") / "summary.json").write_text("{}")
        paths = log_paths(log_id, CURATOR)
        results_root = results_dir(CURATOR, log_id, "precompute_mod").parent

        def snapshot(row: Any) -> Any:
            return (
                tree_digest(paths.root),
                tree_digest(results_root),
                row.data_revision,
                row.active_filter,
                row.column_roles,
            )

        before = snapshot(await get_log(log_id))
        await publish(c, state, log_id)
        assert snapshot(await get_log(log_id)) == before


async def test_publish_snapshots_only_parquet_and_fresh_whitelisted_results(
    tmp_path: Path,
) -> None:
    from mate.api.db.engine import get_sessionmaker
    from mate.api.db.models import LogModuleRun
    from mate.api.ingest.storage import log_paths

    async with multi_user_client(
        HUB_USERS, mods=("precompute_mod", "eager_mod", "sample_mod"), tmp_path=tmp_path
    ) as (c, state):
        log_id = await import_log(c, state)
        await warm(c, log_id, "precompute_mod")
        await warm(c, log_id, "eager_mod")
        fresh = results_dir(CURATOR, log_id, "precompute_mod")
        (fresh / "summary.json").write_text('{"n": 1}')
        (fresh / "__ai_guidance.json").write_text("{}")
        (fresh / "_v_deadbeef").mkdir()
        (fresh / "_v_deadbeef" / "summary.json").write_text("{}")
        (results_dir(CURATOR, log_id, "eager_mod") / "summary.json").write_text("{}")
        # An on-demand module's result never ships: mtime freshness misses filter
        # commits, and some write results computed against ANOTHER log here
        # (pcomp/process_comparison) - shipping them leaks the curator's other logs.
        (results_dir(CURATOR, log_id, "sample_mod") / "vs_other_log.json").write_text(
            '{"other_log_id": "private"}'
        )
        # A module sidecar dir in the log root (as conformance/CDE write).
        (log_paths(log_id, CURATOR).root / "conformance_models").mkdir()
        (log_paths(log_id, CURATOR).root / "conformance_models" / "m.pnml").write_text("x")
        # eager_mod's run is for older data: stale, so not canonical.
        async with get_sessionmaker()() as s:
            run = await s.get(LogModuleRun, (log_id, "eager_mod"))
            assert run is not None
            run.data_revision = -1
            await s.commit()

        bad = await publish(c, state, log_id, modules=["eager_mod"], expect=422)
        assert "eager_mod" in bad["detail"]
        bad = await publish(c, state, log_id, modules=["sample_mod"], expect=422)
        assert "precompute on import" in bad["detail"]

        # The dialog lists exactly what a default publish ships.
        be(state, CURATOR)
        cand = await c.get(f"/api/v1/hub/publish-candidates/{log_id}")
        assert cand.status_code == 200, cand.text
        assert [(m["module_id"], m["config_is_default"]) for m in cand.json()] == [
            ("precompute_mod", True)
        ]

        ds = await publish(c, state, log_id)
        assert ds["revision"]["canonical_modules"] == ["precompute_mod"]
        assert ds["required_modules"] == ["precompute_mod"]
        tree = sorted(tree_digest(revision_root(ds["id"])))
        assert tree == [
            "data/cases.parquet",
            "data/events.parquet",
            "module_results/precompute_mod/summary.json",
        ]


async def test_publish_records_stats_and_preview() -> None:
    async with multi_user_client(HUB_USERS) as (c, state):
        log_id = await import_log(c, state)
        src = (await c.get(f"/api/v1/event-logs/{log_id}")).json()
        activities = (await c.get(f"/api/v1/event-logs/{log_id}/activities")).json()
        ds = await publish(c, state, log_id)
        rev = ds["revision"]
        assert (rev["events_count"], rev["cases_count"], rev["variants_count"]) == (
            src["events_count"],
            src["cases_count"],
            src["variants_count"],
        )
        assert rev["activities_count"] == activities["total"]
        counts = [a["count"] for a in rev["preview"]["top_activities"]]
        assert counts == sorted(counts, reverse=True) and counts
        assert 0 < len(rev["preview"]["rows"]["data"]) <= 50
        assert "activity" in rev["preview"]["rows"]["columns"]
        assert {c["name"] for c in rev["stats"]["columns"]} >= {"case_id", "activity"}
        assert rev["stats"]["file_format"] == src["source_format"]
        assert rev["bytes_parquet"] > 0


# ── catalog ────────────────────────────────────────────────────────────────


async def test_catalog_search_escapes_like_and_matches_tags() -> None:
    async with multi_user_client(HUB_USERS) as (c, state):
        log_id = await import_log(c, state)
        tok = uniq("cs")
        made = {}
        for key, fields in {
            "pct": {"title": f"{tok} sale 50% off"},
            "plain": {"title": f"{tok} sale 500 off"},
            "under": {"title": f"{tok} a_b"},
            "axb": {"title": f"{tok} axb"},
            "tag": {"title": f"{tok} tagged", "tags": ["ocean freight"]},
            "nc": {"title": f"{tok} nc", "license_id": "cc-by-nc-4.0"},
        }.items():
            made[key] = (await publish(c, state, log_id, **fields))["slug"]

        async def search(**params: Any) -> list[str]:
            be(state, STUDENT)
            r = await c.get("/api/v1/hub/datasets", params=params)
            assert r.status_code == 200, r.text
            return [d["slug"] for d in r.json()["items"]]

        assert await search(q=f"{tok} 50%") == [made["pct"]]
        assert await search(q=f"{tok} a_b") == [made["under"]]
        assert await search(q=f"{tok} OCEAN fre") == [made["tag"]]
        assert await search(q=f"{tok} sale off") == [made["plain"], made["pct"]]
        assert await search(q=tok, license="cc-by-nc-4.0") == [made["nc"]]

        # The namespace page lists what the viewer may read there, nothing else:
        # a private dataset in the same namespace is as invisible as an unknown
        # namespace.
        ns = uniq("ns-")
        shown = (await publish(c, state, log_id, ns=ns, name="open"))["slug"]
        await publish(c, state, log_id, ns=ns, name="draft", visibility="private")
        assert await search(namespace=ns) == [shown]
        assert await search(namespace=uniq("ns-")) == []

        # sort=uses counts distinct users (a second copy by one user is not a use).
        assert (await use(c, state, STUDENT, made["axb"])).status_code == 201
        assert (await use(c, state, OTHER, made["axb"])).status_code == 201
        assert (await use(c, state, STUDENT, made["tag"])).status_code == 201
        assert (await use(c, state, STUDENT, made["tag"], new_copy=True)).status_code == 201
        be(state, STUDENT)
        r = await c.get("/api/v1/hub/datasets", params={"q": tok, "sort": "uses"})
        items = r.json()["items"]
        assert [d["slug"] for d in items[:2]] == [made["axb"], made["tag"]]
        assert [d["uses"] for d in items[:2]] == [2, 1]
        assert (await c.get("/api/v1/hub/datasets", params={"sort": "name"})).status_code == 422


# ── use (copy-on-use) ──────────────────────────────────────────────────────


async def test_use_is_idempotent_and_add_another_copy() -> None:
    async with multi_user_client(HUB_USERS) as (c, state):
        log_id = await import_log(c, state)
        ds = await publish(c, state, log_id, title="Course case study")
        first = await use(c, state, STUDENT, ds["slug"])
        first_id = first.json()["log"]["id"]
        assert first.status_code == 201 and first.json()["created"] is True
        again = await use(c, state, STUDENT, ds["slug"])
        assert again.status_code == 200 and again.json()["created"] is False
        assert again.json()["log"]["id"] == first_id

        # A duplicate of the copy (which the student may have edited) is never
        # handed back as "the" copy, although it is newer and keeps the lineage.
        be(state, STUDENT)
        dup = await c.post(f"/api/v1/event-logs/{first_id}/duplicate")
        assert dup.status_code == 201, dup.text
        assert (await use(c, state, STUDENT, ds["slug"])).json()["log"]["id"] == first_id
        assert (await c.delete(f"/api/v1/event-logs/{dup.json()['id']}")).status_code == 204

        async def another() -> dict[str, Any]:
            r = await use(c, state, STUDENT, ds["slug"], new_copy=True)
            assert r.status_code == 201, r.text
            return r.json()["log"]

        second, third = await another(), await another()
        assert (second["name"], third["name"]) == ("Course case study (2)", "Course case study (3)")
        detail = (await c.get(f"/api/v1/hub/datasets/{ds['slug']}")).json()
        assert [x["id"] for x in detail["viewer"]["my_logs"]] == [
            third["id"],
            second["id"],
            first_id,
        ]

        # Names stay unique when an earlier copy was deleted (the gap is reused).
        assert (await c.delete(f"/api/v1/event-logs/{second['id']}")).status_code == 204
        assert (await another())["name"] == "Course case study (2)"

        # Deleted copies are not handed back.
        be(state, STUDENT)
        for lid in [
            x["id"]
            for x in (await c.get(f"/api/v1/hub/datasets/{ds['slug']}")).json()["viewer"]["my_logs"]
        ]:
            assert (await c.delete(f"/api/v1/event-logs/{lid}")).status_code == 204
        fresh = await use(c, state, STUDENT, ds["slug"])
        assert fresh.status_code == 201


async def test_use_refused_over_storage_quota(monkeypatch: pytest.MonkeyPatch) -> None:
    """S3 quota: "use" writes ~Parquet size per student, so it obeys the same 507
    as an upload; an existing copy is still handed back."""
    from mate.api.hub import service

    async with multi_user_client(HUB_USERS) as (c, state):
        log_id = await import_log(c, state)
        ds = await publish(c, state, log_id)
        first = await use(c, state, STUDENT, ds["slug"])
        monkeypatch.setattr(service, "over_quota_sync", lambda: True)
        assert (await use(c, state, STUDENT, ds["slug"])).status_code == 200
        r = await use(c, state, STUDENT, ds["slug"], new_copy=True)
        assert r.status_code == 507, r.text
        be(state, STUDENT)
        detail = (await c.get(f"/api/v1/hub/datasets/{ds['slug']}")).json()
        assert [x["id"] for x in detail["viewer"]["my_logs"]] == [first.json()["log"]["id"]]


async def test_use_creates_ready_log_with_snapshot_and_lineage() -> None:
    from mate.api.db.engine import get_sessionmaker
    from mate.api.db.models import HubDatasetRevision

    async with multi_user_client(HUB_USERS) as (c, state):
        log_id = await import_log(c, state)
        ds = await publish(c, state, log_id)
        r = await use(c, state, STUDENT, ds["slug"])
        new_id = r.json()["log"]["id"]
        row = await get_log(new_id)
        async with get_sessionmaker()() as s:
            rev = (
                await s.execute(
                    __import__("sqlalchemy")
                    .select(HubDatasetRevision)
                    .where(HubDatasetRevision.dataset_id == ds["id"])
                )
            ).scalar_one()
        assert (row.user_id, row.status, row.data_revision) == (STUDENT, "ready", 0)
        assert row.dataset_revision_id == rev.id
        assert row.column_roles == rev.column_roles and row.column_roles
        assert row.detected_schema == rev.detected_schema
        assert row.column_overrides == rev.column_overrides
        assert row.license_snapshot["slug"] == ds["slug"]
        assert row.license_snapshot["citation"] == ds["citation"]
        assert row.source_filename == f"{ds['slug']}@r1"

        be(state, STUDENT)
        listed = (await c.get("/api/v1/event-logs")).json()
        mine = next(x for x in listed if x["id"] == new_id)
        assert mine["source_dataset"]["slug"] == ds["slug"]
        assert mine["source_dataset"]["status"] == "active"
        detail = (await c.get(f"/api/v1/event-logs/{new_id}")).json()
        assert detail["source_dataset"]["license_id"] == "cc-by-4.0"
        # The copy is a working log: its data reads.
        assert (await c.get(f"/api/v1/event-logs/{new_id}/variants")).status_code == 200


async def test_use_copies_parquet_and_results_only_with_fresh_result_mtimes(
    tmp_path: Path,
) -> None:
    from mate.api.ingest.storage import log_paths

    async with multi_user_client(HUB_USERS, mods=("precompute_mod",), tmp_path=tmp_path) as (
        c,
        state,
    ):
        log_id = await import_log(c, state)
        await warm(c, log_id, "precompute_mod")
        res = results_dir(CURATOR, log_id, "precompute_mod") / "summary.json"
        res.write_text("{}")
        os.utime(res, (1, 1))  # an ancient source mtime must not leak into the copy
        ds = await publish(c, state, log_id)
        new_id = (await use(c, state, STUDENT, ds["slug"])).json()["log"]["id"]

        dst = log_paths(new_id, STUDENT)
        assert sorted(p.name for p in dst.root.iterdir() if p.is_file()) == [
            "cases.parquet",
            "events.parquet",
        ]
        assert [p for p in dst.root.iterdir() if p.is_dir()] == [dst.ocel_dir]
        copied = results_dir(STUDENT, new_id, "precompute_mod") / "summary.json"
        assert copied.stat().st_mtime >= dst.events.stat().st_mtime


async def test_use_queues_no_job_and_emits_no_import_event(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from sqlalchemy import select

    from mate.api.db.engine import get_sessionmaker
    from mate.api.db.models import Job
    from mate.api.events import get_event_bus

    async with multi_user_client(HUB_USERS) as (c, state):
        log_id = await import_log(c, state)
        ds = await publish(c, state, log_id)
        bus = get_event_bus()
        topics: list[str] = []
        real_publish = bus.publish

        async def spy(topic: str, payload: Any, *a: Any, **kw: Any) -> Any:
            topics.append(topic)
            return await real_publish(topic, payload, *a, **kw)

        monkeypatch.setattr(bus, "publish", spy)
        new_id = (await use(c, state, STUDENT, ds["slug"])).json()["log"]["id"]
        assert topics == []
        async with get_sessionmaker()() as s:
            jobs = (await s.execute(select(Job).where(Job.user_id == STUDENT))).scalars().all()
        assert [j for j in jobs if (j.payload_json or {}).get("log_id") == new_id] == []


@pytest.mark.parametrize("case", ["non_member", "private", "disabled", "purged"])
async def test_use_refused_for_unreadable_or_disabled_dataset(case: str) -> None:
    async with multi_user_client(HUB_USERS) as (c, state):
        log_id = await import_log(c, state)
        if case == "non_member":
            team = await make_team(c, state, (OTHER,))
            ds = await publish(c, state, log_id, visibility="restricted", audience_team_id=team)
        else:
            ds = await publish(
                c, state, log_id, visibility="private" if case == "private" else "public"
            )
        if case in ("disabled", "purged"):
            be(state, CURATOR)
            await c.post(f"/api/v1/hub/datasets/{ds['slug']}/disable", json={"reason": "x"})
        if case == "purged":
            r = await c.post(
                f"/api/v1/hub/datasets/{ds['slug']}/purge", json={"confirm": ds["slug"]}
            )
            assert r.status_code == 200, r.text
        assert (await use(c, state, STUDENT, ds["slug"])).status_code == 404


async def test_student_actions_never_write_the_dataset_tree(tmp_path: Path) -> None:
    async with multi_user_client(
        HUB_USERS, mods=("precompute_mod", "sample_mod"), tmp_path=tmp_path
    ) as (c, state):
        log_id = await import_log(c, state)
        await warm(c, log_id, "precompute_mod")
        (results_dir(CURATOR, log_id, "precompute_mod") / "summary.json").write_text('{"n": 7}')
        ds = await publish(c, state, log_id)
        root = revision_root(ds["id"])
        before = tree_digest(root)
        assert "module_results/precompute_mod/summary.json" in before

        new_id = (await use(c, state, STUDENT, ds["slug"])).json()["log"]["id"]
        be(state, STUDENT)
        # Opening the module seeds its run from the copied result.
        seeded = await c.post(f"/api/v1/event-logs/{new_id}/module-runs/precompute_mod")
        assert seeded.status_code == 200, seeded.text
        assert (
            await c.patch(
                f"/api/v1/event-logs/{new_id}/events/0", json={"field": "resource", "value": "z"}
            )
        ).status_code == 200
        assert (
            await c.put(
                f"/api/v1/event-logs/{new_id}/active-filter",
                json={"filter": [{"field": "activity", "op": "equals", "value": "ship"}]},
            )
        ).status_code == 200
        # A module route writing its cache (sidecar of the copy).
        assert (
            await c.get("/api/v1/modules/sample_mod/cached-count", params={"log_id": new_id})
        ).status_code == 200
        dup = await c.post(f"/api/v1/event-logs/{new_id}/duplicate")
        assert dup.status_code == 201, dup.text
        assert (await c.delete(f"/api/v1/event-logs/{dup.json()['id']}")).status_code == 204
        assert (await c.delete(f"/api/v1/event-logs/{new_id}")).status_code == 204

        assert tree_digest(root) == before


async def test_reimport_and_remap_refused_on_derived_log() -> None:
    from .test_mcp_analysis import _ctx, _principal, _seed_log

    async with multi_user_client(HUB_USERS) as (c, state):
        from mate.api.mcp.errors import MCPToolError
        from mate.api.mcp.toolsets import processes

        log_id = await import_log(c, state)
        ds = await publish(c, state, log_id)
        new_id = (await use(c, state, STUDENT, ds["slug"])).json()["log"]["id"]
        be(state, STUDENT)
        r = await c.post(f"/api/v1/event-logs/{new_id}/reimport")
        assert r.status_code == 409 and r.json()["detail"].startswith("no_original")
        r = await c.post(
            f"/api/v1/event-logs/{new_id}/remap",
            json={"case_id": "case_id", "activity": "activity", "timestamp": "timestamp"},
        )
        assert r.status_code == 409 and r.json()["detail"].startswith("no_original")

        await _seed_log(new_id, STUDENT)  # egress consent for the MCP principal
        with pytest.raises(MCPToolError, match=r"\[conflict\] no_original"):
            await processes.reimport_process(_ctx(_principal(STUDENT)), new_id, confirm=True)


async def test_duplicate_keeps_column_roles_and_lineage() -> None:
    async with multi_user_client(HUB_USERS) as (c, state):
        log_id = await import_log(c, state)
        await set_log(log_id, mapping_needs_review=True)
        dup = await c.post(f"/api/v1/event-logs/{log_id}/duplicate")
        src_row, dup_row = await get_log(log_id), await get_log(dup.json()["id"])
        assert dup_row.column_roles == src_row.column_roles and dup_row.column_roles
        assert dup_row.mapping_needs_review is True

        await set_log(log_id, mapping_needs_review=False)
        ds = await publish(c, state, log_id)
        new_id = (await use(c, state, STUDENT, ds["slug"])).json()["log"]["id"]
        dup2 = await c.post(f"/api/v1/event-logs/{new_id}/duplicate")
        assert dup2.status_code == 201
        assert dup2.json()["source_dataset"]["slug"] == ds["slug"]
        row = await get_log(dup2.json()["id"])
        assert row.dataset_revision_id == (await get_log(new_id)).dataset_revision_id
        assert row.license_snapshot == {
            **(await get_log(new_id)).license_snapshot,
            "duplicate_of": new_id,
        }


async def test_delete_log_removes_its_module_results() -> None:
    async with multi_user_client(HUB_USERS) as (c, state):
        log_id = await import_log(c, state)
        res = results_dir(CURATOR, log_id, "any_mod")
        (res / "k.json").write_text("{}")
        be(state, CURATOR)
        assert (await c.delete(f"/api/v1/event-logs/{log_id}")).status_code == 204
        assert not res.parent.exists()


async def test_hub_flag_off_hides_routes_but_keeps_gates(monkeypatch: pytest.MonkeyPatch) -> None:
    from mate.api.config import get_settings

    async with multi_user_client(HUB_USERS) as (c, state):
        log_id = await import_log(c, state)
        ds = await publish(c, state, log_id)
        new_id = (await use(c, state, STUDENT, ds["slug"])).json()["log"]["id"]
        be(state, CURATOR)
        await c.post(f"/api/v1/hub/datasets/{ds['slug']}/disable", json={"reason": "x"})

        monkeypatch.setattr(get_settings(), "hub_datasets_enabled", False)
        be(state, STUDENT)
        assert (await c.get("/api/v1/hub/datasets")).status_code == 404
        assert (await c.get(f"/api/v1/hub/datasets/{ds['slug']}")).status_code == 404
        assert (await c.get("/api/v1/hub/status")).json() == {"enabled": False, "can_curate": False}
        assert (await c.get(f"/api/v1/event-logs/{new_id}/variants")).status_code == 410


async def test_dashboard_share_of_restricted_derived_log_limited_to_audience() -> None:
    from mate.api.db.engine import get_sessionmaker
    from mate.api.sharing import user_can_read_log, user_can_read_log_module

    async with multi_user_client(HUB_USERS) as (c, state):
        log_id = await import_log(c, state)
        audience = await make_team(c, state, (STUDENT, OTHER))
        outsiders = await make_team(c, state, (STUDENT, REVIEWER))
        ds = await publish(c, state, log_id, visibility="restricted", audience_team_id=audience)
        new_id = (await use(c, state, STUDENT, ds["slug"])).json()["log"]["id"]

        be(state, STUDENT)
        board = await c.post(
            "/api/v1/dashboards",
            json={
                "name": "b",
                "event_log_id": new_id,
                "items": [
                    {"i": "a", "module_id": "m", "widget_id": "w", "x": 0, "y": 0, "w": 4, "h": 4}
                ],
            },
        )
        assert board.status_code == 201, board.text
        assert board.json()["source_dataset"]["slug"] == ds["slug"]
        bid = board.json()["id"]
        shares = f"/api/v1/dashboards/{bid}/shares"
        assert (await c.post(shares, json={"target_team_id": outsiders})).status_code == 403
        assert (await c.post(shares, json={"target_user_id": REVIEWER})).status_code == 403
        assert (await c.post(shares, json={"target_user_id": OTHER})).status_code == 201

        async with get_sessionmaker()() as s:
            assert await user_can_read_log(s, new_id, OTHER)
            assert await user_can_read_log_module(s, new_id, OTHER, "m")
        be(state, CURATOR)
        r = await c.delete(f"/api/v1/admin/teams/{audience}/members/{OTHER}")
        assert r.status_code == 204
        async with get_sessionmaker()() as s:
            assert not await user_can_read_log(s, new_id, OTHER)
            assert not await user_can_read_log_module(s, new_id, OTHER, "m")
        assert ADMIN  # (curator roles used throughout via `be`)


async def test_sandboxed_recipient_cannot_read_non_open_copy_through_share() -> None:
    """A reviewer gets 404 on a public 4TU dataset; a dashboard share of a copy
    must not hand it the data either (the share path knows only the user id, so
    the sandboxed role is persisted at login)."""
    from mate.api.auth.dependencies import _jit_sync_user, _seen_user_ids
    from mate.api.db.engine import get_sessionmaker
    from mate.api.sharing import user_can_read_log, user_can_read_log_module

    from .conftest import make_user

    async with multi_user_client(HUB_USERS) as (c, state):
        # The reviewer's first request in this process persists the flag.
        _seen_user_ids.discard(REVIEWER)
        async with get_sessionmaker()() as s:
            await _jit_sync_user(s, make_user(REVIEWER, ("user", "reviewer")))

        log_id = await import_log(c, state)
        team = await make_team(c, state, (STUDENT, REVIEWER, OTHER))
        ds = await publish(c, state, log_id, license_id="4tu-general-terms")
        be(state, REVIEWER)
        assert (await c.get(f"/api/v1/hub/datasets/{ds['slug']}")).status_code == 404

        new_id = (await use(c, state, STUDENT, ds["slug"])).json()["log"]["id"]
        be(state, STUDENT)
        board = await c.post(
            "/api/v1/dashboards",
            json={
                "name": "b",
                "event_log_id": new_id,
                "items": [
                    {"i": "a", "module_id": "m", "widget_id": "w", "x": 0, "y": 0, "w": 4, "h": 4}
                ],
            },
        )
        shares = f"/api/v1/dashboards/{board.json()['id']}/shares"
        assert (await c.post(shares, json={"target_user_id": REVIEWER})).status_code == 403
        assert (await c.post(shares, json={"target_team_id": team})).status_code == 201
        async with get_sessionmaker()() as s:
            assert await user_can_read_log(s, new_id, OTHER)
            assert not await user_can_read_log(s, new_id, REVIEWER)
            assert not await user_can_read_log_module(s, new_id, REVIEWER, "m")
