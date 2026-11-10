"""Artifact types: the contract for what modules produce and consume.

An *artifact type* (``petri_net@1``) fixes how a payload is stored, which generic
visualization shape it renders as by default and - for JSON payloads - a lenient
pydantic model of its keys. An *artifact* is one concrete instance a module produced,
addressed by an :class:`ArtifactRef`.

A module declares what it produces on its manifest ``datasets[]`` (``artifact_type`` +
``cache_key``) and what it consumes under ``inputs:``. The platform binds each input
port to the producer's cached output and hands it to the handler::

    model = await ctx.inputs.get("reference_model")   # dict, DataFrame, bytes or None
"""

from __future__ import annotations

import sys
from collections.abc import Iterable
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any, Literal

from pydantic import BaseModel, ConfigDict, ValidationError

if TYPE_CHECKING:
    from mate.sdk.manifest import DatasetEntry, InputPort, Manifest

# "log" = the platform's own event log / OCEL tables, never a result-cache file.
ArtifactStorage = Literal["json", "parquet", "bytes", "log"]
# The reserved module id of a user's uploaded artifacts: `{log_id or "_"}/_workspace/{id}`.
WORKSPACE_MODULE_ID = "_workspace"
# The `DatasetEnvelope` shapes the generic visualizations accept.
DatasetShape = Literal["table", "graph", "kpi", "tree", "blob"]


class _Payload(BaseModel):
    # Lenient on purpose: producers add fields (and their own `kind`/`version` tags)
    # freely; only the keys every producer emits today are required.
    model_config = ConfigDict(extra="allow")


class DfgActivity(_Payload):
    id: str


class DfgEdge(_Payload):
    source: str
    target: str
    frequency: int


class DfgPayload(_Payload):
    """``dfg``: a directly-follows graph (discovery's ``serialize_dfg``)."""

    activities: list[DfgActivity]
    edges: list[DfgEdge]


class PetriNetPlace(_Payload):
    id: str


class PetriNetTransition(_Payload):
    id: str
    # "" for an invisible (tau) transition.
    label: str


class PetriNetArc(_Payload):
    source: str
    target: str


class PetriNetPayload(_Payload):
    """``petri_net``: places, transitions and arcs (discovery's ``serialize_petri_net``)."""

    places: list[PetriNetPlace]
    transitions: list[PetriNetTransition]
    arcs: list[PetriNetArc]


class ProcessTreeNode(_Payload):
    # An operator node (`sequence`, `xor`, ...) or a leaf with a label (None = tau).
    operator: str | None = None
    label: str | None = None
    children: list[ProcessTreeNode] = []


class ProcessTreePayload(_Payload):
    """``process_tree``: a recursive node tree (discovery's ``serialize_process_tree``)."""

    root: ProcessTreeNode


class BpmnPayload(_Payload):
    """``bpmn``: a BPMN 2.0 document as XML text, without diagram interchange."""

    xml: str


class ConformanceKpis(_Payload):
    # None when pm4py returned a non-finite fitness.
    log_fitness: float | None


class ConformanceResultPayload(_Payload):
    """``conformance_result``: one conformance run (conformance's ``serialize_conformance``)."""

    technique: str
    # Absent until the first run (the route answers `{ran: false, ...}`).
    kpis: ConformanceKpis | None = None


class DriftPoint(_Payload):
    type: str
    start_timestamp: str
    end_timestamp: str
    confidence: float


class DriftPointsPayload(_Payload):
    """``drift_points``: detected concept drifts (cv4cdd's ``detections``)."""

    drifts: list[DriftPoint]


@dataclass(frozen=True)
class ArtifactType:
    """One registered artifact type. ``str()`` gives the versioned id, ``petri_net@1``."""

    id: str
    version: int
    storage: ArtifactStorage
    # Default visualization shape; a manifest dataset of this type inherits it.
    shape: DatasetShape
    # Payload model for `json` storage, None otherwise.
    model: type[BaseModel] | None = None
    description: str = ""

    def __str__(self) -> str:
        return f"{self.id}@{self.version}"


