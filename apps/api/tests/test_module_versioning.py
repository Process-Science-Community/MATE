"""Module versioning (P8): SemVer manifests, version specifiers on hard deps, the
contract-diff bump check, staged uploads with rollback, and the versions API."""

from __future__ import annotations

import importlib.util
import io
import json
import subprocess
import sys
import zipfile
from collections.abc import AsyncIterator
from datetime import UTC, datetime
from pathlib import Path
from types import ModuleType, SimpleNamespace
from typing import Any

import pytest
import yaml
from httpx import AsyncClient
from structlog.testing import capture_logs

from mate.api.db.models import Artifact
from mate.api.modules import get_module_loader
from mate.api.modules.content_hash import read_content_hash, write_content_hash
from mate.api.modules.discovery import DiscoveredModule, discover, topo_sort
from mate.api.modules.maintenance import gc_orphaned_uploaded_modules, recover_install_staging
from mate.api.routes.artifacts import _out  # pyright: ignore[reportPrivateUsage]
from mate.sdk.cli import main as cli_main
from mate.sdk.errors import ModuleManifestError
from mate.sdk.manifest import Manifest, ModuleRequirement
from tests.conftest import (
    TEST_USER_ID,
    _sample_mod_client,  # pyright: ignore[reportPrivateUsage]
)
from tests.guide_text import GUIDE_FILENAME, minimal_guide
from tests.test_modules_per_user import _await_job  # pyright: ignore[reportPrivateUsage]

REPO = Path(__file__).resolve().parents[3]


def _manifest(
    module_id: str, version: str = "1.0.0", requires: list[str] | None = None
) -> Manifest:
    return Manifest.model_validate(
        {
            "id": module_id,
            "name": module_id,
            "version": version,
            "category": "other",
            "requirements": {"modules": requires or []},
        }
    )


def _manifest_yaml(module_id: str) -> str:
    return yaml.safe_dump(
        {"id": module_id, "name": module_id, "version": "1.0.0", "category": "other"}
    )


# -- SemVer + specifiers --------------------------------------------------------


@pytest.mark.parametrize("version", ["1.0.0", "0.1.0", "1.2.3-rc.1", "2.0.0+build.7", "10.20.30"])
def test_semver_versions_validate(version: str) -> None:
    assert _manifest("m", version).version == version


@pytest.mark.parametrize("version", ["1", "1.0", "v1.0.0", "01.0.0", "1.0.0-", "1.0.0." + "9" * 70])
def test_non_semver_versions_fail(version: str) -> None:
    with pytest.raises(ModuleManifestError, match="SemVer"):
        _manifest("m", version)


def test_requirement_specifiers_parse() -> None:
    req = ModuleRequirement.parse("discovery>=1,<2")
    assert req.id == "discovery"
    assert req.allows("1.4.0") and req.allows("1.5.0-rc.1") and not req.allows("2.0.0")
    assert ModuleRequirement.parse("cv4cdd").allows("0.0.1")
    m = _manifest("m", requires=["discovery >=1, <2", "cv4cdd"])
    assert m.requirements.module_ids == ["discovery", "cv4cdd"]
    for bad in ("Discovery", "discovery>=", "discovery ~ 1"):
        with pytest.raises(ModuleManifestError, match=r"requirements\.modules"):
            _manifest("m", requires=[bad])


def test_topo_sort_skips_unmet_version_constraints() -> None:
    mods = [
        _manifest("base", "1.5.0"),
        _manifest("fits", requires=["base>=1,<2"]),
        _manifest("too_new", requires=["base>=2"]),
        _manifest("behind", requires=["too_new"]),
        _manifest("bare", requires=["base"]),
    ]
    with capture_logs() as logs:
        ordered = topo_sort(DiscoveredModule(folder=Path(m.id), manifest=m) for m in mods)
    assert [d.id for d in ordered] == ["base", "bare", "fits"]
    unmet = [e for e in logs if e["event"] == "modules.discovery.requirement_version_unmet"]
    assert unmet == [
        {
            "event": "modules.discovery.requirement_version_unmet",
            "log_level": "error",
            "module_id": "too_new",
            "requires": "base>=2",
            "found": "1.5.0",
        }
    ]
    assert any(
        e["event"] == "modules.discovery.requirement_missing" and e["module_id"] == "behind"
        for e in logs
    )


