"""Precompute ordering under lazy processing.

A module that depends on another - it subscribes to the producer's reserved
`<id>.completed` event, or lists it in `requirements.modules` - must not run until
the producer has. With nothing waiting on the module list anymore, the coordinator
itself orders the work: it runs the producer first, parks the dependent as a
`queued` run with no job, and releases it when the producer finishes (or fails it,
if the producer never will).

Two layers:
  * the plan-ordering helper as a pure unit, no app needed;
  * the coordinator end to end with `precompute_mod` (a `log.imported` subscriber)
    and `chained_mod` (subscribes to `precompute_mod.completed`).
"""

from __future__ import annotations

from pathlib import Path

import pytest

from .conftest import TEST_USER_ID
from .test_module_processing import (
    coordinator_or_fail,
    data_revision,
    import_sample,
    mods_client,
    module_jobs,
    run_row,
    set_run,
    state,
    wait_until,
)

# --------------------------------------------------------------------------- #
# Plan ordering - the jobs UI renders the plan verbatim, so it must read as the
# execution order, not alphabetically.
# --------------------------------------------------------------------------- #


def _topo(after):
    from mate.api.modules.processing import ModuleProcessingCoordinator

    return ModuleProcessingCoordinator._topo_order(after)


def test_topo_puts_upstream_first_against_alphabetical_order() -> None:
    # "a" waits on "z": alphabetical would invert the real execution order.
    assert _topo({"a": ["z"], "z": []}) == ["z", "a"]


def test_topo_sorts_alphabetically_within_a_layer() -> None:
    # Same depth → stable, byte-identical output across imports.
    assert _topo({"b": [], "a": [], "c": ["a", "b"]}) == ["a", "b", "c"]


def test_topo_appends_cycle_members_instead_of_dropping_them() -> None:
    # A cycle can't be emitted by Kahn; it must still appear in the plan, or the
    # UI would show a checklist shorter than the set of modules being run.
    out = _topo({"a": ["b"], "b": ["a"], "root": []})
    assert out[0] == "root"
    assert sorted(out[1:]) == ["a", "b"]


# --------------------------------------------------------------------------- #
# Dependency resolution
# --------------------------------------------------------------------------- #


@pytest.mark.asyncio
async def test_precompute_deps_follow_the_chain_in_dependency_order(tmp_path: Path) -> None:
    from mate.api.modules import get_module_loader

    async with mods_client(tmp_path, "precompute_mod", "chained_mod"):
        loader = get_module_loader()
        both = {"precompute_mod", "chained_mod"}

        assert loader.chained_producers("chained_mod", both) == {"precompute_mod"}
        assert loader.precompute_deps("precompute_mod", both) == ["precompute_mod"]
        # Producers come first - the order `ensure` runs them in.
        assert loader.precompute_deps("chained_mod", both) == ["precompute_mod", "chained_mod"]
        # A producer the user never installed cannot run for them.
        assert loader.precompute_deps("chained_mod", {"chained_mod"}) == ["chained_mod"]


# --------------------------------------------------------------------------- #
# Running a chain
# --------------------------------------------------------------------------- #


@pytest.mark.asyncio
async def test_chained_module_runs_only_after_its_producer(tmp_path: Path) -> None:
    async with mods_client(tmp_path, "precompute_mod", "chained_mod") as c:
        log_id = await import_sample(c)

        states = await coordinator_or_fail().ensure(TEST_USER_ID, log_id, "chained_mod")
        by_id = {s.module_id: s for s in states}
        assert [s.module_id for s in states] == ["precompute_mod", "chained_mod"]
        assert by_id["precompute_mod"].job_id is not None  # the producer is submitted now
        # ...the dependent is only parked: queued, with no job of its own yet.
        assert (by_id["chained_mod"].status, by_id["chained_mod"].job_id) == ("queued", None)

        async def _both_ready() -> bool:
            return (
                await state(log_id, "precompute_mod") == "ready"
                and await state(log_id, "chained_mod") == "ready"
            )

        await wait_until(_both_ready, what="the chain to finish")

        [producer] = await module_jobs(log_id, "precompute_mod")
        [dependent] = await module_jobs(log_id, "chained_mod")
        assert dependent.created_at >= producer.finished_at  # strictly after, never alongside


