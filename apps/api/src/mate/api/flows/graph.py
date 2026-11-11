"""Flow graphs: the node/edge model, its validation, run order and the node palette.

A flow has three node types and nothing else:

- ``source`` - ``{kind: "log"}`` (the run's log; at most one, every module node reads
  it implicitly) or ``{kind: "artifact", ref}`` (an artifact in the user's workspace).
  Its one output handle is ``"log"`` or the ref itself.
- ``module`` - ``{module_id, params}``: the module's default run, or - with params or
  an input other than its default binding - a run variant of its precompute
  (``modules.variants``); ``params`` holds ``ui.scope: dataset`` values only. Output
  handles are its typed, cached datasets (``dataset_id``); input handles its manifest
  ``inputs:`` port ids.
- ``output`` - ``{name, pin}``: names (and pins) the connected module output in the
  workspace once a run produced it. Input handle ``"in"``.

An edge runs from an output handle to an input handle of the same artifact type.
:func:`graph_errors` covers what the graph alone gets wrong (ids, dangling edges, a
second binding of one port, cycles) and blocks a save; :func:`flow_problems` adds
everything that depends on the user's modules and workspace (types, required ports,
params) and blocks a run - an unfinished graph can still be autosaved.
"""

from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass
from typing import Annotated, Any, Literal

from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from mate.api.db.models import Artifact
from mate.api.modules.installs import user_module_ids
from mate.api.modules.log_config import dataset_properties
from mate.sdk.artifacts import ArtifactRef, get_artifact_type, resolve_input
from mate.sdk.manifest import DatasetEntry, Manifest

LogModel = Literal["case_centric", "object_centric"]
LOG_HANDLE = "log"
OUTPUT_HANDLE = "in"
MAX_NODES = 200
MAX_EDGES = 500


# -- the graph ------------------------------------------------------------------


class Position(BaseModel):
    x: float = 0
    y: float = 0


class LogSourceData(BaseModel):
    kind: Literal["log"]


class ArtifactSourceData(BaseModel):
    kind: Literal["artifact"]
    # An ArtifactRef of an artifact in the user's workspace; empty until picked.
    ref: str = Field(default="", max_length=255)


class ModuleNodeData(BaseModel):
    module_id: str = Field(min_length=1, max_length=64)
    # The module's `ui.scope: dataset` values for this node's run variant (none: the
    # default run).
    params: dict[str, Any] = Field(default_factory=dict)


class OutputNodeData(BaseModel):
    # The workspace name the result gets; empty keeps the dataset's title.
    name: str = Field(default="", max_length=255)
    pin: bool = False


class _Node(BaseModel):
    id: str = Field(min_length=1, max_length=64)
    position: Position = Field(default_factory=Position)


class SourceNode(_Node):
    type: Literal["source"]
    data: Annotated[LogSourceData | ArtifactSourceData, Field(discriminator="kind")]


class ModuleNode(_Node):
    type: Literal["module"]
    data: ModuleNodeData


class OutputNode(_Node):
    type: Literal["output"]
    data: OutputNodeData = Field(default_factory=OutputNodeData)


FlowNode = Annotated[SourceNode | ModuleNode | OutputNode, Field(discriminator="type")]


class FlowEdge(BaseModel):
    model_config = ConfigDict(populate_by_name=True)

    id: str = Field(min_length=1, max_length=64)
    source: str
    # "log" (log source), the ref (artifact source) or a dataset id (module node).
    source_handle: str | None = Field(default=None, alias="sourceHandle")
    target: str
    # An input port id (module node) or "in" (output node).
    target_handle: str | None = Field(default=None, alias="targetHandle")


class FlowGraph(BaseModel):
    nodes: list[FlowNode] = Field(default_factory=list, max_length=MAX_NODES)
    edges: list[FlowEdge] = Field(default_factory=list, max_length=MAX_EDGES)

    def stored(self) -> dict[str, Any]:
        return self.model_dump(mode="json", by_alias=True)


class FlowProblem(BaseModel):
    """One thing wrong with a flow, on the node or edge the editor marks."""

    node_id: str | None = None
    edge_id: str | None = None
    message: str


def _problem(message: str, *, node: str | None = None, edge: str | None = None) -> FlowProblem:
    return FlowProblem(node_id=node, edge_id=edge, message=message)


# -- the graph alone ----------------------------------------------------------------


