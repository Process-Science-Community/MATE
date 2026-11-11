"""A module's effective config for one event log, and the run fingerprint built from it.

A ``config_schema`` property carries two independent flags under its ``ui`` object:

* ``ui.scope: dataset`` - the user may override the value per event log; the
  module-wide value (``module_configs``) is the default every log inherits.
  ``module`` (the default) is module-wide only.
* ``ui.affects: precompute`` - the module's precompute job reads it, so changing its
  effective value makes the stored per-log run stale (``modules.processing``).
  ``affects: compute`` (a route recomputes on demand) and ``view`` never do.

Merge order (:func:`effective_config`): the caller's module config - for a shared
log's viewer with the owner's value of every :func:`owner_keys` key on top - then the
log owner's per-log overrides (``scope: dataset`` keys only), then a run variant's spec
params (same keys), then each admin-locked card's slice - last, so no per-log or
per-run value gets around an admin policy. Schema defaults are not merged in:
modules apply their own.
"""

from __future__ import annotations

import hashlib
import json
from collections.abc import Iterable, Mapping
from typing import TYPE_CHECKING, Any, cast

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from mate.api.db.models import LogModuleConfig, ModuleConfig
from mate.api.modules.cards import AI_KEY, resolve_card_overlays
from mate.api.modules.installs import user_module_ids
from mate.sdk.manifest import Manifest

if TYPE_CHECKING:
    from mate.api.modules.loader import LoadedModule, ModuleLoader


def _properties(manifest: Manifest) -> dict[str, dict[str, Any]]:
    props = (manifest.config_schema or {}).get("properties")
    if not isinstance(props, dict):
        return {}
    return {
        str(k): cast(dict[str, Any], v)
        for k, v in cast(dict[Any, Any], props).items()
        if isinstance(v, dict)
    }


def _ui(prop: dict[str, Any]) -> dict[str, Any]:
    ui = prop.get("ui")
    return cast(dict[str, Any], ui) if isinstance(ui, dict) else {}


def _is_secret(node: Any) -> bool:
    """A schema node that is - or nests anywhere (``properties``, ``items``,
    ``additionalProperties``, ``anyOf``, ...) - a credential: ``ui.widget``
    password/secret, ``format: password``, ``writeOnly`` or a ``secret`` flag (on the
    node or its ``ui``). Walks every value, so a stray match only errs towards secret."""
    if isinstance(node, list):
        return any(_is_secret(n) for n in cast(list[Any], node))
    if not isinstance(node, dict):
        return False
    prop = cast(dict[str, Any], node)
    ui = _ui(prop)
    return (
        ui.get("widget") in ("password", "secret")
        or prop.get("format") == "password"
        or prop.get("writeOnly") is True
        or bool(prop.get("secret") or ui.get("secret"))
        or any(_is_secret(v) for v in prop.values())
    )


async def _module_cfg(session: AsyncSession, user_id: str, module_id: str) -> dict[str, Any]:
    row = await session.get(ModuleConfig, (user_id, module_id))
    return dict(row.config_json) if row is not None and row.config_json else {}


def dataset_properties(manifest: Manifest) -> dict[str, dict[str, Any]]:
    """The ``ui.scope: dataset`` properties - the ones a log may override."""
    return {k: p for k, p in _properties(manifest).items() if _ui(p).get("scope") == "dataset"}


def precompute_keys(manifest: Manifest) -> list[str]:
    """The config keys the run fingerprint covers: every ``ui.affects: precompute``
    property plus, with a ``model_store``, the selected model's key."""
    props = _properties(manifest)
    keys = {k for k, p in props.items() if _ui(p).get("affects") == "precompute"}
    if manifest.model_store is not None:
        keys.add(manifest.model_store.config_key)
    return sorted(keys)


def owner_keys(manifest: Manifest) -> set[str]:
    """The config keys a shared log's viewer takes from the owner: the ones that
    determine results - ``ui.affects`` compute/precompute, ``ui.scope: dataset`` and the
    ``model_store`` selection - minus ``ai`` and any secret. Everything else (connection
    settings, credentials, undeclared keys) stays the viewer's own, so a viewer's
    credential never pairs with an owner-chosen server."""
    props = _properties(manifest)
    keys = {
        k
        for k, p in props.items()
        if _ui(p).get("affects") in ("compute", "precompute") or _ui(p).get("scope") == "dataset"
    }
    if manifest.model_store is not None:
        keys.add(manifest.model_store.config_key)
    return {k for k in keys if k != AI_KEY and not _is_secret(props.get(k))}


def normalised(value: Any) -> Any:
    """Integral floats as ints, so ``1`` and ``1.0`` hash alike. Every other number (and
    bool) stays as is: ints never become floats, which would merge ints above 2**53 and
    overflow on huge ones."""
    if isinstance(value, float) and value.is_integer():
        return int(value)
    if isinstance(value, dict):
        return {str(k): normalised(v) for k, v in cast(dict[Any, Any], value).items()}
    if isinstance(value, list | tuple):
        return [normalised(v) for v in cast(list[Any], value)]
    return value


