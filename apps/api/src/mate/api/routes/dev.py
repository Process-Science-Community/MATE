"""/api/v1/dev - lite dev mode helpers (docs/DEV_LITE.md).

Mounted by ``create_app`` only when ``settings.dev_tools_enabled`` (ENV=dev and
DEMO_MODE), so on any other deployment every path here is a plain 404. Feeds the
web Studio: module status, saving a module's ``layout.json``, forced reloads.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import structlog
from fastapi import APIRouter, HTTPException

from mate.api.auth import CurrentUserDep
from mate.api.config import get_settings
from mate.api.modules import ModuleLoader, get_module_loader
from mate.api.modules.discovery import DiscoveredModule
from mate.api.routes.modules import read_module_file_layout
from mate.sdk.errors import ModuleManifestError
from mate.sdk.layout import LAYOUT_FILENAME, ModuleLayout
from mate.sdk.manifest import Manifest

log = structlog.get_logger(__name__)

router = APIRouter(prefix="/dev", tags=["dev"])


def _entries(loader: ModuleLoader) -> dict[str, tuple[DiscoveredModule, str | None]]:
    """Every module the loader knows: loaded ones plus the ones that failed."""
    out: dict[str, tuple[DiscoveredModule, str | None]] = {
        mid: (d, err) for mid, (d, err) in loader.load_errors.items()
    }
    for mid, lm in loader.loaded.items():
        out[mid] = (lm.discovered, None)
    return out


def _status(d: DiscoveredModule, load_error: str | None) -> dict[str, Any]:
    m = d.manifest
    return {
        "id": m.id,
        "name": m.name,
        "folder": str(d.folder),
        "has_panel": bool(m.frontend.panel),
        "has_layout": (d.folder / LAYOUT_FILENAME).is_file(),
        "datasets": [ds.id for ds in m.datasets],
        "widgets": [w.id for w in m.frontend.widgets],
        "isolation": m.dependencies.python.isolation,
        "execution": m.dependencies.python.execution,
        "load_error": load_error,
    }


def _entry_or_404(loader: ModuleLoader, module_id: str) -> tuple[DiscoveredModule, str | None]:
    entry = _entries(loader).get(module_id)
    if entry is None:
        raise HTTPException(status_code=404, detail=f"Module {module_id!r} is not known.")
    return entry


@router.get("/status")
async def dev_status(user: CurrentUserDep) -> dict[str, Any]:
    settings = get_settings()
    loader = get_module_loader()
    return {
        "dev": True,
        "modules_only": sorted(settings.modules_only_ids),
        "debugpy_port": settings.debugpy_port,
        "dev_force_thread": settings.dev_force_thread,
        "modules": [_status(d, err) for _mid, (d, err) in sorted(_entries(loader).items())],
    }


@router.put("/modules/{module_id}/layout")
async def put_layout(module_id: str, layout: ModuleLayout, user: CurrentUserDep) -> dict[str, Any]:
    """Write ``modules/<id>/layout.json``. Only repo modules under ``modules_dir``;
    an upload's folder is never written."""
    d, _err = _entry_or_404(get_module_loader(), module_id)
    folder = Path(d.folder).resolve()
    if not folder.is_relative_to(get_settings().modules_dir.resolve()):
        raise HTTPException(
            status_code=403, detail=f"Module {module_id!r} is not under the modules dir."
        )
    layout.dump(folder)
    return {"layout": read_module_file_layout(folder)}


@router.post("/modules/{module_id}/reload")
async def reload_module(module_id: str, user: CurrentUserDep) -> dict[str, Any]:
    """Force the hot-reload path (``loader.load_one``) for one module. A failed
    load is reported in ``load_error``, not as an HTTP error."""
    loader = get_module_loader()
    d, _err = _entry_or_404(loader, module_id)
    try:
        manifest = Manifest.load_yaml(Path(d.folder) / "manifest.yaml")
    except ModuleManifestError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    async with await loader.id_lock(module_id):
        try:
            await loader.load_one(Path(d.folder), manifest)
        except Exception:
            # Also recorded in loader.load_errors and returned below.
            log.exception("modules.dev.reload_failed", module_id=module_id)
    return _status(*_entry_or_404(loader, manifest.id))