def _back_edges(graph: FlowGraph) -> list[FlowEdge]:
    """The edges that close a cycle (a DFS's back edges)."""
    out_edges: dict[str, list[FlowEdge]] = defaultdict(list)
    for e in graph.edges:
        out_edges[e.source].append(e)
    color: dict[str, int] = {}  # 1 on the DFS stack, 2 done
    back: list[FlowEdge] = []

    def visit(node_id: str) -> None:  # depth <= MAX_NODES
        color[node_id] = 1
        for e in out_edges.get(node_id, ()):
            state = color.get(e.target, 0)
            if state == 1:
                back.append(e)
            elif state == 0:
                visit(e.target)
        color[node_id] = 2

    for n in graph.nodes:
        if n.id not in color:
            visit(n.id)
    return back


def graph_errors(graph: FlowGraph) -> list[FlowProblem]:
    """What the graph alone gets wrong; none of it may be saved."""
    out: list[FlowProblem] = []
    nodes: dict[str, FlowNode] = {}
    for n in graph.nodes:
        if n.id in nodes:
            out.append(_problem(f"Duplicate node id {n.id!r}.", node=n.id))
        nodes[n.id] = n
    logs = [n.id for n in graph.nodes if isinstance(n, SourceNode) and n.data.kind == "log"]
    out.extend(
        _problem("A flow has one log source: the log it runs on.", node=extra) for extra in logs[1:]
    )
    edge_ids: set[str] = set()
    bound: set[tuple[str, str]] = set()
    for e in graph.edges:
        if e.id in edge_ids:
            out.append(_problem(f"Duplicate edge id {e.id!r}.", edge=e.id))
        edge_ids.add(e.id)
        src, dst = nodes.get(e.source), nodes.get(e.target)
        if src is None or dst is None:
            out.append(_problem("The connection leads to a node that is not there.", edge=e.id))
            continue
        if isinstance(src, OutputNode):
            out.append(_problem("An output node has nothing to pass on.", edge=e.id))
        if isinstance(dst, SourceNode):
            out.append(_problem("A source node takes no input.", edge=e.id))
        if not e.source_handle or not e.target_handle:
            out.append(_problem("The connection names no port.", edge=e.id))
            continue
        if (e.target, e.target_handle) in bound:
            out.append(_problem(f"Input {e.target_handle!r} is connected twice.", edge=e.id))
        bound.add((e.target, e.target_handle))
    if not out:
        out.extend(
            _problem("This connection closes a cycle.", edge=e.id) for e in _back_edges(graph)
        )
    return out


def run_order(graph: FlowGraph) -> list[str]:
    """Node ids, each after every node it reads from (stable: graph order otherwise).
    The graph must be free of :func:`graph_errors`."""
    indegree = {n.id: 0 for n in graph.nodes}
    out_edges: dict[str, list[str]] = defaultdict(list)
    for e in graph.edges:
        out_edges[e.source].append(e.target)
        indegree[e.target] += 1
    position = {n.id: i for i, n in enumerate(graph.nodes)}
    ready = sorted((nid for nid, d in indegree.items() if d == 0), key=position.__getitem__)
    order: list[str] = []
    while ready:
        nid = ready.pop(0)
        order.append(nid)
        for target in out_edges.get(nid, ()):
            indegree[target] -= 1
            if indegree[target] == 0:
                ready.append(target)
                ready.sort(key=position.__getitem__)
    return order


# -- against the user's modules and workspace ----------------------------------------


@dataclass(frozen=True)
class Catalog:
    """The modules a user's flow can use: those they own that are loaded."""

    manifests: dict[str, Manifest]
    # Modules with a precompute to run as a variant.
    runnable: frozenset[str]
    # `module_compute_hash` at load, for pins.
    hashes: dict[str, str]

    def pins(self, graph: FlowGraph) -> dict[str, str]:
        """``{module_id: "version+compute_hash"}`` of the graph's loaded module nodes."""
        return {
            mid: f"{self.manifests[mid].version}+{self.hashes[mid]}"
            for n in graph.nodes
            if isinstance(n, ModuleNode) and (mid := n.data.module_id) in self.manifests
        }


async def load_catalog(session: AsyncSession, user_id: str) -> Catalog:
    from mate.api.modules import get_module_loader

    loader = get_module_loader()
    owned = await user_module_ids(session, user_id)
    loaded = {mid: lm for mid, lm in loader.loaded.items() if mid in owned}
    return Catalog(
        manifests={mid: lm.manifest for mid, lm in loaded.items()},
        runnable=frozenset(m for m in loaded if loader.precompute_declared_topics(m)),
        hashes={mid: lm.compute_hash for mid, lm in loaded.items()},
    )


