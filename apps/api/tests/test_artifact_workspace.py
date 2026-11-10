"""The artifact workspace (`mate.api.modules.artifacts`, `/api/v1/artifacts`): an index
over the modules' result caches - registered when a default run or a run variant
settles ready or found (or refreshed) by a list; a run's row hidden while its entry is
missing, a variant's pruned once its dir is gone - plus uploaded JSON artifacts that
bind input ports by `…/_workspace/{id}` ref.

Fixtures: the `artifact_producer` -> `artifact_consumer` pair (a required `petri_net`
input) from `test_run_variants`.
"""

from __future__ import annotations

import asyncio
import importlib.util
import io
import json
import shutil
from collections.abc import AsyncIterator
from datetime import UTC, datetime, timedelta
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pandas as pd
import pytest
import sqlalchemy as sa
from alembic.migration import MigrationContext
from alembic.operations import Operations
from httpx import AsyncClient
from sqlalchemy.exc import IntegrityError

from mate.api.db.engine import get_sessionmaker
from mate.api.db.models import Artifact, EventLog, ModuleInstall, SystemSetting
from mate.api.modules.cache import ResultCache, run_variant_key
from mate.api.modules.inputs import workspace_file
from mate.sdk.manifest import DatasetEntry

from .conftest import TEST_USER_ID
from .test_log_module_config import (
    OTHER_ID,
    _seed_user,  # pyright: ignore[reportPrivateUsage]
)
from .test_module_processing import (
    coordinator_or_fail,
    import_sample,
    mods_client,
    state,
    wait_until,
)
from .test_run_variants import (
    _ONLY_TWO,  # pyright: ignore[reportPrivateUsage]
    PAIR,
    _fake_s3,  # pyright: ignore[reportPrivateUsage]
    _gc_everything,  # pyright: ignore[reportPrivateUsage]
    _ready,  # pyright: ignore[reportPrivateUsage]
    _settled,  # pyright: ignore[reportPrivateUsage]
    _url,  # pyright: ignore[reportPrivateUsage]
)

ART = "/api/v1/artifacts"
_NET = {
    "places": [{"id": "p0"}, {"id": "p1"}, {"id": "p2"}],
    "transitions": [{"id": "t0", "label": "a"}],
    "arcs": [{"source": "p0", "target": "t0"}],
}


def _now() -> datetime:
    return datetime.now(UTC).replace(tzinfo=None)


async def _listed(c: AsyncClient, log_id: str, n: int = 1, **params: Any) -> list[dict[str, Any]]:
    """The log's artifacts, once at least *n* are indexed (registration trails the
    run's state by a moment)."""
    out: list[dict[str, Any]] = []

    async def _check() -> bool:
        nonlocal out
        resp = await c.get(ART, params={"log_id": log_id, **params})
        assert resp.status_code == 200, resp.text
        out = resp.json()
        return len(out) >= n

    await wait_until(_check, what=f"{n} artifact(s) to be indexed")
    return out


async def _default_ready(log_id: str, module_id: str) -> None:
    await coordinator_or_fail().ensure(TEST_USER_ID, log_id, module_id)

    async def _ready_now() -> bool:
        return await state(log_id, module_id) == "ready"

    await wait_until(_ready_now, what=f"{module_id}'s default run")


async def _upload(c: AsyncClient, **body: Any) -> dict[str, Any]:
    resp = await c.post(ART, json=body)
    assert resp.status_code == 201, resp.text
    return resp.json()


async def _foreign_log(tag: str) -> str:
    await _seed_user(OTHER_ID)
    log_id = f"p5-foreign-{tag}"
    async with get_sessionmaker()() as session:
        if await session.get(EventLog, log_id) is None:
            session.add(
                EventLog(
                    id=log_id, user_id=OTHER_ID, name="theirs", status="ready", created_at=_now()
                )
            )
            await session.commit()
    return log_id


async def _foreign_upload(tag: str, artifact_type: str = "petri_net") -> str:
    """Another user's uploaded artifact (row + file); its ref."""
    await _seed_user(OTHER_ID)
    artifact_id = f"p5-foreign-art-{tag}"
    ref = f"_/_workspace/{artifact_id}"
    path = workspace_file(OTHER_ID, artifact_id)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(_NET))
    async with get_sessionmaker()() as session:
        if await session.get(Artifact, artifact_id) is None:
            session.add(
                Artifact(
                    id=artifact_id,
                    user_id=OTHER_ID,
                    ref=ref,
                    artifact_type=artifact_type,
                    type_version=1,
                    source="upload",
                    key=artifact_id,
                    name="theirs",
                    inputs=[],
                )
            )
            await session.commit()
    return ref


async def _row(artifact_id: str) -> Artifact | None:
    async with get_sessionmaker()() as session:
        return await session.get(Artifact, artifact_id)


async def _bump(log_id: str) -> None:
    """A data change: the log's default run and variants go stale."""
    async with get_sessionmaker()() as session:
        log_row = await session.get(EventLog, log_id)
        assert log_row is not None
        log_row.data_revision = int(log_row.data_revision or 0) + 1
        await session.commit()


# --------------------------------------------------------------------------- #
# Registration + lineage
# --------------------------------------------------------------------------- #