ARTIFACT_TYPES: dict[str, ArtifactType] = {
    t.id: t
    for t in (
        ArtifactType("event_log", 1, "log", "table", None, "The case-centric event log itself."),
        ArtifactType("ocel", 1, "log", "graph", None, "The object-centric event log itself."),
        ArtifactType("table", 1, "parquet", "table", None, "A DataFrame stored as Parquet."),
        ArtifactType("dfg", 1, "json", "graph", DfgPayload, "Directly-follows graph."),
        ArtifactType("petri_net", 1, "json", "graph", PetriNetPayload, "Petri net."),
        ArtifactType("process_tree", 1, "json", "tree", ProcessTreePayload, "Process tree."),
        # `blob`: the generic viz layer renders BPMN through its `bpmn-xml` blob media;
        # there is no BPMN-to-graph adapter.
        ArtifactType("bpmn", 1, "json", "blob", BpmnPayload, "BPMN 2.0 model as XML."),
        ArtifactType(
            "conformance_result",
            1,
            "json",
            "table",
            ConformanceResultPayload,
            "Fitness, precision and deviations of a log against a model.",
        ),
        ArtifactType(
            "drift_points", 1, "json", "table", DriftPointsPayload, "Detected concept drifts."
        ),
    )
}


def get_artifact_type(type_id: str) -> ArtifactType | None:
    """The type for ``petri_net`` or ``petri_net@1``; None when unknown or the version
    differs from the registered one."""
    base, sep, version = type_id.partition("@")
    found = ARTIFACT_TYPES.get(base)
    if found is None or (sep and version != str(found.version)):
        return None
    return found


def validate_artifact(type_id: str, value: Any) -> list[str]:
    """Problems with *value* as an artifact of *type_id*; empty when it conforms."""
    artifact_type = get_artifact_type(type_id)
    if artifact_type is None:
        return [f"unknown artifact type {type_id!r}"]
    if artifact_type.model is not None:
        try:
            artifact_type.model.model_validate(value)
        except ValidationError as exc:
            return [
                f"{'.'.join(str(p) for p in err['loc']) or '<root>'}: {err['msg']}"
                for err in exc.errors()
            ]
        return []
    got = type(value).__name__
    if artifact_type.storage == "parquet":
        pd = sys.modules.get("pandas")  # a DataFrame means pandas is already imported
        if pd is None or not isinstance(value, pd.DataFrame):
            return [f"{artifact_type} is stored as Parquet and needs a DataFrame, got {got}"]
    if artifact_type.storage == "bytes" and not isinstance(value, bytes | bytearray):
        return [f"{artifact_type} needs bytes, got {got}"]
    return []


@dataclass(frozen=True)
class ArtifactRef:
    """Address of one artifact: ``{log_id}/{module_id}[@{variant}]/{key}``.

    The producer's result-cache entry *key* for a log - in its default result, or with
    ``@{variant}`` in one of its *run variants* (a separately requested run with its own
    params, inputs and filter; the variant key is 16 hex chars, its cache ``_r_<key>/``).
    Never a dashboard's filter view. Always resolved for the requesting user's own data.
    """

    log_id: str
    module_id: str
    key: str
    variant: str | None = None

    def __str__(self) -> str:
        module = f"{self.module_id}@{self.variant}" if self.variant else self.module_id
        return f"{self.log_id}/{module}/{self.key}"

    @classmethod
    def parse(cls, text: str) -> ArtifactRef:
        parts = text.split("/")
        module_id, sep, variant = parts[1].partition("@") if len(parts) == 3 else ("", "", "")
        if not (parts[0] and module_id and parts[-1]) or (sep and not variant):
            raise ValueError(f"{text!r} is not log_id/module_id[@variant]/key.")
        return cls(parts[0], module_id, parts[2], variant or None)