def log_artifact_type(log_model: str) -> str:
    """The artifact type of the log source: what a flow's log is to a port."""
    return "ocel" if log_model == "object_centric" else "event_log"


def type_id(raw: str | None) -> str | None:
    """The registered base id (``petri_net``) of ``petri_net`` / ``petri_net@1``."""
    found = get_artifact_type(raw or "")
    return found.id if found is not None else None


def outputs(manifest: Manifest) -> dict[str, DatasetEntry]:
    """The module's typed, cached datasets - what a flow can connect - by id."""
    return {
        d.id: d
        for d in manifest.datasets
        if d.cache_key is not None and type_id(d.artifact_type) is not None
    }


def default_bound(manifest: Manifest, port_id: str, catalog: Catalog) -> bool:
    """Whether the port reads its ``from:`` producer's default result unconnected."""
    port = next((p for p in manifest.inputs if p.id == port_id), None)
    producer = catalog.manifests.get(port.from_ or "") if port is not None else None
    if port is None or producer is None:
        return False
    entry, _problem = resolve_input(port, producer)
    return entry is not None


async def workspace_type(session: AsyncSession, user_id: str, ref: str) -> str | None:
    """The artifact type of the user's workspace artifact *ref*, None when they have
    no such artifact."""
    try:
        ArtifactRef.parse(ref)
    except ValueError:
        return None
    row = (
        await session.execute(
            select(Artifact.artifact_type).where(Artifact.user_id == user_id, Artifact.ref == ref)
        )
    ).scalar_one_or_none()
    return type_id(row)


def is_log_type(port_type: str | None) -> bool:
    """Whether an input of this type is the log itself (``event_log`` / ``ocel``)."""
    found = get_artifact_type(port_type or "")
    return found is not None and found.storage == "log"


async def flow_problems(
    session: AsyncSession,
    graph: FlowGraph,
    *,
    user_id: str,
    log_model: str,
    catalog: Catalog,
) -> list[FlowProblem]:
    """Everything that keeps the flow from running, the graph's own errors first."""
    out = graph_errors(graph)
    if out:
        return out
    nodes = {n.id: n for n in graph.nodes}
    # Output type of each source; a module node's come from its manifest.
    source_types: dict[str, str] = {}
    unusable: set[str] = set()  # module nodes that cannot run at all
    for n in graph.nodes:
        if isinstance(n, SourceNode):
            if isinstance(n.data, LogSourceData):
                source_types[n.id] = log_artifact_type(log_model)
            elif not n.data.ref:
                out.append(_problem("Pick an artifact from your workspace.", node=n.id))
            elif (found := await workspace_type(session, user_id, n.data.ref)) is None:
                out.append(_problem(f"No artifact {n.data.ref!r} in your workspace.", node=n.id))
            else:
                source_types[n.id] = found
        elif isinstance(n, ModuleNode) and (cannot := _module_problems(n, log_model, catalog)):
            out.extend(cannot)
            unusable.add(n.id)
    if not any(isinstance(n, ModuleNode) for n in graph.nodes):
        out.append(_problem("Add a module: the flow has nothing to run."))

    connected: set[tuple[str, str]] = set()
    for e in graph.edges:
        src, dst = nodes[e.source], nodes[e.target]
        handle, port_id = str(e.source_handle), str(e.target_handle)
        connected.add((dst.id, port_id))
        carried: str | None = None
        if isinstance(src, SourceNode):
            wanted = LOG_HANDLE if isinstance(src.data, LogSourceData) else src.data.ref
            if handle != wanted:
                out.append(_problem(f"The source has no output {handle!r}.", edge=e.id))
                continue
            carried = source_types.get(src.id)
        elif isinstance(src, ModuleNode) and (m := catalog.manifests.get(src.data.module_id)):
            entry = outputs(m).get(handle)
            if entry is None:
                out.append(_problem(f"{m.name} has no output {handle!r}.", edge=e.id))
                continue
            carried = type_id(entry.artifact_type)
        if isinstance(dst, OutputNode):
            if port_id != OUTPUT_HANDLE:
                out.append(_problem(f"An output node has no input {port_id!r}.", edge=e.id))
            elif not isinstance(src, ModuleNode):
                out.append(_problem("An output saves a module's result.", edge=e.id))
            continue
        if (
            not isinstance(dst, ModuleNode)
            or (m := catalog.manifests.get(dst.data.module_id)) is None
        ):
            continue
        port = next((p for p in m.inputs if p.id == port_id), None)
        if port is None:
            out.append(_problem(f"{m.name} has no input {port_id!r}.", edge=e.id))
            continue
        wanted_type = type_id(port.artifact_type)
        if carried is not None and carried != wanted_type:
            out.append(
                _problem(
                    f"{m.name} input {port.id!r} takes {wanted_type or port.artifact_type}, "
                    f"not {carried}.",
                    edge=e.id,
                )
            )

    for n in graph.nodes:
        if isinstance(n, OutputNode) and (n.id, OUTPUT_HANDLE) not in connected:
            out.append(_problem("Connect a module output to this output.", node=n.id))
        if not isinstance(n, ModuleNode) or n.id in unusable:
            continue
        m = catalog.manifests[n.data.module_id]
        for port in m.inputs:
            if (
                port.required
                and (n.id, port.id) not in connected
                and not is_log_type(port.artifact_type)
                and not default_bound(m, port.id, catalog)
            ):
                out.append(
                    _problem(
                        f"Connect {m.name}'s required input {port.id!r} "
                        f"({type_id(port.artifact_type) or port.artifact_type}).",
                        node=n.id,
                    )
                )
    return out


