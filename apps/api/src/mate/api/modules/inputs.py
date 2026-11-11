"""``ctx.inputs``: a module's manifest ``inputs:`` bound to other modules' artifacts.

A port resolves ``from`` producer -> its dataset of the port's artifact type -> that
dataset's ``cache_key`` -> the producer's ``ResultCache`` for this log. Only producers
the requesting user owns bind; every other port stays unbound and reads as None.

A run variant (``mate.api.modules.variants``) may bind a port explicitly to any
``ArtifactRef`` instead (:func:`resolve_refs` checks it); a ref's ``@variant`` reads
the producer's run variant of that key, and a ``…/_workspace/{id}`` ref one of the
user's uploaded artifacts (``mate.api.modules.artifacts``).
"""

from __future__ import annotations

import asyncio
import json
from collections.abc import Mapping, Sequence
from pathlib import Path
from typing import Any

from sqlalchemy.ext.asyncio import AsyncSession

from mate.api.config import get_settings
from mate.api.db.models import Artifact, EventLog
from mate.api.modules.cache import ResultCache, is_run_variant
from mate.api.storage import sync as storage_sync
from mate.sdk.artifacts import WORKSPACE_MODULE_ID, ArtifactRef, get_artifact_type, resolve_input
from mate.sdk.manifest import InputPort, Manifest

# ResultCache file suffix -> the `format` a worker reads the file as.
_FORMATS = {".json": "json", ".parquet": "parquet", ".bin": "bytes"}


def workspace_file(user_id: str, artifact_id: str) -> Path:
    """Where an uploaded workspace artifact lives (it may only be in the S3 mirror)."""
    return get_settings().artifacts_dir_for(user_id) / f"{artifact_id}.json"


def workspace_path(user_id: str, artifact_id: str) -> Path | None:
    """The uploaded artifact's local file - fetched alone from the S3 mirror when this
    host has no copy - or None when it is gone. Blocking."""
    path = workspace_file(user_id, artifact_id)
    if not path.exists():
        storage_sync.hydrate_missing_sync(path.parent, lambda rel: rel == path.name)
    return path if path.exists() else None


class ModuleInputs:
    """``InputsProtocol`` over the producers' result caches."""

    def __init__(
        self,
        ports: Sequence[InputPort],
        refs: dict[str, ArtifactRef],
        storage_user_id: str,
        explicit: frozenset[str] = frozenset(),
    ) -> None:
        self._ports = list(ports)
        self._refs = refs
        self._storage_user_id = storage_user_id
        # Ports a run spec bound explicitly: honoured or not run at all, required or not.
        self._explicit = explicit

    @classmethod
    def bind(
        cls,
        ports: Sequence[InputPort],
        owned_producers: Mapping[str, Manifest],
        log_id: str,
        storage_user_id: str,
        explicit: Mapping[str, ArtifactRef] | None = None,
    ) -> ModuleInputs:
        """Bind *ports* to the producers in *owned_producers* (the loaded modules the
        requesting user owns), reading caches from *storage_user_id*'s data. *explicit*
        refs (already checked by :func:`resolve_refs`) win over that default binding."""
        refs: dict[str, ArtifactRef] = {}
        for port in ports:
            producer = owned_producers.get(port.from_ or "")
            if not log_id or producer is None:
                continue
            entry, _problem = resolve_input(port, producer)
            if entry is not None and entry.cache_key is not None:
                # The default binding reads the producer's default result, never a
                # filter view's or a run variant's.
                refs[port.id] = ArtifactRef(log_id, producer.id, entry.cache_key)
        refs.update(explicit or {})
        return cls(ports, refs, storage_user_id, frozenset(explicit or {}))

    @property
    def ports(self) -> list[str]:
        return [p.id for p in self._ports]

    def ref(self, port: str) -> ArtifactRef | None:
        return self._refs.get(port)

    def _cache(self, ref: ArtifactRef) -> ResultCache:
        return ResultCache(
            ref.log_id, ref.module_id, self._storage_user_id, run_variant=ref.variant
        )

    def path(self, port: str) -> Path | None:
        """The local file behind *port*, None while unbound or not produced. Blocking.
        Like a read, it stamps a producer run variant's ``last_used_at`` (the GC's key)."""
        ref = self._refs.get(port)
        if ref is None:
            return None
        if ref.module_id == WORKSPACE_MODULE_ID:
            return workspace_path(self._storage_user_id, ref.key)
        cache = self._cache(ref)
        found = cache.path(ref.key)
        if found is not None:
            cache.touch()
        return found

    async def get(self, port: str) -> Any | None:
        ref = self._refs.get(port)
        if ref is None:
            return None
        if ref.module_id == WORKSPACE_MODULE_ID:
            path = await asyncio.to_thread(self.path, port)
            return None if path is None else json.loads(await asyncio.to_thread(path.read_text))
        return await self._cache(ref).get(ref.key)

    def missing(self) -> list[str]:
        """Required or explicitly bound ports with no produced artifact behind them (the
        log itself aside). Blocking."""
        out: list[str] = []
        for port in self._ports:
            artifact_type = get_artifact_type(port.artifact_type)
            wanted = port.required or port.id in self._explicit
            if not wanted or (artifact_type is not None and artifact_type.storage == "log"):
                continue
            if self.path(port.id) is None:
                out.append(port.id)
        return out

    def snapshot(self) -> dict[str, dict[str, Any]]:
        """``{port: {artifact_type, format, path, ref}}`` for a worker's ctx meta
        (modules/PROTOCOL.md §6). ``format``/``path`` are null until the producer has
        produced the artifact, ``ref`` while the port is unbound. Blocking (S3
        hydration): call it off the event loop."""
        out: dict[str, dict[str, Any]] = {}
        for port in self._ports:
            ref = self._refs.get(port.id)
            path = self.path(port.id)
            out[port.id] = {
                "artifact_type": port.artifact_type,
                "format": _FORMATS.get(path.suffix) if path is not None else None,
                "path": str(path) if path is not None else None,
                "ref": str(ref) if ref is not None else None,
            }
        return out