def run_fingerprint(compute_hash: str, manifest: Manifest, config: dict[str, Any]) -> str:
    """*compute_hash* for a module without precompute config (unchanged semantics);
    otherwise a hash over it and each precompute key's effective value (the schema
    default when unset)."""
    keys = precompute_keys(manifest)
    if not keys:
        return compute_hash
    # shortcut: model_store hashes the selected model name, not its files - replacing a model
    # under the same name, or an implicit default, does not stale runs; hash files if it matters.
    props = _properties(manifest)
    values = {k: normalised(config.get(k, props.get(k, {}).get("default"))) for k in keys}
    digest = hashlib.blake2b(digest_size=32)
    digest.update(compute_hash.encode())
    digest.update(json.dumps(values, sort_keys=True, default=str).encode())
    return digest.hexdigest()


def _chained(own: str | None, producers: dict[str, str | None]) -> str | None:
    """*own* for a module with no (loaded) producer, else a hash over it and theirs."""
    known = {k: v for k, v in producers.items() if v is not None}
    if own is None or not known:
        return own
    digest = hashlib.blake2b(digest_size=32)
    digest.update(own.encode())
    digest.update(json.dumps(known, sort_keys=True).encode())
    return digest.hexdigest()


async def run_fingerprints(
    session: AsyncSession,
    loader: ModuleLoader,
    user_id: str,
    log_id: str,
    module_ids: Iterable[str],
    *,
    owned: set[str] | None = None,
    params: Mapping[str, dict[str, Any]] | None = None,
) -> dict[str, str | None]:
    """Each module's current run fingerprint for the log (``None`` when not loaded).

    Its own :func:`run_fingerprint`, combined with the own fingerprint of every module
    it precomputes from - ``loader.precompute_deps`` (chained producers, required input
    producers, owned ``requirements.modules``), transitive and cycle-safe - so a
    producer's code or config change makes its consumers stale too. The precompute
    config of that whole closure is read in bulk: one query for the user's module
    configs, one for the log's overrides. *params* (``{module_id: run spec params}``)
    apply to that module's own config only. *owned* defaults to the user's installs.
    """
    if owned is None:
        owned = await user_module_ids(session, user_id)
    deps = {mid: [d for d in loader.precompute_deps(mid, owned) if d != mid] for mid in module_ids}
    own: dict[str, str | None] = {}
    configured: list[LoadedModule] = []
    for mid in set(deps).union(*deps.values()):
        loaded = loader.loaded.get(mid)
        if loaded is not None and precompute_keys(loaded.manifest):
            configured.append(loaded)
        else:
            own[mid] = loaded.compute_hash if loaded is not None else None
    if configured:
        ids = [m.id for m in configured]
        user_cfgs = {
            r.module_id: dict(r.config_json or {})
            for r in (
                await session.execute(
                    select(ModuleConfig).where(
                        ModuleConfig.user_id == user_id, ModuleConfig.module_id.in_(ids)
                    )
                )
            ).scalars()
        }
        overrides = {
            r.module_id: dict(r.config_json or {})
            for r in (
                await session.execute(
                    select(LogModuleConfig).where(
                        LogModuleConfig.user_id == user_id,
                        LogModuleConfig.log_id == log_id,
                        LogModuleConfig.module_id.in_(ids),
                    )
                )
            ).scalars()
        }
        for loaded in configured:
            cfg, _ = await effective_config(
                session,
                loaded.manifest,
                user_id,
                log_id,
                user_cfg=user_cfgs.get(loaded.id, {}),
                overrides=overrides.get(loaded.id, {}),
                params=(params or {}).get(loaded.id),
            )
            own[loaded.id] = run_fingerprint(loaded.compute_hash, loaded.manifest, cfg)
    return {mid: _chained(own[mid], {d: own[d] for d in ds}) for mid, ds in deps.items()}


async def effective_config(
    session: AsyncSession,
    manifest: Manifest,
    user_id: str,
    log_id: str | None,
    *,
    owner_id: str | None = None,
    user_cfg: dict[str, Any] | None = None,
    overrides: dict[str, Any] | None = None,
    params: dict[str, Any] | None = None,
) -> tuple[dict[str, Any], dict[str, bool]]:
    """``(config, {card_id: locked})`` - what ``ctx.config`` holds for *log_id*.

    *user_id* is the caller: the module config and admin locks are resolved for
    them. *owner_id* (default *user_id*) is the log's owner, whose per-log overrides
    apply. A shared-dashboard viewer (``owner_id != user_id``) starts from its own
    module config, then takes the owner's value - or its absence, i.e. the default -
    for every :func:`owner_keys` key, so what it computes into the owner's cache is
    what the owner would compute; connection settings, secrets, ``ai`` and undeclared
    keys stay the viewer's. Pass *user_cfg* / *overrides* when they were already read
    in bulk; ``None`` reads them here. *params* are a run variant's spec values, applied over
    the overrides.
    """
    if user_cfg is None:
        user_cfg = await _module_cfg(session, user_id, manifest.id)
    cfg = dict(user_cfg)
    if owner_id and owner_id != user_id:
        owner_cfg = await _module_cfg(session, owner_id, manifest.id)
        for k in owner_keys(manifest):
            if k in owner_cfg:
                cfg[k] = owner_cfg[k]
            else:
                cfg.pop(k, None)
    allowed = dataset_properties(manifest)
    if log_id and allowed:
        if overrides is None:
            o = await session.get(LogModuleConfig, (owner_id or user_id, log_id, manifest.id))
            overrides = dict(o.config_json) if o is not None and o.config_json else {}
        cfg.update({k: v for k, v in overrides.items() if k in allowed})
        cfg.update({k: v for k, v in (params or {}).items() if k in allowed})
    return await resolve_card_overlays(session, manifest, cfg, user_id)