@pytest.mark.asyncio
async def test_failed_producer_fails_the_module_waiting_on_it(tmp_path: Path) -> None:
    from mate.api.jobs.runtime import get_job_runtime

    async with mods_client(tmp_path, "precompute_mod", "chained_mod") as c:
        log_id = await import_sample(c)
        runtime = get_job_runtime()

        # Hold the queue so the producer is still waiting when we cancel it.
        await runtime.pause_queue(TEST_USER_ID)
        try:
            states = await coordinator_or_fail().ensure(TEST_USER_ID, log_id, "chained_mod")
            producer_job = states[0].job_id
            assert producer_job is not None
            await runtime.cancel(producer_job)

            async def _dependent_failed() -> bool:
                return await state(log_id, "chained_mod") == "failed"

            await wait_until(_dependent_failed, what="the waiting module to be failed")
        finally:
            await runtime.resume_queue(TEST_USER_ID)

        assert await state(log_id, "precompute_mod") == "cancelled"
        row = await run_row(log_id, "chained_mod")
        assert "precompute_mod" in (row.error or "")
        assert await module_jobs(log_id, "chained_mod") == []  # it never got a job


@pytest.mark.asyncio
async def test_chained_module_is_refreshed_after_its_producer_on_a_data_change(
    tmp_path: Path,
) -> None:
    async with mods_client(tmp_path, "precompute_mod", "chained_mod") as c:
        log_id = await import_sample(c)
        await coordinator_or_fail().ensure(TEST_USER_ID, log_id, "chained_mod")

        async def _both_ready() -> bool:
            return (
                await state(log_id, "precompute_mod") == "ready"
                and await state(log_id, "chained_mod") == "ready"
            )

        await wait_until(_both_ready, what="the first chain run")
        before = await data_revision(log_id)

        resp = await c.put(
            f"/api/v1/event-logs/{log_id}/active-filter",
            json={"filter": [{"field": "activity", "op": "equals", "value": "register order"}]},
        )
        assert resp.status_code == 200, resp.text

        # Only the producer is a root subscriber of `log.imported`; the dependent is
        # woken by the producer's `<id>.completed`, and refreshed because it was used.
        async def _both_refreshed() -> bool:
            rows = [await run_row(log_id, m) for m in ("precompute_mod", "chained_mod")]
            return all(
                r is not None and r.status == "ready" and r.data_revision == before + 1
                for r in rows
            )

        await wait_until(_both_refreshed, what="the chain to refresh")
        assert len(await module_jobs(log_id, "chained_mod")) == 2
        [_, producer_again] = await module_jobs(log_id, "precompute_mod")
        [_, dependent_again] = await module_jobs(log_id, "chained_mod")
        assert dependent_again.created_at >= producer_again.finished_at


# --------------------------------------------------------------------------- #
# The plan the jobs drawer shows
# --------------------------------------------------------------------------- #


@pytest.mark.asyncio
async def test_precompute_plan_lists_only_what_will_run_in_execution_order(
    tmp_path: Path,
) -> None:
    from mate.api.db.engine import get_sessionmaker
    from mate.api.db.models import EventLog

    async with mods_client(tmp_path, "precompute_mod", "chained_mod") as c:
        log_id = await import_sample(c)
        coordinator = coordinator_or_fail()

        async def _plan() -> tuple[set[str], list[dict]]:
            async with get_sessionmaker()() as session:
                row = await session.get(EventLog, log_id)
                assert row is not None
                return await coordinator.precompute_plan(
                    "log.imported",
                    TEST_USER_ID,
                    session,
                    log_id=log_id,
                    log_model=row.log_model,
                    detected_schema=row.detected_schema,
                    events_count=row.events_count,
                    cases_count=row.cases_count,
                )

        # Nothing is eager and nothing has been used: a fresh import starts no module.
        assert await _plan() == (set(), [])

        # A reimport refreshes what the log already used - the producer, plus the
        # module chained off it - and the plan reads in the order they will run.
        await set_run(log_id, "precompute_mod", status="ready")
        await set_run(log_id, "chained_mod", status="ready")
        nodes, plan = await _plan()
        assert nodes == {"precompute_mod", "chained_mod"}
        assert [step["id"] for step in plan] == ["precompute_mod", "chained_mod"]
        by_id = {step["id"]: step["after"] for step in plan}
        assert by_id == {"precompute_mod": [], "chained_mod": ["precompute_mod"]}