async def resolve_refs(
    session: AsyncSession,
    *,
    user_id: str,
    log_id: str,
    ports: Sequence[InputPort],
    owned_producers: Mapping[str, Manifest],
    refs: Mapping[str, str],
) -> tuple[dict[str, ArtifactRef], list[str]]:
    """``(bound, problems)`` for a run spec's explicit ``{port: "ArtifactRef"}`` bindings.

    A ref binds only when its port exists, it parses (a variant must be a well-formed
    key - it becomes a path segment), its module is one the user owns, that module
    declares the key as a cached output of the port's artifact type, and its log is
    the run's own or another log *user_id* owns. A ``…/_workspace/{id}`` ref binds when
    it is exactly the ref of an artifact *user_id* uploaded, of the port's type. Every
    other ref is a problem, worded the same whether the target is missing or someone
    else's.
    """
    by_id = {p.id: p for p in ports}
    bound: dict[str, ArtifactRef] = {}
    problems: list[str] = []
    for port_id, text in refs.items():
        port = by_id.get(port_id)
        if port is None:
            problems.append(f"{port_id!r} is not an input port")
            continue
        try:
            ref = ArtifactRef.parse(text)
        except ValueError as exc:
            problems.append(f"input {port_id!r}: {exc}")
            continue
        wanted = get_artifact_type(port.artifact_type)
        if ref.module_id == WORKSPACE_MODULE_ID:
            row = await session.get(Artifact, ref.key)
            if row is None or row.user_id != user_id or row.ref != str(ref):
                problems.append(f"input {port_id!r}: no artifact {text!r}")
            elif wanted is None or get_artifact_type(row.artifact_type) is not wanted:
                problems.append(
                    f"input {port_id!r} takes {port.artifact_type}, {text!r} is {row.artifact_type}"
                )
            else:
                bound[port_id] = ref
            continue
        if ref.variant is not None and not is_run_variant(ref.variant):
            problems.append(f"input {port_id!r}: {ref.variant!r} is not a run variant")
            continue
        producer = owned_producers.get(ref.module_id)
        entry = None
        if producer is not None:
            entry = next((d for d in producer.datasets if d.cache_key == ref.key), None)
        if entry is None:
            problems.append(f"input {port_id!r}: no artifact {text!r}")
            continue
        if wanted is None or get_artifact_type(entry.artifact_type or "") is not wanted:
            problems.append(
                f"input {port_id!r} takes {port.artifact_type}, "
                f"{text!r} is {entry.artifact_type or 'untyped'}"
            )
            continue
        if ref.log_id != log_id:
            other = await session.get(EventLog, ref.log_id)
            if other is None or other.deleted_at is not None or other.user_id != user_id:
                problems.append(f"input {port_id!r}: no artifact {text!r}")
                continue
        bound[port_id] = ref
    return bound, problems