def _module_problems(node: ModuleNode, log_model: str, catalog: Catalog) -> list[FlowProblem]:
    mid = node.data.module_id
    manifest = catalog.manifests.get(mid)
    if manifest is None:
        return [_problem(f"Module {mid!r} is not installed.", node=node.id)]
    name = manifest.name or mid
    if manifest.requirements.event_log.log_model != log_model:
        return [_problem(f"{name} does not work on this flow's kind of log.", node=node.id)]
    if mid not in catalog.runnable:
        return [_problem(f"{name} has no computation a flow can run.", node=node.id)]
    unknown = sorted(set(node.data.params) - set(dataset_properties(manifest)))
    if unknown:
        return [_problem(f"Not a per-log parameter of {name}: {', '.join(unknown)}.", node=node.id)]
    return []


# -- the palette ----------------------------------------------------------------------


def palette(catalog: Catalog, log_model: str) -> dict[str, Any]:
    """The modules a flow on *log_model* logs can use as nodes, and the converters
    the editor offers for a type mismatch."""
    modules: list[dict[str, Any]] = []
    for mid in sorted(catalog.manifests):
        m = catalog.manifests[mid]
        if mid not in catalog.runnable or m.requirements.event_log.log_model != log_model:
            continue
        modules.append(
            {
                "module_id": mid,
                "name": m.name or mid,
                "description": m.description or "",
                "category": m.category,
                "inputs": [
                    {
                        "id": p.id,
                        "artifact_type": type_id(p.artifact_type) or p.artifact_type,
                        "required": p.required,
                        "from": p.from_,
                        "default_bound": default_bound(m, p.id, catalog),
                        "description": p.description,
                    }
                    for p in m.inputs
                ],
                "outputs": [
                    {
                        "dataset_id": d.id,
                        "artifact_type": type_id(d.artifact_type),
                        "title": d.title or d.id,
                    }
                    for d in outputs(m).values()
                ],
                "params": {"type": "object", "properties": dataset_properties(m)},
            }
        )
    return {
        "log_artifact_type": log_artifact_type(log_model),
        "modules": modules,
        "converters": converters(modules),
    }


def converters(modules: list[dict[str, Any]]) -> list[dict[str, str]]:
    """``{from_type, to_type, module_id, input, output}`` for each module output named
    ``<anything>_from_<port id>`` (``petri_net_from_bpmn``) that turns that port's type
    into another: the convention a converter module follows (``modules/interop``)."""
    out: list[dict[str, str]] = []
    for m in modules:
        for port in m["inputs"]:
            for d in m["outputs"]:
                if (
                    d["dataset_id"].endswith(f"_from_{port['id']}")
                    and d["artifact_type"] != port["artifact_type"]
                ):
                    out.append(
                        {
                            "from_type": port["artifact_type"],
                            "to_type": d["artifact_type"],
                            "module_id": m["module_id"],
                            "input": port["id"],
                            "output": d["dataset_id"],
                        }
                    )
    return out