@pytest.mark.asyncio
async def test_default_and_variant_runs_register_their_outputs(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from mate.api.modules import get_module_loader

    async with mods_client(tmp_path, *PAIR) as c:
        log_id = await import_sample(c, name="p5 registration")
        # Give the consumer a typed output, so its lineage gets indexed too.
        consumer = get_module_loader().loaded["artifact_consumer"].manifest
        seen = DatasetEntry.model_validate(
            {"id": "seen", "artifact_type": "drift_points", "cache_key": "seen"}
        )
        monkeypatch.setattr(consumer, "datasets", [seen])

        await _default_ready(log_id, "artifact_producer")
        [prod] = await _listed(c, log_id)
        default_ref = f"{log_id}/artifact_producer/model"
        assert prod["ref"] == default_ref
        assert (prod["source"], prod["variant"], prod["module_name"]) == (
            "run",
            None,
            "Artifact Producer",
        )
        assert (prod["artifact_type"], prod["type_version"], prod["name"]) == (
            "petri_net",
            1,
            "model",
        )
        assert prod["producer_version"] == "0.1.0" and prod["producer_hash"]
        assert prod["inputs"] == [] and prod["size_bytes"] > 0

        # The consumer's default run records the producer it read.
        await _default_ready(log_id, "artifact_consumer")
        rows = await _listed(c, log_id, 2)
        cons = next(r for r in rows if r["module_id"] == "artifact_consumer")
        assert cons["inputs"] == [default_ref]

        # A rename and a pin survive re-registration (a rerun on new data).
        resp = await c.patch(f"{ART}/{prod['id']}", json={"name": "My net", "pinned": True})
        assert resp.status_code == 200 and resp.json()["name"] == "My net"
        await _bump(log_id)
        await _default_ready(log_id, "artifact_producer")
        again: dict[str, Any] = {}

        async def _reregistered() -> bool:
            nonlocal again
            again = (await c.get(f"{ART}/{prod['id']}")).json()
            return again["updated_at"] != prod["updated_at"]

        await wait_until(_reregistered, what="the rerun to re-register the artifact")
        assert (again["name"], again["pinned"]) == ("My net", True)

        # A variant registers under its own ref, with its spec.
        pv = await _ready(c, log_id, "artifact_producer", filter=_ONLY_TWO)
        variant_ref = f"{log_id}/artifact_producer@{pv['variant']}/model"
        rows = await _listed(c, log_id, 3)
        var = next(r for r in rows if r["ref"] == variant_ref)
        assert var["variant"] == pv["variant"]
        assert var["spec"] == {"params": {}, "inputs": {}, "filter": _ONLY_TWO}

        # A consumer variant's lineage is its spec's binding, resolved in the detail.
        cv = await _ready(c, log_id, "artifact_consumer", inputs={"reference_model": variant_ref})
        rows = await _listed(c, log_id, 4)
        cons_v = next(r for r in rows if r["variant"] == cv["variant"])
        assert cons_v["inputs"] == [variant_ref]
        detail = (await c.get(f"{ART}/{cons_v['id']}")).json()
        assert detail["lineage"] == [
            {"ref": variant_ref, "id": var["id"], "name": "model", "artifact_type": "petri_net"}
        ]


@pytest.mark.asyncio
async def test_a_default_row_is_hidden_never_dropped_while_its_entry_is_missing(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from mate.api.modules import get_module_loader

    async with mods_client(tmp_path, *PAIR) as c:
        log_id = await import_sample(c, name="p5 hidden default")
        await _default_ready(log_id, "artifact_producer")
        [prod] = await _listed(c, log_id)
        resp = await c.patch(f"{ART}/{prod['id']}", json={"name": "Mine", "pinned": True})
        assert resp.status_code == 200
        entry = ResultCache(log_id, "artifact_producer", TEST_USER_ID).dir / "model.json"
        saved = entry.read_text()
        entry.unlink()
        assert (await c.get(ART, params={"log_id": log_id})).json() == []
        assert (await c.get(f"{ART}/{prod['id']}")).status_code == 404
        assert await _row(prod["id"]) is not None
        entry.write_text(saved)
        [back] = (await c.get(ART, params={"log_id": log_id})).json()
        assert (back["id"], back["name"], back["pinned"]) == (prod["id"], "Mine", True)

        # A rerun empties the cache before it submits: a list in between hides the row,
        # and the rerun's registration brings the same row back.
        loader = get_module_loader()
        submit = loader.submit_precompute
        during: list[Any] = []

        async def _submit(*args: Any, **kwargs: Any) -> Any:
            during.append((await c.get(ART, params={"log_id": log_id})).json())
            return await submit(*args, **kwargs)

        monkeypatch.setattr(loader, "submit_precompute", _submit)
        await _bump(log_id)
        await _default_ready(log_id, "artifact_producer")
        assert during == [[]]
        again: dict[str, Any] = {}

        async def _reregistered() -> bool:
            nonlocal again
            again = (await c.get(f"{ART}/{prod['id']}")).json()
            return again.get("updated_at", back["updated_at"]) != back["updated_at"]

        await wait_until(_reregistered, what="the rerun to re-register the artifact")
        assert (again["name"], again["pinned"]) == ("Mine", True)


@pytest.mark.asyncio
async def test_a_variants_row_is_hidden_while_its_dir_is_there_and_pruned_once_gone(
    tmp_path: Path,
) -> None:
    async with mods_client(tmp_path, *PAIR) as c:
        log_id = await import_sample(c, name="p5 prune variant")
        pv = await _ready(c, log_id, "artifact_producer", filter=_ONLY_TWO)
        [row] = await _listed(c, log_id)
        cache = ResultCache(log_id, "artifact_producer", TEST_USER_ID, run_variant=pv["variant"])
        (cache.dir / "model.json").unlink()
        assert (await c.get(ART, params={"log_id": log_id})).json() == []
        assert await _row(row["id"]) is not None
        cache.remove()  # evicted
        assert (await c.get(ART, params={"log_id": log_id})).json() == []
        assert await _row(row["id"]) is None


@pytest.mark.asyncio
async def test_a_variants_row_keeps_its_name_and_pin_through_a_rerun(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A stale variant's rerun empties its dir before it writes the queued meta: a list
    in between hides the row, and the rerun's registration brings the same row back."""
    async with mods_client(tmp_path, *PAIR) as c:
        log_id = await import_sample(c, name="p5 variant rerun")
        pv = await _ready(c, log_id, "artifact_producer", filter=_ONLY_TWO)
        [row] = await _listed(c, log_id)
        resp = await c.patch(f"{ART}/{row['id']}", json={"name": "Mine", "pinned": True})
        assert resp.status_code == 200
        await _bump(log_id)

        loop = asyncio.get_running_loop()
        clear = ResultCache.clear
        during: list[Any] = []

        def _clear(self: ResultCache, **kwargs: Any) -> None:
            clear(self, **kwargs)
            if self.run_variant == pv["variant"]:  # runs in a thread: list on the loop
                listing = c.get(ART, params={"log_id": log_id})
                done = asyncio.run_coroutine_threadsafe(listing, loop).result(timeout=30)
                during.append(done.json())

        monkeypatch.setattr(ResultCache, "clear", _clear)
        resp = await c.post(_url(log_id, "artifact_producer"), json={"filter": _ONLY_TWO})
        assert resp.status_code in (200, 202), resp.text
        monkeypatch.setattr(ResultCache, "clear", clear)
        assert during == [[]]
        again: dict[str, Any] = {}

        async def _reregistered() -> bool:
            nonlocal again
            again = (await c.get(f"{ART}/{row['id']}")).json()
            return again.get("updated_at", row["updated_at"]) != row["updated_at"]

        await wait_until(_reregistered, what="the rerun to re-register the artifact")
        assert (again["name"], again["pinned"]) == ("Mine", True)
        # Leave no pinned variant behind: it would outlive every later test's GC.
        assert (await c.delete(f"{ART}/{row['id']}")).status_code == 204


@pytest.mark.asyncio
async def test_a_pinned_variant_whose_rerun_failed_stays_listed_until_retried_or_deleted(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A pinned variant reruns (stale) and fails: it has no output, yet the workspace
    lists it `failed` - to rename, unpin, delete, or rerun with `force` - and the GC
    no longer keeps its dir for the pin."""
    async with mods_client(tmp_path, *PAIR) as c:
        log_id = await import_sample(c, name="p5 failed rerun")
        a = await _ready(c, log_id, "artifact_producer", filter=_ONLY_TWO)
        b = await _ready(c, log_id, "artifact_producer", filter=[])
        rows = await _listed(c, log_id, 2)
        row_a = next(r for r in rows if r["variant"] == a["variant"])
        row_b = next(r for r in rows if r["variant"] == b["variant"])
        assert {r["state"] for r in rows} == {"ready"}
        for r in (row_a, row_b):
            assert (await c.patch(f"{ART}/{r['id']}", json={"pinned": True})).status_code == 200
        write = ResultCache._set_and_persist  # pyright: ignore[reportPrivateUsage]

        def _boom(self: ResultCache, key: str, value: Any) -> None:
            if self.run_variant is not None:
                raise RuntimeError("boom")
            write(self, key, value)

        async def _fail(*variants: dict[str, Any]) -> None:
            await _bump(log_id)
            with monkeypatch.context() as m:
                m.setattr(ResultCache, "_set_and_persist", _boom)
                for v in variants:
                    resp = await c.post(_url(log_id, "artifact_producer"), json=v["spec"])
                    assert resp.status_code in (200, 202), resp.text
                    settled = await _settled(c, log_id, "artifact_producer", v["variant"])
                    assert settled["status"] == "failed", settled

        def _dir(v: dict[str, Any]) -> Path:
            return ResultCache(
                log_id, "artifact_producer", TEST_USER_ID, run_variant=v["variant"]
            ).dir

        await _fail(a, b)
        listed = (await c.get(ART, params={"log_id": log_id})).json()
        assert sorted((r["id"], r["state"]) for r in listed) == sorted(
            [(row_a["id"], "failed"), (row_b["id"], "failed")]
        )
        detail = await c.get(f"{ART}/{row_a['id']}")
        assert detail.status_code == 200 and detail.json()["state"] == "failed"
        assert (await c.get(f"{ART}/{row_a['id']}/content")).status_code == 404

        # Unpinned it is hidden - and still renamed, (un)pinned and deleted.
        resp = await c.patch(f"{ART}/{row_b['id']}", json={"pinned": False, "name": "B"})
        assert resp.status_code == 200
        assert (resp.json()["state"], resp.json()["name"]) == ("failed", "B")
        assert [r["id"] for r in (await c.get(ART, params={"log_id": log_id})).json()] == [
            row_a["id"]
        ]
        assert (await c.get(f"{ART}/{row_b['id']}")).status_code == 404
        assert (await c.delete(f"{ART}/{row_b['id']}")).status_code == 204
        assert not _dir(b).exists() and await _row(row_b["id"]) is None

        # "Re-run": the row's spec again, with `force`.
        resp = await c.post(
            _url(log_id, "artifact_producer"), json={**row_a["spec"], "force": True}
        )
        assert resp.status_code in (200, 202), resp.text
        assert (await _settled(c, log_id, "artifact_producer", a["variant"]))["status"] == "ready"
        [back] = (await c.get(ART, params={"log_id": log_id})).json()
        assert (back["id"], back["state"], back["pinned"]) == (row_a["id"], "ready", True)

        # Failed again: the pin no longer keeps it from the GC.
        await _fail(a)
        await _gc_everything()
        assert not _dir(a).exists() and await _row(row_a["id"]) is None


@pytest.mark.asyncio
async def test_a_list_indexes_outputs_no_run_registered(tmp_path: Path) -> None:
    """A run that settled before the index existed (or a cache entry a route wrote)."""
    async with mods_client(tmp_path, *PAIR) as c:
        log_id = await import_sample(c, name="p5 discover")
        await _default_ready(log_id, "artifact_producer")
        [prod] = await _listed(c, log_id)
        async with get_sessionmaker()() as session:
            await session.execute(sa.delete(Artifact).where(Artifact.log_id == log_id))
            await session.commit()

        # Without `log_id` as well: the newest logs are looked at.
        found = [r for r in (await c.get(ART)).json() if r["log_id"] == log_id]
        assert [(r["ref"], r["name"], r["inputs"], r["producer_hash"]) for r in found] == [
            (prod["ref"], "model", [], None)
        ]
        # Once: the next list leaves the row alone.
        again = (await c.get(ART, params={"log_id": log_id})).json()
        assert [(r["id"], r["updated_at"]) for r in again] == [
            (found[0]["id"], found[0]["updated_at"])
        ]


@pytest.mark.asyncio
async def test_a_model_interop_import_is_listed_and_refreshed(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from mate.api.modules import artifacts

    from .test_shared_read_only import (
        _client,  # pyright: ignore[reportPrivateUsage]
        _pnml,  # pyright: ignore[reportPrivateUsage]
    )

    async with _client(tmp_path) as (c, _who):
        log_id = await import_sample(c, name="p5 interop import")

        async def _import(label: str) -> str:
            resp = await c.post(
                "/api/v1/modules/interop/import/pnml",
                params={"log_id": log_id},
                files={"file": ("a.pnml", _pnml(label))},
            )
            assert resp.status_code == 200, resp.text
            return resp.json()["ref"]

        ref = await _import("t1")

        async def _listed_import() -> dict[str, Any]:
            listed = (await c.get(ART, params={"log_id": log_id})).json()
            [row] = [r for r in listed if r["ref"] == ref]
            return row

        # Without `log_id`, past the newest logs: a log whose results changed last.
        monkeypatch.setattr(artifacts, "DISCOVER_LOGS", 1)
        async with get_sessionmaker()() as session:
            newer = EventLog(
                id="p5-newer-log",
                user_id=TEST_USER_ID,
                name="newer",
                status="ready",
                created_at=_now() + timedelta(minutes=1),
            )
            session.add(newer)
            await session.commit()
            assert ref in {r["ref"] for r in (await c.get(ART)).json()}
            await session.delete(newer)
            await session.commit()

        # A re-import overwrites the entry: the row shows its new size and time.
        first = await _listed_import()
        await _import("a much longer transition label " * 20)
        second = await _listed_import()
        entry = ResultCache(log_id, "interop", TEST_USER_ID).dir / f"{first['key']}.json"
        assert second["id"] == first["id"]
        assert second["size_bytes"] == entry.stat().st_size != first["size_bytes"]
        assert second["updated_at"] != first["updated_at"]


@pytest.mark.asyncio
async def test_s3_rehydration_leaves_updated_at_alone(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """S3 mode: the reaper evicts a default result and a read hydrates it - a fresh
    mtime on the same bytes, not content produced anew."""
    from mate.api.storage import sync as storage_sync

    async with mods_client(tmp_path, *PAIR) as c:
        log_id = await import_sample(c, name="p5 s3 hydrate")
        await _default_ready(log_id, "artifact_producer")
        [row] = await _listed(c, log_id)
        default = ResultCache(log_id, "artifact_producer", TEST_USER_ID)
        before = (default.dir / "model.json").read_bytes()
        _fake_s3(monkeypatch)
        storage_sync.persist_dir_sync(default.dir)
        await asyncio.sleep(0.05)
        shutil.rmtree(default.dir)  # the reaper
        storage_sync.forget(default.dir)
        assert (await c.get(f"{ART}/{row['id']}/content")).status_code == 200  # hydrates
        assert (default.dir / "model.json").read_bytes() == before
        [again] = (await c.get(ART, params={"log_id": log_id})).json()
        assert (again["updated_at"], again["size_bytes"]) == (row["updated_at"], row["size_bytes"])


@pytest.mark.asyncio
async def test_a_variant_with_an_unwritten_output_is_indexed_once(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A ready variant that wrote only some of its declared outputs (an interop variant
    never writes `imported_*`) is not re-registered on every list."""
    from mate.api.modules import artifacts, get_module_loader

    async with mods_client(tmp_path, *PAIR) as c:
        log_id = await import_sample(c, name="p5 partial variant")
        manifest = get_module_loader().loaded["artifact_producer"].manifest
        extra = DatasetEntry.model_validate(
            {"id": "extra", "artifact_type": "petri_net", "cache_key": "extra"}
        )
        monkeypatch.setattr(manifest, "datasets", [*manifest.datasets, extra])
        await _ready(c, log_id, "artifact_producer", filter=_ONLY_TWO)
        await _listed(c, log_id)
        calls: list[Any] = []
        register = artifacts.register_run

        async def _counting(*args: Any, **kwargs: Any) -> None:
            calls.append(kwargs.get("variant"))
            await register(*args, **kwargs)

        monkeypatch.setattr(artifacts, "register_run", _counting)
        for _ in range(3):
            assert len((await c.get(ART, params={"log_id": log_id})).json()) == 1
        assert calls == []


@pytest.mark.asyncio
async def test_deleting_a_variant_of_an_unloaded_module_removes_it(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Installed but not loaded (a failed load, a reload window): the delete removes the
    variant's dir itself, so a list once the module is back does not index it again."""
    from mate.api.modules import get_module_loader

    async with mods_client(tmp_path, *PAIR) as c:
        log_id = await import_sample(c, name="p5 delete unloaded")
        pv = await _ready(c, log_id, "artifact_producer", filter=_ONLY_TWO)
        [row] = await _listed(c, log_id)
        with monkeypatch.context() as m:
            m.delitem(get_module_loader().loaded, "artifact_producer")
            assert (await c.delete(f"{ART}/{row['id']}")).status_code == 204
        cache = ResultCache(log_id, "artifact_producer", TEST_USER_ID, run_variant=pv["variant"])
        assert not cache.dir.exists() and await _row(row["id"]) is None
        assert (await c.get(ART, params={"log_id": log_id})).json() == []


@pytest.mark.asyncio
async def test_the_startup_backfill_indexes_old_results_once(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from mate.api.config import get_settings
    from mate.api.modules import artifacts, get_module_loader

    async def _refs() -> list[str]:
        async with get_sessionmaker()() as session:
            query = sa.select(Artifact.ref).where(Artifact.log_id == log_id)
            return list((await session.execute(query)).scalars())

    async def _forget() -> None:
        """What a result computed before the index looks like."""
        async with get_sessionmaker()() as session:
            await session.execute(sa.delete(Artifact).where(Artifact.log_id == log_id))
            await session.execute(
                sa.delete(SystemSetting).where(SystemSetting.key == artifacts.BACKFILL_KEY)
            )
            await session.commit()

    async with mods_client(tmp_path, *PAIR) as c:
        log_id = await import_sample(c, name="p5 backfill")
        await _default_ready(log_id, "artifact_producer")
        pv = await _ready(c, log_id, "artifact_producer", filter=_ONLY_TWO)
        refs = sorted(r["ref"] for r in await _listed(c, log_id, 2))
        manifests = {m: lm.manifest for m, lm in get_module_loader().loaded.items()}
        await _forget()
        await artifacts.backfill(manifests)  # off in the tests
        assert await _refs() == []

        monkeypatch.setattr(get_settings(), "artifacts_backfill", True)
        await artifacts.backfill(manifests)
        assert sorted(await _refs()) == refs
        assert f"{log_id}/artifact_producer@{pv['variant']}/model" in refs
        async with get_sessionmaker()() as session:
            assert await session.get(SystemSetting, artifacts.BACKFILL_KEY) is not None
            await session.execute(sa.delete(Artifact).where(Artifact.log_id == log_id))
            await session.commit()
        await artifacts.backfill(manifests)  # once
        assert await _refs() == []

        # A module not loaded at that boot (a failed load) is caught up on a later one.
        await _forget()
        partial = {m: man for m, man in manifests.items() if m != "artifact_producer"}
        await artifacts.backfill(partial)
        assert await _refs() == []
        await artifacts.backfill(manifests)
        assert sorted(await _refs()) == refs


@pytest.mark.asyncio
async def test_datasets_sharing_a_cache_key_index_one_artifact(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from mate.api.modules import get_module_loader

    async with mods_client(tmp_path, *PAIR) as c:
        log_id = await import_sample(c, name="p5 dup key")
        producer = get_module_loader().loaded["artifact_producer"].manifest
        net = {"artifact_type": "petri_net", "cache_key": "model"}
        twins = [DatasetEntry.model_validate({"id": i, **net}) for i in ("model", "again")]
        monkeypatch.setattr(producer, "datasets", twins)
        await _default_ready(log_id, "artifact_producer")
        [row] = await _listed(c, log_id)
        assert (row["dataset_id"], row["ref"]) == ("model", f"{log_id}/artifact_producer/model")
        assert row["producer_hash"]  # the run's own registration, not a list's discovery


# --------------------------------------------------------------------------- #
# Uploads
# --------------------------------------------------------------------------- #


@pytest.mark.asyncio
async def test_upload_validates_caps_and_checks_ownership(tmp_path: Path) -> None:
    from mate.api.config import get_settings

    async with mods_client(tmp_path, *PAIR) as c:
        log_id = await import_sample(c, name="p5 upload")
        up = await _upload(c, artifact_type="petri_net@1", name="Net", log_id=log_id, payload=_NET)
        assert up["ref"] == f"{log_id}/_workspace/{up['id']}"
        assert (up["source"], up["artifact_type"], up["type_version"]) == ("upload", "petri_net", 1)
        assert (up["module_id"], up["variant"], up["name"]) == (None, None, "Net")
        assert json.loads(workspace_file(TEST_USER_ID, up["id"]).read_text()) == _NET
        loose = await _upload(c, artifact_type="petri_net", payload=_NET)
        assert loose["ref"] == f"_/_workspace/{loose['id']}"
        assert loose["log_id"] is None and loose["name"] == "Petri net"

        # Multipart: the file's name is the default name.
        resp = await c.post(
            ART,
            files={"file": ("net.json", json.dumps(_NET).encode(), "application/json")},
            data={"artifact_type": "petri_net", "log_id": log_id},
        )
        assert resp.status_code == 201, resp.text
        assert resp.json()["name"] == "net.json"
        resp = await c.post(
            ART,
            files={"file": ("x.json", b"{nope", "application/json")},
            data={"artifact_type": "dfg"},
        )
        assert resp.status_code == 422

        foreign = await _foreign_log("upload")
        for body, code in (
            ({"artifact_type": "petri_net", "payload": {"places": []}}, 422),  # schema
            ({"artifact_type": "table", "payload": {}}, 422),  # Parquet-stored
            ({"artifact_type": "event_log", "payload": {}}, 422),
            ({"artifact_type": "nope", "payload": {}}, 422),
            ({"artifact_type": "petri_net", "payload": _NET, "log_id": foreign}, 404),
            ({"artifact_type": "petri_net"}, 422),  # no payload
        ):
            resp = await c.post(ART, json=body)
            assert resp.status_code == code, (body, resp.text)
        resp = await c.post(ART, json={"artifact_type": "petri_net", "payload": {"places": []}})
        assert "transitions" in resp.json()["detail"]

        settings = get_settings()
        prev = settings.artifact_upload_max_bytes
        settings.artifact_upload_max_bytes = 64
        try:
            resp = await c.post(ART, json={"artifact_type": "petri_net", "payload": _NET})
            assert resp.status_code == 413
            resp = await c.post(
                ART,
                files={"file": ("net.json", json.dumps(_NET).encode(), "application/json")},
                data={"artifact_type": "petri_net"},
            )
            assert resp.status_code == 413
        finally:
            settings.artifact_upload_max_bytes = prev

        # Someone else's artifact is a 404 everywhere.
        theirs = (await _foreign_upload("ownership")).rsplit("/", 1)[1]
        assert (await c.get(f"{ART}/{theirs}")).status_code == 404
        assert (await c.get(f"{ART}/{theirs}/content")).status_code == 404
        assert (await c.get(f"{ART}/{theirs}/download")).status_code == 404
        assert (await c.patch(f"{ART}/{theirs}", json={"pinned": True})).status_code == 404
        assert (await c.delete(f"{ART}/{theirs}")).status_code == 404
        assert theirs not in {r["id"] for r in (await c.get(ART)).json()}
        assert await _row(theirs) is not None


@pytest.mark.asyncio
async def test_upload_bounds_multipart_and_leaves_no_orphan(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from mate.api.routes import artifacts as route

    async with mods_client(tmp_path, *PAIR) as c:
        # A file name is no validated `name`: cut to the column's 255.
        resp = await c.post(
            ART,
            files={"file": ("n" * 400 + ".json", json.dumps(_NET).encode(), "application/json")},
            data={"artifact_type": "petri_net"},
        )
        assert resp.status_code == 201 and resp.json()["name"] == "n" * 255

        # Chunked multipart is refused before `request.form()` reads it.
        sent = 0

        async def _chunked() -> AsyncIterator[bytes]:
            nonlocal sent
            for chunk in (b"--x\r\n", b"y" * 65536, b"\r\n--x--\r\n"):
                sent += len(chunk)
                yield chunk

        resp = await c.post(
            ART, content=_chunked(), headers={"content-type": "multipart/form-data; boundary=x"}
        )
        assert resp.status_code == 411 and sent < 65536

        # A failure once the file is written takes the file with it.
        workspace = workspace_file(TEST_USER_ID, "probe").parent
        before = set(workspace.iterdir())

        def _boom(*_args: Any) -> str:
            raise RuntimeError("boom")

        monkeypatch.setattr(route, "workspace_ref", _boom)
        with pytest.raises(RuntimeError, match="boom"):
            await c.post(ART, json={"artifact_type": "petri_net", "payload": _NET})
        assert set(workspace.iterdir()) == before


@pytest.mark.asyncio
async def test_a_variant_binds_a_workspace_artifact_end_to_end(tmp_path: Path) -> None:
    async with mods_client(tmp_path, *PAIR) as c:
        log_id = await import_sample(c, name="p5 workspace input")
        up = await _upload(c, artifact_type="petri_net", log_id=log_id, payload=_NET)
        done = await _ready(c, log_id, "artifact_consumer", inputs={"reference_model": up["ref"]})
        seen = await ResultCache(
            log_id, "artifact_consumer", TEST_USER_ID, run_variant=done["variant"]
        ).get("seen")
        assert seen == {"ports": ["reference_model"], "ref": up["ref"], "places": 3}

        bpmn = await _upload(c, artifact_type="bpmn", payload={"xml": "<definitions/>"})
        theirs = await _foreign_upload("bind")
        for ref in (
            bpmn["ref"],  # wrong type
            theirs,  # someone else's
            f"{log_id}/_workspace/{bpmn['id']}",  # not its ref
            "_/_workspace/p5-no-such-artifact",
        ):
            resp = await c.post(
                _url(log_id, "artifact_consumer"), json={"inputs": {"reference_model": ref}}
            )
            assert resp.status_code == 422, (ref, resp.text)

        # A deleted upload binds nothing any more.
        assert (await c.delete(f"{ART}/{up['id']}")).status_code == 204
        resp = await c.post(
            _url(log_id, "artifact_consumer"),
            json={"inputs": {"reference_model": up["ref"]}, "filter": []},
        )
        assert resp.status_code == 422, resp.text


# --------------------------------------------------------------------------- #
# Reads by ref, consumers, delete, GC
# --------------------------------------------------------------------------- #


@pytest.mark.asyncio
async def test_content_by_ref_serves_only_the_users_own_artifacts(tmp_path: Path) -> None:
    async with mods_client(tmp_path, *PAIR) as c:
        log_id = await import_sample(c, name="p5 by ref")
        await _default_ready(log_id, "artifact_producer")
        pv = await _ready(c, log_id, "artifact_producer", filter=_ONLY_TWO)
        up = await _upload(c, artifact_type="petri_net", payload=_NET)

        async def _get(ref: str) -> Any:
            return await c.get(f"{ART}/content", params={"ref": ref})

        default = await _get(f"{log_id}/artifact_producer/model")
        assert default.status_code == 200 and len(default.json()["places"]) == 4
        variant = await _get(f"{log_id}/artifact_producer@{pv['variant']}/model")
        assert variant.status_code == 200 and len(variant.json()["places"]) == 2
        assert (await _get(up["ref"])).json() == _NET
        indexed = (await _listed(c, log_id, 2, artifact_type="petri_net", pinned=False))[0]
        by_id = await c.get(f"{ART}/{indexed['id']}/content")
        assert by_id.status_code == 200 and "places" in by_id.json()

        foreign = await _foreign_log("byref")
        await ResultCache(foreign, "artifact_producer", OTHER_ID).set("model", _NET)
        never = run_variant_key({"params": {"never": 1}})
        for ref, code in (
            (f"{foreign}/artifact_producer/model", 404),  # someone else's log
            (await _foreign_upload("byref"), 404),  # someone else's upload
            (f"{log_id}/_workspace/{up['id']}", 404),  # not the upload's ref
            (f"{log_id}/artifact_producer/__meta", 404),  # not a declared output
            (f"{log_id}/artifact_producer@ABCDEF0123456789/model", 404),
            (f"{log_id}/artifact_producer@{never}/model", 404),  # never produced
            (f"{log_id}/ghost_mod/model", 404),
            (f"{log_id}/artifact_producer/../model", 422),
            ("model", 422),
        ):
            assert (await _get(ref)).status_code == code, ref


@pytest.mark.asyncio
async def test_a_shared_dashboards_viewer_reads_the_owners_default_results(
    tmp_path: Path,
) -> None:
    from .test_module_processing import FIXTURES
    from .test_shared_read_only import (
        VIEWER_ID,
        _client,  # pyright: ignore[reportPrivateUsage]
        _share_with_viewer,  # pyright: ignore[reportPrivateUsage]
    )

    async with _client(tmp_path, tuple(FIXTURES / "modules" / m for m in PAIR)) as (c, who):
        log_id = await import_sample(c, name="p5 shared viewer")
        await _default_ready(log_id, "artifact_producer")
        pv = await _ready(c, log_id, "artifact_producer", filter=_ONLY_TWO)
        up = await _upload(c, artifact_type="petri_net", log_id=log_id, payload=_NET)
        default = f"{log_id}/artifact_producer/model"

        async def _get(ref: str) -> Any:
            return await c.get(f"{ART}/content", params={"ref": ref})

        who["id"] = VIEWER_ID
        assert (await _get(default)).status_code == 404  # not shared yet
        await _share_with_viewer(log_id)
        resp = await _get(default)
        assert resp.status_code == 200 and len(resp.json()["places"]) == 4
        # A variant's and an upload stay the owner's.
        for ref in (f"{log_id}/artifact_producer@{pv['variant']}/model", up["ref"]):
            assert (await _get(ref)).status_code == 404, ref
        # Like a route dataset, it needs the module installed.
        async with get_sessionmaker()() as session:
            await session.execute(
                sa.delete(ModuleInstall).where(
                    ModuleInstall.user_id == VIEWER_ID,
                    ModuleInstall.module_id == "artifact_producer",
                )
            )
            await session.commit()
        assert (await _get(default)).status_code == 404


@pytest.mark.asyncio
async def test_an_uninstalled_producers_artifacts_leave_the_workspace(tmp_path: Path) -> None:
    from mate.api.mcp.toolsets import analysis
    from mate.api.modules.installs import record_install

    from .test_mcp_analysis import (
        _ctx,  # pyright: ignore[reportPrivateUsage]
        _principal,  # pyright: ignore[reportPrivateUsage]
        _seed_log,  # pyright: ignore[reportPrivateUsage]
    )

    async with mods_client(tmp_path, *PAIR) as c:
        log_id = await import_sample(c, name="p5 uninstall")
        await _default_ready(log_id, "artifact_producer")
        pv = await _ready(c, log_id, "artifact_producer", filter=_ONLY_TWO)
        rows = await _listed(c, log_id, 2)
        prod = next(r for r in rows if r["variant"] is None)
        var = next(r for r in rows if r["variant"] == pv["variant"])
        assert (await c.patch(f"{ART}/{var['id']}", json={"pinned": True})).status_code == 200
        resp = await c.delete(f"{ART}/{prod['id']}")
        assert resp.status_code == 409 and "uninstall" not in resp.json()["detail"]

        assert (await c.delete("/api/v1/modules/artifact_producer")).status_code == 204
        assert await _row(prod["id"]) is None and await _row(var["id"]) is None
        assert (await c.get(ART, params={"log_id": log_id})).json() == []
        # Reinstalled, the next list - an MCP one too - indexes its results again: the
        # default's and the ready variant's, its lineage from the meta (the pin is lost).
        async with get_sessionmaker()() as session:
            await record_install(session, TEST_USER_ID, "artifact_producer", "upload")
            await session.commit()
        await _seed_log(log_id)  # MCP egress consent
        listed = await analysis.list_artifacts(_ctx(_principal()), log_id=log_id)
        assert sorted((e["ref"], e["pinned"]) for e in listed) == sorted(
            [(prod["ref"], False), (var["ref"], False)]
        )
        rows = await _listed(c, log_id, 2)
        back = next(r for r in rows if r["variant"] is None)
        var_back = next(r for r in rows if r["variant"] == pv["variant"])
        assert [var_back[k] for k in ("spec", "inputs", "producer_hash", "pinned")] == [
            var["spec"],
            var["inputs"],
            var["producer_hash"],
            False,
        ]
        # A row of a module no longer installed (however that came about) is hidden.
        async with get_sessionmaker()() as session:
            await session.execute(
                sa.delete(ModuleInstall).where(
                    ModuleInstall.user_id == TEST_USER_ID,
                    ModuleInstall.module_id == "artifact_producer",
                )
            )
            await session.commit()
        assert (await c.get(ART, params={"log_id": log_id})).json() == []
        for tail in ("", "/content", "/download"):
            assert (await c.get(f"{ART}/{back['id']}{tail}")).status_code == 404, tail
        assert (await c.get(f"{ART}/content", params={"ref": back["ref"]})).status_code == 404
        assert await _row(back["id"]) is not None


@pytest.mark.asyncio
async def test_download_serves_the_stored_file(tmp_path: Path) -> None:
    async with mods_client(tmp_path, *PAIR) as c:
        log_id = await import_sample(c, name="p5 download")
        await _default_ready(log_id, "artifact_producer")
        [prod] = await _listed(c, log_id)
        resp = await c.get(f"{ART}/{prod['id']}/download")
        assert resp.status_code == 200, resp.text
        assert resp.headers["content-type"] == "application/json"
        assert resp.headers["content-disposition"] == 'attachment; filename="model.json"'
        cache = ResultCache(log_id, "artifact_producer", TEST_USER_ID)
        assert resp.content == (cache.dir / "model.json").read_bytes()

        up = await _upload(c, artifact_type="petri_net", name="net.json", payload=_NET)
        resp = await c.get(f"{ART}/{up['id']}/download")
        assert resp.headers["content-disposition"] == 'attachment; filename="net.json"'
        assert resp.json() == _NET


@pytest.mark.asyncio
async def test_consumers_and_delete_semantics(tmp_path: Path) -> None:
    async with mods_client(tmp_path, *PAIR) as c:
        log_id = await import_sample(c, name="p5 delete")
        await _default_ready(log_id, "artifact_producer")
        pv = await _ready(c, log_id, "artifact_producer", filter=_ONLY_TWO)
        up = await _upload(c, artifact_type="petri_net", log_id=log_id, payload=_NET)
        rows = await _listed(c, log_id, 3)
        default = next(r for r in rows if r["source"] == "run" and r["variant"] is None)
        variant = next(r for r in rows if r["variant"] == pv["variant"])
        assert rows[0]["id"] == up["id"]  # newest first

        detail = (await c.get(f"{ART}/{default['id']}")).json()
        assert detail["consumers"] == [
            {
                "module_id": "artifact_consumer",
                "module_name": "Artifact Consumer",
                "port_id": "reference_model",
                "required": True,
            }
        ]
        assert detail["lineage"] == []

        resp = await c.delete(f"{ART}/{default['id']}")
        assert resp.status_code == 409
        resp = await c.delete(f"{ART}/{variant['id']}")
        assert resp.status_code == 204
        cache = ResultCache(log_id, "artifact_producer", TEST_USER_ID, run_variant=pv["variant"])
        assert not cache.dir.exists()
        assert (await c.get(_url(log_id, "artifact_producer", pv["variant"]))).status_code == 404
        resp = await c.delete(f"{ART}/{up['id']}")
        assert resp.status_code == 204
        assert not workspace_file(TEST_USER_ID, up["id"]).exists()
        assert [r["id"] for r in (await c.get(ART, params={"log_id": log_id})).json()] == [
            default["id"]
        ]
        assert (await c.patch(f"{ART}/{default['id']}", json={"name": ""})).status_code == 422


@pytest.mark.asyncio
async def test_a_pinned_variant_artifact_survives_the_gc(tmp_path: Path) -> None:
    async with mods_client(tmp_path, *PAIR) as c:
        log_id = await import_sample(c, name="p5 gc")
        kept = await _ready(c, log_id, "artifact_producer", filter=_ONLY_TWO)
        dropped = await _ready(c, log_id, "artifact_producer", filter=[])
        rows = await _listed(c, log_id, 2)
        pin = next(r for r in rows if r["variant"] == kept["variant"])
        other = next(r for r in rows if r["variant"] == dropped["variant"])
        resp = await c.patch(f"{ART}/{pin['id']}", json={"pinned": True})
        assert resp.status_code == 200 and resp.json()["pinned"] is True

        await _gc_everything()

        def _dir(v: dict[str, Any]) -> Path:
            return ResultCache(
                log_id, "artifact_producer", TEST_USER_ID, run_variant=v["variant"]
            ).dir

        assert _dir(kept).is_dir() and not _dir(dropped).exists()
        assert await _row(other["id"]) is None  # dropped eagerly, not on the next list
        assert await _row(pin["id"]) is not None
        # Leave no pinned variant behind: the data dir is per session, and it would
        # outlive every later test's GC.
        assert (await c.delete(f"{ART}/{pin['id']}")).status_code == 204
        assert not _dir(kept).exists()


# --------------------------------------------------------------------------- #
# Catalog, Parquet preview, migration
# --------------------------------------------------------------------------- #


@pytest.mark.asyncio
async def test_catalog_lists_cache_only_datasets_with_their_ref_key(tmp_path: Path) -> None:
    async with mods_client(tmp_path, *PAIR) as c:
        catalog = (await c.get("/api/v1/datasets/catalog")).json()
        entry = next(e for e in catalog if e["module_id"] == "artifact_producer")
        assert (entry["dataset_id"], entry["route"], entry["content_ref_key"]) == (
            "model",
            None,
            "model",
        )
        assert entry["artifact_type"] == "petri_net" and entry["shape"] == "graph"


async def test_parquet_content_is_a_preview_and_bytes_have_none(client: AsyncClient) -> None:
    from .test_mcp_analysis import (
        _ART_MANIFEST,  # pyright: ignore[reportPrivateUsage]
        _fake_module,  # pyright: ignore[reportPrivateUsage]
        _seed_log,  # pyright: ignore[reportPrivateUsage]
    )

    await _seed_log("p5-parquet-log")
    async with _fake_module("mcpan_art_mod", SimpleNamespace(), manifest=_ART_MANIFEST):
        cache = ResultCache("p5-parquet-log", "mcpan_art_mod", TEST_USER_ID)
        await cache.set("stats", pd.DataFrame({"activity": ["a"] * 250, "n": range(250)}))
        await cache.set("blob", b"\x00")
        url = f"{ART}/content?ref=p5-parquet-log/mcpan_art_mod"
        out = (await client.get(f"{url}/stats")).json()
        assert out["columns"] == ["activity", "n"]
        assert (len(out["rows"]), out["total_rows"], out["truncated"]) == (200, 250, True)
        assert out["rows"][0] == {"activity": "a", "n": 0}
        assert (await client.get(f"{url}/blob")).status_code == 415

        # The whole table is the download (the list indexes the entries on the way).
        listed = (await client.get(ART, params={"log_id": "p5-parquet-log"})).json()
        stats = next(r for r in listed if r["dataset_id"] == "stats")
        resp = await client.get(f"{ART}/{stats['id']}/download")
        assert resp.headers["content-type"] == "application/vnd.apache.parquet"
        assert resp.headers["content-disposition"] == 'attachment; filename="stats.parquet"'
        assert len(pd.read_parquet(io.BytesIO(resp.content))) == 250


def test_workspace_is_a_reserved_module_id() -> None:
    from mate.sdk.errors import ModuleManifestError
    from mate.sdk.manifest import Manifest

    with pytest.raises(ModuleManifestError, match="reserved"):
        Manifest.model_validate(
            {"id": "_workspace", "name": "W", "version": "1.0.0", "category": "other"}
        )


def _load_migration() -> Any:
    path = Path(__file__).resolve().parents[1] / "alembic" / "versions" / "0020_artifacts.py"
    spec = importlib.util.spec_from_file_location("mig_0020", path)
    assert spec is not None and spec.loader is not None
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def test_migration_up_down_up(monkeypatch: pytest.MonkeyPatch) -> None:
    mig = _load_migration()
    assert mig.down_revision == "0019_module_boards"
    eng = sa.create_engine("sqlite://")
    with eng.connect() as conn:
        conn.exec_driver_sql("CREATE TABLE users (id VARCHAR(36) PRIMARY KEY)")
        conn.exec_driver_sql("CREATE TABLE process_logs (id VARCHAR(36) PRIMARY KEY)")

        def run(fn: str) -> None:
            monkeypatch.setattr(mig, "op", Operations(MigrationContext.configure(conn)))
            getattr(mig, fn)()

        def indexes() -> set[str]:
            return {str(i["name"]) for i in sa.inspect(conn).get_indexes("artifacts")}

        run("upgrade")
        run("upgrade")  # idempotent
        assert indexes() == {
            "ux_artifacts_user_ref",
            "ix_artifacts_user_log",
            "ix_artifacts_user_type",
        }
        row = (
            "INSERT INTO artifacts (id, user_id, ref, artifact_type, type_version, source, key,"
            " name, inputs, created_at, updated_at) VALUES (:id, 'u', 'l/m/k', 'dfg', 1, 'run',"
            " 'k', 'k', '[]', datetime('now'), datetime('now'))"
        )
        conn.execute(sa.text(row), {"id": "a"})
        pinned = conn.exec_driver_sql("SELECT pinned FROM artifacts").scalar_one()
        assert pinned in (0, False)
        with pytest.raises(IntegrityError):  # (user_id, ref) is unique
            conn.execute(sa.text(row), {"id": "b"})

        run("downgrade")
        run("downgrade")  # idempotent
        assert "artifacts" not in sa.inspect(conn).get_table_names()
        run("upgrade")
        assert "artifacts" in sa.inspect(conn).get_table_names()