def resolve_input(port: InputPort, producer: Manifest) -> tuple[DatasetEntry | None, str | None]:
    """The *producer* dataset feeding *port* (its named ``dataset``, else its only dataset
    of the port's artifact type), or ``(None, problem)`` when there is no usable one."""
    wanted = get_artifact_type(port.artifact_type)
    if wanted is None:
        return None, f"input {port.id!r} has unknown artifact type {port.artifact_type!r}"
    matches = [
        d
        for d in producer.datasets
        if d.id == port.dataset
        or (port.dataset is None and get_artifact_type(d.artifact_type or "") is wanted)
    ]
    if not matches:
        what = f"dataset {port.dataset!r}" if port.dataset else f"{wanted.id} dataset"
        return None, f"input {port.id!r}: {producer.id} has no {what}"
    if len(matches) > 1:
        return None, (
            f"input {port.id!r}: {producer.id} has several {wanted.id} datasets - "
            "name one with `dataset:`"
        )
    entry = matches[0]
    if get_artifact_type(entry.artifact_type or "") is not wanted:
        return None, (
            f"input {port.id!r}: {producer.id}.{entry.id} is "
            f"{entry.artifact_type or 'untyped'}, not {wanted.id}"
        )
    if entry.cache_key is None:
        return None, (
            f"input {port.id!r}: {producer.id}.{entry.id} has no cache_key - "
            "inputs read precomputed cache entries only"
        )
    return entry, None


def check_contract(manifests: Iterable[Manifest]) -> list[str]:
    """Artifact-contract problems across *manifests*, each prefixed with its module id:
    unknown artifact types, typed datasets sharing a cache_key (one artifact ref), inputs
    whose producer or producer dataset is missing or unusable, and required inputs that
    name no producer."""
    by_id = {m.id: m for m in manifests}
    problems: list[str] = []
    for m in by_id.values():
        typed_keys: dict[str, str] = {}
        for d in m.datasets:
            if d.artifact_type is not None and get_artifact_type(d.artifact_type) is None:
                problems.append(
                    f"{m.id}: dataset {d.id!r} has unknown artifact type {d.artifact_type!r}"
                )
            if (
                d.artifact_type is not None
                and d.cache_key is not None
                and (first := typed_keys.setdefault(d.cache_key, d.id)) != d.id
            ):
                problems.append(
                    f"{m.id}: datasets {first!r} and {d.id!r} share cache_key "
                    f"{d.cache_key!r} - one artifact, only {first!r} is indexed"
                )
        for port in m.inputs:
            artifact_type = get_artifact_type(port.artifact_type)
            if artifact_type is None:
                problems.append(
                    f"{m.id}: input {port.id!r} has unknown artifact type {port.artifact_type!r}"
                )
            elif port.from_ is None:
                # A `log`-stored port is the platform's own log, never another module's.
                if port.required and artifact_type.storage != "log":
                    problems.append(f"{m.id}: required input {port.id!r} names no `from:` module")
            elif port.from_ not in by_id:
                problems.append(
                    f"{m.id}: input {port.id!r} reads from unknown module {port.from_!r}"
                )
            else:
                _entry, problem = resolve_input(port, by_id[port.from_])
                if problem is not None:
                    problems.append(f"{m.id}: {problem}")
    problems.extend(_required_input_cycles(by_id))
    return problems


def _required_input_cycles(by_id: dict[str, Manifest]) -> list[str]:
    """Modules that reach themselves through required `inputs.from` edges - each
    would wait for the other to compute first, forever."""
    edges = {
        m.id: {f for p in m.inputs if p.required and (f := p.from_) is not None and f in by_id}
        for m in by_id.values()
    }
    problems: list[str] = []
    for start in sorted(edges):
        stack, seen = list(edges[start]), set[str]()
        while stack:
            mid = stack.pop()
            if mid == start:
                problems.append(f"{start}: required inputs form a cycle back to {start!r}")
                break
            if mid not in seen:
                seen.add(mid)
                stack.extend(edges.get(mid, ()))
    return problems
