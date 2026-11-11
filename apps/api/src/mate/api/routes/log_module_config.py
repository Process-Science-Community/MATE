"""/api/v1/event-logs/{log_id}/modules/{module_id}/config - a module's parameters for one log.

A ``config_schema`` property tagged ``ui.scope: dataset`` may be overridden per event
log (see ``mate.api.modules.log_config``); the module-wide value is the default every
log inherits. These two routes read and replace one log's overrides. Changing one that
is also tagged ``ui.affects: precompute`` makes the log's stored run stale, which the
``module-runs`` routes then report.
"""

from __future__ import annotations

from collections.abc import Iterable
from typing import Any

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, Field
from sqlalchemy.ext.asyncio import AsyncSession

from mate.api.auth import CurrentUserDep, get_owned_event_log
from mate.api.db.models import LogModuleConfig
from mate.api.db.session import SessionDep
from mate.api.modules import get_module_loader
from mate.api.modules.cards import card_key, card_owned_keys, derive_cards
from mate.api.modules.installs import user_owns_module
from mate.api.modules.log_config import dataset_properties, effective_config
from mate.api.policy import SCOPE_CARD, resolve
from mate.sdk.manifest import Manifest

router = APIRouter(prefix="/event-logs/{log_id}/modules/{module_id}/config", tags=["event-logs"])


class LogModuleConfigOut(BaseModel):
    # The module's `ui.scope: dataset` properties only, in the `config_schema` dialect.
    schema_: dict[str, Any] = Field(alias="schema")
    # This log's stored overrides; a missing key inherits the module-wide value.
    overrides: dict[str, Any]
    # The module-wide value of each property (schema default when unset).
    module_values: dict[str, Any]
    # What the module uses for this log - overrides win over module values, admin
    # locks win over both.
    effective: dict[str, Any]
    # The properties an admin-locked card owns: read-only here, a PUT keeps their
    # stored override.
    locked: list[str]


class LogModuleConfigIn(BaseModel):
    # Replaces every override of this log; an omitted or null key inherits.
    overrides: dict[str, Any] = Field(default_factory=dict)


def _with_defaults(cfg: dict[str, Any], props: dict[str, dict[str, Any]]) -> dict[str, Any]:
    return {
        k: cfg[k] if k in cfg else p["default"]
        for k, p in props.items()
        if k in cfg or "default" in p
    }


async def _manifest(session: AsyncSession, log_id: str, module_id: str, user_id: str) -> Manifest:
    """404 unless the user owns both the log and a loaded install of the module."""
    await get_owned_event_log(session, log_id, user_id)
    loaded = get_module_loader().loaded.get(module_id)
    if loaded is None or not await user_owns_module(session, user_id, module_id):
        raise HTTPException(status_code=404, detail=f"Module {module_id!r} is not installed.")
    return loaded.manifest


async def _locked_keys(
    session: AsyncSession, manifest: Manifest, user_id: str, keys: Iterable[str]
) -> set[str]:
    """The *keys* owned by an admin-locked card (the module-wide PUT's rule)."""
    cards = derive_cards(manifest)
    probe = dict.fromkeys(keys)
    out: set[str] = set()
    for card in cards:
        _, locked = await resolve(session, SCOPE_CARD, card_key(manifest.id, card.card_id), user_id)
        if locked:
            out |= card_owned_keys(card, cards, probe)
    return out


async def _out(
    session: AsyncSession, manifest: Manifest, user_id: str, log_id: str
) -> LogModuleConfigOut:
    props = dataset_properties(manifest)
    row = await session.get(LogModuleConfig, (user_id, log_id, manifest.id))
    stored = row.config_json if row is not None and row.config_json else {}
    overrides = {k: v for k, v in stored.items() if k in props}
    module_cfg, _ = await effective_config(session, manifest, user_id, None)
    effective, _ = await effective_config(session, manifest, user_id, log_id, overrides=overrides)
    return LogModuleConfigOut(
        schema={"type": "object", "properties": props},
        overrides=overrides,
        module_values=_with_defaults(module_cfg, props),
        effective=_with_defaults(effective, props),
        locked=sorted(await _locked_keys(session, manifest, user_id, props)),
    )


@router.get("", response_model=LogModuleConfigOut)
async def get_log_module_config(
    log_id: str, module_id: str, session: SessionDep, user: CurrentUserDep
) -> LogModuleConfigOut:
    """The module's per-log parameters for this log: what may be overridden, what is."""
    manifest = await _manifest(session, log_id, module_id, user.id)
    return await _out(session, manifest, user.id, log_id)


@router.put("", response_model=LogModuleConfigOut)
async def put_log_module_config(
    log_id: str,
    module_id: str,
    body: LogModuleConfigIn,
    session: SessionDep,
    user: CurrentUserDep,
) -> LogModuleConfigOut:
    """Replace this log's overrides. 422 for a key that is not a per-log property;
    a key an admin-locked card owns keeps its stored override."""
    manifest = await _manifest(session, log_id, module_id, user.id)
    props = dataset_properties(manifest)
    unknown = sorted(set(body.overrides) - set(props))
    if unknown:
        raise HTTPException(
            status_code=422,
            detail=f"Not a per-log parameter of {module_id!r}: {', '.join(unknown)}.",
        )
    overrides = {k: v for k, v in body.overrides.items() if v is not None}
    row = await session.get(LogModuleConfig, (user.id, log_id, module_id))
    stored = {k: v for k, v in (row.config_json if row else {}).items() if k in props}
    # A locked card is read-only: keep what was stored for its keys, ignore the change
    # (the module-wide PUT's rule) - the admin value wins at runtime anyway.
    for k in await _locked_keys(session, manifest, user.id, {*stored, *overrides}):
        if k in stored:
            overrides[k] = stored[k]
        else:
            overrides.pop(k, None)
    if not overrides:
        if row is not None:
            await session.delete(row)
    elif row is None:
        session.add(
            LogModuleConfig(
                user_id=user.id, log_id=log_id, module_id=module_id, config_json=overrides
            )
        )
    else:
        row.config_json = overrides
    await session.commit()
    return await _out(session, manifest, user.id, log_id)