def test_validate_checks_sibling_versions(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    for mid in ("dep_mod", "user_mod"):
        assert cli_main(["new", mid, "--dir", str(tmp_path)]) == 0
    path = tmp_path / "user_mod" / "manifest.yaml"
    data = yaml.safe_load(path.read_text())
    data.setdefault("requirements", {})["modules"] = ["dep_mod>=1"]
    path.write_text(yaml.safe_dump(data))
    capsys.readouterr()
    assert cli_main(["validate", str(tmp_path / "user_mod")]) == 1
    assert "dep_mod>=1 is not met by the sibling dep_mod 0.1.0" in capsys.readouterr().out
    data["requirements"]["modules"] = ["dep_mod>=0.1,<1"]
    path.write_text(yaml.safe_dump(data))
    assert cli_main(["validate", str(tmp_path / "user_mod")]) == 0


def test_boot_discovery_accepts_pre_semver_manifests(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    """A module uploaded before P8 (non-SemVer version, unparseable requirement) still
    loads at boot with a warning; `mate-module validate` stays strict."""
    old = tmp_path / "old_mod"
    old.mkdir()
    data = {
        "id": "old_mod",
        "name": "Old",
        "version": "1.0",
        "category": "other",
        "requirements": {"modules": ["base>=", "Weird-Dep", "base"]},
    }
    (old / "manifest.yaml").write_text(yaml.safe_dump(data))
    base = tmp_path / "base"
    base.mkdir()
    (base / "manifest.yaml").write_text(yaml.safe_dump({**data, "id": "base", "requirements": {}}))
    with capture_logs() as logs:
        found = {d.id: d for d in discover(tmp_path) if d.source == "filesystem"}
    assert found["old_mod"].manifest.version == "1.0"
    assert found["old_mod"].manifest.requirements.module_ids == ["base", "Weird-Dep", "base"]
    events = [(e["event"], e.get("treated_as")) for e in logs if e["log_level"] == "warning"]
    assert ("modules.manifest.version_not_semver", None) in events
    assert ("modules.manifest.requirement_invalid", "base") in events
    assert ("modules.manifest.requirement_invalid", "Weird-Dep") in events
    # Its bare-id fallback still has to resolve: `Weird-Dep` is missing, so it is skipped.
    assert [d.id for d in topo_sort(found.values())] == ["base"]

    with pytest.raises(ModuleManifestError, match="SemVer"):
        Manifest.load_yaml(old / "manifest.yaml")
    assert cli_main(["validate", str(old)]) == 1
    assert "SemVer" in capsys.readouterr().out


def test_boot_discovery_fixes_a_slashless_dataset_route(tmp_path: Path) -> None:
    """A dataset `route` without its leading "/" (accepted before the rule) gets one at
    boot, with a warning, instead of dropping the module; uploads stay strict."""
    old = tmp_path / "old_ds"
    old.mkdir()
    (old / "manifest.yaml").write_text(
        yaml.safe_dump(
            {
                "id": "old_ds",
                "name": "Old",
                "version": "1.0.0",
                "category": "other",
                "datasets": [{"id": "t", "shape": "table", "route": "table"}],
            }
        )
    )
    with capture_logs() as logs:
        [found] = [d for d in discover(tmp_path) if d.source == "filesystem"]
    assert found.manifest.datasets[0].route == "/table"
    assert any(e["event"] == "modules.manifest.dataset_route_slash" for e in logs)
    with pytest.raises(ModuleManifestError, match="must start with '/'"):
        Manifest.load_yaml(old / "manifest.yaml")


@pytest.mark.parametrize(
    "folder", sorted(p.parent.name for p in (REPO / "modules").glob("*/manifest.yaml"))
)
def test_bundled_manifests_are_semver(folder: str) -> None:
    Manifest.load_yaml(REPO / "modules" / folder / "manifest.yaml")


# -- scripts/check-module-versions.py --------------------------------------------


def _script() -> ModuleType:
    spec = importlib.util.spec_from_file_location(
        "check_module_versions", REPO / "scripts" / "check-module-versions.py"
    )
    assert spec is not None and spec.loader is not None
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


_MODULE_PY = """\
from mate.sdk import Module, route


class Foo(Module):
    id = "foo"

    @route.get("/a")
    async def a(self, ctx):
        return {BODY}
"""


def _git(repo: Path, *args: str) -> None:
    subprocess.run(
        ["git", "-c", "user.name=t", "-c", "user.email=t@t", "-C", str(repo), *args],
        check=True,
        capture_output=True,
    )


@pytest.fixture
def scratch_repo(tmp_path: Path) -> Path:
    folder = tmp_path / "modules" / "foo"
    folder.mkdir(parents=True)
    (folder / "manifest.yaml").write_text(
        yaml.safe_dump(
            {
                "id": "foo",
                "name": "Foo",
                "version": "1.0.0",
                "category": "other",
                "datasets": [{"id": "d", "shape": "table", "route": "/a"}],
            }
        )
    )
    (folder / "module.py").write_text(_MODULE_PY.replace("{BODY}", "1"))
    _git(tmp_path, "init", "-q")
    _git(tmp_path, "add", ".")
    _git(tmp_path, "commit", "-qm", "base")
    return tmp_path


def _edit(repo: Path, version: str, **manifest: Any) -> None:
    path = repo / "modules" / "foo" / "manifest.yaml"
    data = yaml.safe_load(path.read_text())
    path.write_text(yaml.safe_dump({**data, **manifest, "version": version}))


def test_check_script_patch(scratch_repo: Path) -> None:
    check = _script().check
    assert check(scratch_repo, "HEAD") == []
    (scratch_repo / "modules" / "foo" / "module.py").write_text(_MODULE_PY.replace("{BODY}", "2"))
    [(ok, line)] = check(scratch_repo, "HEAD")
    assert not ok and "needs patch: files changed" in line
    _edit(scratch_repo, "1.0.1")
    assert check(scratch_repo, "HEAD")[0][0]


def test_check_script_minor(scratch_repo: Path) -> None:
    check = _script().check
    (scratch_repo / "modules" / "foo" / "module.py").write_text(
        _MODULE_PY.replace("{BODY}", "1")
        + '\n    @route.post(\n        path="/b"\n    )\n    async def b(self, ctx):\n        return 1\n'
    )
    _edit(scratch_repo, "1.0.1")
    [(ok, line)] = check(scratch_repo, "HEAD")
    assert not ok and "needs minor: routes: added POST /b" in line
    _edit(scratch_repo, "1.1.0")
    assert check(scratch_repo, "HEAD")[0][0]


def test_check_script_major(scratch_repo: Path) -> None:
    check = _script().check
    _edit(scratch_repo, "1.1.0", datasets=[], requirements={"modules": ["discovery>=1"]})
    [(ok, line)] = check(scratch_repo, "HEAD")
    assert not ok
    assert "needs major: datasets: removed d; hard dep added: discovery>=1" in line
    _edit(scratch_repo, "2.0.0", datasets=[])
    assert check(scratch_repo, "HEAD")[0][0]
    # In 0.x a minor bump may break.
    _git(scratch_repo, "commit", "-qam", "2.0.0")
    _edit(scratch_repo, "0.1.0")
    _git(scratch_repo, "commit", "-qam", "0.1.0")
    _edit(scratch_repo, "0.2.0", inputs=[{"id": "p", "artifact_type": "dfg", "required": True}])
    assert check(scratch_repo, "HEAD")[0][0]


def test_check_script_cli_exit_codes(scratch_repo: Path) -> None:
    run = [sys.executable, str(REPO / "scripts" / "check-module-versions.py"), "--repo"]
    assert subprocess.run([*run, str(scratch_repo), "--base", "HEAD"]).returncode == 0
    (scratch_repo / "modules" / "foo" / "extra.py").write_text("x = 1\n")  # untracked
    assert subprocess.run([*run, str(scratch_repo), "--base", "HEAD"]).returncode == 1
    assert subprocess.run([*run, str(scratch_repo), "--base", "nope"]).returncode == 2


@pytest.mark.parametrize("version", ["1.0.0-rc.1", "1.0.0+b2", "1.0"])
def test_check_script_needs_a_higher_core_version(scratch_repo: Path, version: str) -> None:
    (scratch_repo / "modules" / "foo" / "module.py").write_text(_MODULE_PY.replace("{BODY}", "2"))
    _edit(scratch_repo, version)
    [(ok, line)] = _script().check(scratch_repo, "HEAD")
    assert not ok and "needs patch" in line
    bump = _script().bump
    assert bump("1.0.0-rc.1", "1.0.0") == "none"  # prerelease-only: no bump
    assert bump("1.0.0", "1.0.1-rc.1") == "patch"
    assert bump("1.2.0", "1.1.9") is None


def test_check_script_ignores_written_out_defaults(scratch_repo: Path) -> None:
    _edit(
        scratch_repo,
        "1.0.1",
        runtime={"kind": "python"},
        dependencies={"python": {"isolation": "in_process"}},
        requirements={"event_log": {"log_model": "case_centric"}},
    )
    [(ok, line)] = _script().check(scratch_repo, "HEAD")
    assert ok and "needs patch: files changed" in line


# -- staged upload, automatic rollback, admin rollback, versions API ------------


def _versioned_zip(
    module_id: str,
    version: str,
    *,
    broken: bool = False,
    changelog: str | None = None,
    requires: list[str] | None = None,
) -> bytes:
    buf = io.BytesIO()
    manifest: dict[str, Any] = {
        "id": module_id,
        "name": module_id,
        "version": version,
        "category": "foundation",
    }
    if requires:
        manifest["requirements"] = {"modules": requires}
    with zipfile.ZipFile(buf, mode="w") as zf:
        zf.writestr(f"{module_id}/manifest.yaml", yaml.safe_dump(manifest))
        zf.writestr(
            f"{module_id}/module.py",
            ("import does_not_exist_anywhere\n" if broken else "")
            + "from mate.sdk import Module, ModuleContext, route\n\n"
            "class TheModule(Module):\n"
            f'    id = "{module_id}"\n\n'
            '    @route.get("/ping")\n'
            "    async def ping(self, ctx: ModuleContext) -> dict[str, str]:\n"
            f'        return {{"version": "{version}"}}\n',
        )
        zf.writestr(f"{module_id}/{GUIDE_FILENAME}", minimal_guide(module_id))
        if changelog is not None:
            zf.writestr(f"{module_id}/CHANGELOG.md", changelog)
    return buf.getvalue()


async def _upload(client: AsyncClient, module_id: str, version: str, **kw: Any) -> dict[str, Any]:
    resp = await client.post(
        "/api/v1/modules/install",
        files={"file": ("m.zip", _versioned_zip(module_id, version, **kw), "application/zip")},
    )
    assert resp.status_code == 202, resp.text
    return await _await_job(client, resp.json()["job_id"])


async def _ping(client: AsyncClient, module_id: str) -> str:
    resp = await client.get(f"/api/v1/modules/{module_id}/ping")
    assert resp.status_code == 200, resp.text
    return resp.json()["version"]


def _uploads() -> Path:
    return get_module_loader().uploaded_modules_dir


@pytest.fixture
async def admin_client(tmp_path: Path) -> AsyncIterator[AsyncClient]:
    async with _sample_mod_client(tmp_path, seed=True, admin=True) as c:
        yield c


async def test_upgrade_keeps_previous_and_history(admin_client: AsyncClient) -> None:
    mid = "ver_upgrade"
    assert (await _upload(admin_client, mid, "1.0.0"))["status"] == "completed"
    assert (await _upload(admin_client, mid, "2.0.0", changelog="## 2.0.0\n- faster"))[
        "status"
    ] == "completed"
    assert await _ping(admin_client, mid) == "2.0.0"
    assert not (_uploads() / ".staging" / mid).exists()
    assert Manifest.load_yaml(_uploads() / ".prev" / mid / "manifest.yaml").version == "1.0.0"
    history = json.loads((_uploads() / ".history" / f"{mid}.json").read_text())
    assert [(h["version"], h["user_id"]) for h in history] == [
        ("1.0.0", TEST_USER_ID),
        ("2.0.0", TEST_USER_ID),
    ]
    assert all(len(h["content_hash"]) > 10 and h["installed_at"] for h in history)

    body = (await admin_client.get(f"/api/v1/modules/{mid}/versions")).json()
    assert body["current"] == {"version": "2.0.0", "content_hash": history[1]["content_hash"]}
    assert [h["version"] for h in body["history"]] == ["2.0.0", "1.0.0"]
    assert body["previous_available"] is True and body["previous_version"] == "1.0.0"
    assert body["changelog"] == "## 2.0.0\n- faster"

    # Discovery and the uploaded-module GC never see the dot dirs.
    assert [d.id for d in discover(_uploads()) if d.source == "filesystem"] == [mid]
    assert gc_orphaned_uploaded_modules(_uploads(), {mid}) == []
    assert (_uploads() / ".prev" / mid).exists()

    # Re-uploading the same code keeps 1.0.0 as the version to roll back to.
    changelog = "## 2.0.0\n- faster"
    assert (await _upload(admin_client, mid, "2.0.0", changelog=changelog))["status"] == "completed"
    assert Manifest.load_yaml(_uploads() / ".prev" / mid / "manifest.yaml").version == "1.0.0"
    assert await _ping(admin_client, mid) == "2.0.0"


async def test_broken_upgrade_fails_before_the_swap(admin_client: AsyncClient) -> None:
    mid = "ver_broken"
    assert (await _upload(admin_client, mid, "1.0.0"))["status"] == "completed"
    job = await _upload(admin_client, mid, "2.0.0", broken=True)
    assert job["status"] == "failed"
    assert "does not import" in job["error"] and "does_not_exist_anywhere" in job["error"]
    assert await _ping(admin_client, mid) == "1.0.0"
    assert not (_uploads() / ".prev" / mid).exists()
    assert not (_uploads() / ".staging" / mid).exists()
    history = json.loads((_uploads() / ".history" / f"{mid}.json").read_text())
    assert [h["version"] for h in history] == ["1.0.0"]


async def test_failed_load_restores_the_previous_version(
    admin_client: AsyncClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    mid = "ver_restore"
    assert (await _upload(admin_client, mid, "1.0.0"))["status"] == "completed"
    loader = get_module_loader()
    real_load_one = loader.load_one

    async def flaky_load_one(folder: Path, manifest: Manifest) -> Any:
        if manifest.version == "2.0.0":
            await loader.unload_one(manifest.id)  # fail the way a real load does: mid-way
            raise RuntimeError("boom")
        return await real_load_one(folder, manifest)

    monkeypatch.setattr(loader, "load_one", flaky_load_one)
    job = await _upload(admin_client, mid, "2.0.0")
    assert job["status"] == "failed"
    assert "2.0.0 failed to load (boom); restored 1.0.0" in job["error"]
    assert await _ping(admin_client, mid) == "1.0.0"
    assert Manifest.load_yaml(_uploads() / mid / "manifest.yaml").version == "1.0.0"
    assert not (_uploads() / ".staging" / mid).exists()
    history = json.loads((_uploads() / ".history" / f"{mid}.json").read_text())
    assert [h["version"] for h in history] == ["1.0.0"]


async def test_admin_rollback_and_forward_again(admin_client: AsyncClient) -> None:
    mid = "ver_rollback"
    assert (await admin_client.post(f"/api/v1/admin/modules/{mid}/rollback")).status_code == 404
    assert (await _upload(admin_client, mid, "1.0.0"))["status"] == "completed"
    assert (await admin_client.post(f"/api/v1/admin/modules/{mid}/rollback")).status_code == 409
    assert (await _upload(admin_client, mid, "2.0.0"))["status"] == "completed"

    resp = await admin_client.post(f"/api/v1/admin/modules/{mid}/rollback")
    assert resp.status_code == 200, resp.text
    assert resp.json()["version"] == "1.0.0"
    assert await _ping(admin_client, mid) == "1.0.0"
    body = (await admin_client.get(f"/api/v1/modules/{mid}/versions")).json()
    assert body["current"]["version"] == "1.0.0"
    assert body["previous_version"] == "2.0.0"
    assert [h["version"] for h in body["history"]] == ["1.0.0", "2.0.0", "1.0.0"]

    resp = await admin_client.post(f"/api/v1/admin/modules/{mid}/rollback")
    assert resp.status_code == 200 and resp.json()["version"] == "2.0.0"
    assert await _ping(admin_client, mid) == "2.0.0"
    assert not (_uploads() / ".staging" / mid).exists()

    # A bundled module is never rolled back.
    assert (await admin_client.post("/api/v1/admin/modules/sample_mod/rollback")).status_code == 400

    # The last owner's uninstall takes the kept version and the history along.
    assert (await admin_client.delete(f"/api/v1/modules/{mid}")).status_code == 204
    assert not (_uploads() / ".prev" / mid).exists()
    assert not (_uploads() / ".history" / f"{mid}.json").exists()


async def test_same_code_reupload_that_fails_to_load_keeps_the_live_folder(
    admin_client: AsyncClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    mid = "ver_same"
    for version in ("1.0.0", "2.0.0"):
        assert (await _upload(admin_client, mid, version))["status"] == "completed"
    loader = get_module_loader()
    real_load_one = loader.load_one
    calls: list[str] = []

    async def flaky_load_one(folder: Path, manifest: Manifest) -> Any:
        calls.append(manifest.version)
        if len(calls) == 1:
            await loader.unload_one(manifest.id)
            raise RuntimeError("transient")
        return await real_load_one(folder, manifest)

    monkeypatch.setattr(loader, "load_one", flaky_load_one)
    job = await _upload(admin_client, mid, "2.0.0")
    assert job["status"] == "failed" and "restored 2.0.0" in job["error"]
    assert await _ping(admin_client, mid) == "2.0.0"
    assert Manifest.load_yaml(_uploads() / mid / "manifest.yaml").version == "2.0.0"
    # The real rollback target survives, and nothing is left aside.
    assert Manifest.load_yaml(_uploads() / ".prev" / mid / "manifest.yaml").version == "1.0.0"
    assert sorted(p.name for p in (_uploads() / ".staging").iterdir()) == []


async def test_upgrade_and_rollback_refuse_to_break_dependents(admin_client: AsyncClient) -> None:
    for version in ("0.9.0", "1.0.0"):
        assert (await _upload(admin_client, "ver_base", version))["status"] == "completed"
    job = await _upload(admin_client, "ver_dep", "1.0.0", requires=["ver_base>=1,<2"])
    assert job["status"] == "completed", job.get("error")

    job = await _upload(admin_client, "ver_base", "2.0.0")
    assert job["status"] == "failed"
    assert "would break" in job["error"] and "ver_dep needs ver_base<2,>=1" in job["error"]
    assert await _ping(admin_client, "ver_base") == "1.0.0"

    resp = await admin_client.post("/api/v1/admin/modules/ver_base/rollback")
    assert resp.status_code == 409 and "ver_dep needs ver_base<2,>=1" in resp.json()["detail"]
    assert await _ping(admin_client, "ver_base") == "1.0.0"
    prev = _uploads() / ".prev" / "ver_base" / "manifest.yaml"
    assert Manifest.load_yaml(prev).version == "0.9.0"
    assert not (_uploads() / ".staging" / "ver_base").exists()

    # A version the specifier allows goes through.
    assert (await _upload(admin_client, "ver_base", "1.1.0"))["status"] == "completed"
    assert "ver_dep" in get_module_loader().loaded


def _boot_ids() -> list[str]:
    return [d.id for d in topo_sort(discover(_uploads())) if d.source == "filesystem"]


async def test_upload_refuses_its_own_unmet_requirement(admin_client: AsyncClient) -> None:
    assert (await _upload(admin_client, "ver_b2", "1.0.0"))["status"] == "completed"
    job = await _upload(admin_client, "ver_needs", "1.0.0", requires=["ver_b2>=2"])
    assert job["status"] == "failed"
    assert "needs ver_b2>=2 (loaded: 1.0.0)" in job["error"]
    assert "ver_needs" not in get_module_loader().loaded
    assert not (_uploads() / "ver_needs").exists()
    assert _boot_ids() == ["ver_b2"]


async def test_rollback_refuses_its_own_unmet_requirement(admin_client: AsyncClient) -> None:
    """X 1.0.0 needs base<2; X is upgraded to 2.0.0 (base>=1), then base to 2.0.0. Rolling
    X back to 1.0.0 would load now and be dropped at the next boot: 409 instead."""
    assert (await _upload(admin_client, "ver_rb_base", "1.0.0"))["status"] == "completed"
    for version, req in (("1.0.0", "ver_rb_base>=1,<2"), ("2.0.0", "ver_rb_base>=1")):
        job = await _upload(admin_client, "ver_rb_x", version, requires=[req])
        assert job["status"] == "completed", job.get("error")
    assert (await _upload(admin_client, "ver_rb_base", "2.0.0"))["status"] == "completed"
    resp = await admin_client.post("/api/v1/admin/modules/ver_rb_x/rollback")
    assert resp.status_code == 409
    assert "needs ver_rb_base<2,>=1 (loaded: 2.0.0)" in resp.json()["detail"]
    assert await _ping(admin_client, "ver_rb_x") == "2.0.0"
    prev = _uploads() / ".prev" / "ver_rb_x" / "manifest.yaml"
    assert Manifest.load_yaml(prev).version == "1.0.0"
    assert not (_uploads() / ".staging" / "ver_rb_x").exists()
    assert _boot_ids() == ["ver_rb_base", "ver_rb_x"]


async def test_same_code_reupload_after_rollback_keeps_a_distinct_prev(
    admin_client: AsyncClient,
) -> None:
    mid = "ver_rb_same"
    for version in ("1.0.0", "2.0.0"):
        assert (await _upload(admin_client, mid, version))["status"] == "completed"
    assert (await admin_client.post(f"/api/v1/admin/modules/{mid}/rollback")).status_code == 200
    assert (await _upload(admin_client, mid, "1.0.0"))["status"] == "completed"
    assert Manifest.load_yaml(_uploads() / ".prev" / mid / "manifest.yaml").version == "2.0.0"
    resp = await admin_client.post(f"/api/v1/admin/modules/{mid}/rollback")
    assert resp.status_code == 200 and resp.json()["version"] == "2.0.0"
    assert sorted(p.name for p in (_uploads() / ".staging").iterdir()) == []


async def test_failed_load_of_a_new_id_leaves_nothing(
    admin_client: AsyncClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    loader = get_module_loader()
    real_load_one = loader.load_one

    async def boom(folder: Path, manifest: Manifest) -> Any:
        if manifest.id == "ver_new":
            raise RuntimeError("nope")
        return await real_load_one(folder, manifest)

    monkeypatch.setattr(loader, "load_one", boom)
    job = await _upload(admin_client, "ver_new", "1.0.0")
    assert job["status"] == "failed" and "nothing to restore" in job["error"]
    assert not any("ver_new" in p.name for p in _uploads().iterdir())
    assert sorted(p.name for p in (_uploads() / ".staging").iterdir()) == []


async def test_rollback_of_a_module_that_failed_to_load(admin_client: AsyncClient) -> None:
    mid = "ver_unloaded"
    for version in ("1.0.0", "2.0.0"):
        assert (await _upload(admin_client, mid, version))["status"] == "completed"
    await get_module_loader().unload_one(mid)  # as when 2.0.0 fails to load at boot
    # The Versions card still gets what it needs to offer the rollback.
    body = (await admin_client.get(f"/api/v1/modules/{mid}/versions")).json()
    assert body["current"]["version"] == "2.0.0" and body["current"]["content_hash"]
    assert body["previous_available"] is True and body["previous_version"] == "1.0.0"
    assert [h["version"] for h in body["history"]] == ["2.0.0", "1.0.0"]
    resp = await admin_client.post(f"/api/v1/admin/modules/{mid}/rollback")
    assert resp.status_code == 200, resp.text
    assert resp.json()["version"] == "1.0.0"
    assert await _ping(admin_client, mid) == "1.0.0"
    assert Manifest.load_yaml(_uploads() / ".prev" / mid / "manifest.yaml").version == "2.0.0"


async def test_versions_of_an_unloaded_module_need_ownership_and_history(
    admin_client: AsyncClient,
) -> None:
    # Kept versions on disk, but no install row for this user: 404 like any non-owner.
    ghost = _uploads() / ".prev" / "ver_ghost"
    ghost.mkdir(parents=True)
    (ghost / "manifest.yaml").write_text(_manifest_yaml("ver_ghost"))
    assert (await admin_client.get("/api/v1/modules/ver_ghost/versions")).status_code == 404
    # A bundled module that is not loaded has nothing to roll back to.
    await get_module_loader().unload_one("sample_mod")
    assert (await admin_client.get("/api/v1/modules/sample_mod/versions")).status_code == 404


async def test_upload_stays_strict_on_semver(admin_client: AsyncClient) -> None:
    job = await _upload(admin_client, "ver_loose", "1.0")
    assert job["status"] == "failed" and "SemVer" in job["error"]


def test_gc_drops_kept_versions_with_the_module(tmp_path: Path) -> None:
    up = tmp_path / "uploaded"
    for mid in ("keep", "orphan"):
        (up / mid).mkdir(parents=True)
    for mid in ("keep", "orphan", "only_prev"):
        (up / ".prev" / mid).mkdir(parents=True)
    (up / ".history").mkdir()
    for mid in ("keep", "orphan", "only_history"):
        (up / ".history" / f"{mid}.json").write_text("[]")
    assert gc_orphaned_uploaded_modules(up, {"keep"}) == ["only_history", "only_prev", "orphan"]
    assert sorted(p.name for p in up.iterdir()) == [".history", ".prev", "keep"]
    assert [p.name for p in (up / ".prev").iterdir()] == ["keep"]
    assert [p.name for p in (up / ".history").iterdir()] == ["keep.json"]


def test_boot_recovers_a_swap_that_died_midway(tmp_path: Path) -> None:
    """What `install_staged` leaves in `.staging/` when the process dies mid-swap."""
    up = tmp_path / "uploaded"
    staging = up / ".staging"

    def folder(path: Path, module_id: str, digest: str) -> None:
        path.mkdir(parents=True)
        (path / "manifest.yaml").write_text(_manifest_yaml(module_id))
        write_content_hash(path, digest)

    folder(staging / "moved.live", "moved", "h-live")  # live folder aside, new not in yet
    folder(staging / "both.live", "both", "h-old")  # new one already swapped in
    folder(up / "both", "both", "h-new")
    folder(staging / "upload", "upload", "h-upload")  # an upload that never finished
    # A rollback that took `.prev/rolled` out but never swapped it in.
    folder(up / "rolled", "rolled", "h2")
    folder(staging / "rolled", "rolled", "h1")
    (up / ".history").mkdir()
    (up / ".history" / "rolled.json").write_text(
        json.dumps([{"version": "1.0.0", "content_hash": h} for h in ("h1", "h2")])
    )
    (staging / "stray.tmp").write_text("x")

    with capture_logs() as logs:
        recover_install_staging(up)
    assert read_content_hash(up / "moved") == "h-live"
    assert read_content_hash(up / "both") == "h-new"
    assert read_content_hash(up / ".prev" / "rolled") == "h1"
    assert read_content_hash(up / "rolled") == "h2"
    assert sorted(p.name for p in staging.iterdir()) == ["both.live"]
    events = {e["event"] for e in logs}
    assert {"modules.staging.live_restored", "modules.staging.live_kept"} <= events
    assert sorted(d.id for d in discover(up) if d.source == "filesystem") == [
        "both",
        "moved",
        "rolled",
    ]
    recover_install_staging(tmp_path / "missing")  # no uploads dir yet: a no-op


async def test_rollback_needs_admin(client_with_sample_mod: AsyncClient) -> None:
    resp = await client_with_sample_mod.post("/api/v1/admin/modules/sample_mod/rollback")
    assert resp.status_code == 403


async def test_versions_of_a_bundled_module(client_with_sample_mod: AsyncClient) -> None:
    body = (await client_with_sample_mod.get("/api/v1/modules/sample_mod/versions")).json()
    assert body == {
        "current": {"version": body["current"]["version"], "content_hash": None},
        "history": [],
        "previous_available": False,
        "previous_version": None,
        "changelog": None,
    }
    assert (await client_with_sample_mod.get("/api/v1/modules/nope/versions")).status_code == 404


# -- artifacts: current_version -------------------------------------------------


def test_artifact_out_carries_the_current_version() -> None:
    now = datetime.now(UTC)
    row = Artifact(
        id="a1",
        user_id="u",
        log_id="l",
        ref="l/discovery/petri_net",
        artifact_type="petri_net",
        type_version=1,
        source="run",
        module_id="discovery",
        key="petri_net",
        name="Petri net",
        pinned=False,
        producer_version="1.0.0",
        inputs=[],
        created_at=now,
        updated_at=now,
    )
    loaded = {"discovery": SimpleNamespace(manifest=SimpleNamespace(name="D", version="1.1.0"))}
    out = _out(row, loaded)
    assert (out.producer_version, out.current_version) == ("1.0.0", "1.1.0")
    assert _out(row, {}).current_version is None
