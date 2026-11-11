import type { Edge, Node } from "@xyflow/react";

import { artifactTypeId } from "@/lib/visualizations/adapters";
import type {
  FlowConverter,
  FlowGraph,
  FlowGraphEdge,
  FlowGraphNode,
  FlowPaletteModule,
} from "@/lib/flow-queries";

/** Handle id of the log source's output. Modules read the run's log implicitly. */
export const LOG_HANDLE = "log";
/** Handle id of an output node's single input. */
export const OUTPUT_HANDLE = "in";
/** An output node takes any module result. */
export const ANY_TYPE = "*";

export type FlowNode = Node<FlowGraphNode["data"] & Record<string, unknown>, FlowGraphNode["type"]>;
export type FlowEdge = Edge;
export interface EditorGraph {
  nodes: FlowNode[];
  edges: FlowEdge[];
}

/** Ports in artifact-type colours. */
const TYPE_COLOR: Record<string, string> = {
  event_log: "var(--foreground)",
  ocel: "var(--foreground)",
  petri_net: "var(--chart-1)",
  bpmn: "var(--chart-2)",
  process_tree: "var(--chart-3)",
  dfg: "var(--chart-4)",
  conformance_result: "var(--chart-5)",
};
export const typeColor = (type: string | null | undefined) =>
  TYPE_COLOR[artifactTypeId(type) ?? ""] ?? "var(--muted-foreground)";

export const typeLabel = (type: string | null | undefined) =>
  type === ANY_TYPE ? "module result" : (artifactTypeId(type) ?? "?").replace(/_/g, " ");

export interface Lookup {
  modules: Map<string, FlowPaletteModule>;
  /** What the log source carries (`event_log` / `ocel`). */
  logType: string;
  /** artifact ref → its type, for artifact sources. */
  artifactTypes: Map<string, string>;
}

/** What a node's output handle carries, or null when it doesn't exist. */
export function sourceType(node: FlowNode | undefined, handle: string | null | undefined, lu: Lookup) {
  if (!node || !handle) return null;
  if (node.type === "source") {
    const d = node.data as FlowGraphNode["data"] & { kind: string; ref?: string };
    if (d.kind === "log") return handle === LOG_HANDLE ? lu.logType : null;
    return d.ref === handle ? (lu.artifactTypes.get(handle) ?? null) : null;
  }
  if (node.type === "module") {
    const entry = lu.modules.get((node.data as { module_id: string }).module_id);
    return entry?.outputs.find((o) => o.dataset_id === handle)?.artifact_type ?? null;
  }
  return null;
}

/** What a node's input handle accepts, or null when it doesn't exist. */
export function targetType(node: FlowNode | undefined, handle: string | null | undefined, lu: Lookup) {
  if (!node || !handle) return null;
  if (node.type === "output") return handle === OUTPUT_HANDLE ? ANY_TYPE : null;
  if (node.type === "module") {
    const entry = lu.modules.get((node.data as { module_id: string }).module_id);
    return entry?.inputs.find((p) => p.id === handle)?.artifact_type ?? null;
  }
  return null;
}

/** Would an edge source → target close a cycle? */
export function createsCycle(edges: { source: string; target: string }[], source: string, target: string) {
  if (source === target) return true;
  const seen = new Set<string>();
  const stack = [target];
  while (stack.length) {
    const id = stack.pop()!;
    if (id === source) return true;
    if (seen.has(id)) continue;
    seen.add(id);
    for (const e of edges) if (e.source === id) stack.push(e.target);
  }
  return false;
}

export interface Candidate {
  source: string;
  sourceHandle: string;
  target: string;
  targetHandle: string;
}

/** Why a connection is refused, or null when it is fine: the artifact types must
 *  match (the backend's rule), an output saves module results only, no cycles. */
export function connectionProblem(g: EditorGraph, c: Candidate, lu: Lookup): string | null {
  const from = g.nodes.find((n) => n.id === c.source);
  const src = sourceType(from, c.sourceHandle, lu);
  const tgt = targetType(g.nodes.find((n) => n.id === c.target), c.targetHandle, lu);
  if (!src || !tgt) return "Unknown port";
  if (tgt === ANY_TYPE) {
    if (from?.type !== "module") return "An output saves a module's result";
  } else if (artifactTypeId(src) !== artifactTypeId(tgt)) {
    return `A ${typeLabel(src)} can't feed a ${typeLabel(tgt)} input`;
  }
  if (createsCycle(g.edges, c.source, c.target)) return "That would create a cycle";
  return null;
}

/** The converter that turns `src` into `tgt`, if the palette offers one. */
export function findConverter(converters: FlowConverter[], src: string, tgt: string) {
  return converters.find(
    (c) => artifactTypeId(c.from_type) === artifactTypeId(src) && artifactTypeId(c.to_type) === artifactTypeId(tgt),
  );
}

export const newId = (prefix: string) => `${prefix}-${crypto.randomUUID().slice(0, 8)}`;

/** Bind `c`, replacing whatever fed its target port before (one binding per port). */
export function withEdge(edges: FlowEdge[], c: Candidate): FlowEdge[] {
  return [
    ...edges.filter((e) => !(e.target === c.target && e.targetHandle === c.targetHandle)),
    { id: newId("e"), ...c },
  ];
}

/** Why `conv` can no longer go between `c`'s ends (the graph changed since the hint),
 *  or null: both ports still exist with the converter's types, and no cycle. */
export function converterProblem(g: EditorGraph, c: Candidate, conv: FlowConverter, lu: Lookup): string | null {
  const src = sourceType(g.nodes.find((n) => n.id === c.source), c.sourceHandle, lu);
  const tgt = targetType(g.nodes.find((n) => n.id === c.target), c.targetHandle, lu);
  if (!src || !tgt || !findConverter([conv], src, tgt)) return "the ports it would join changed";
  if (createsCycle(g.edges, c.source, c.target)) return "it would create a cycle";
  return null;
}

/** Insert `conv`'s module between the two ends of a refused connection. */
export function insertConverter(g: EditorGraph, c: Candidate, conv: FlowConverter): EditorGraph {
  const a = g.nodes.find((n) => n.id === c.source);
  const b = g.nodes.find((n) => n.id === c.target);
  const id = newId("module");
  const node: FlowNode = {
    id,
    type: "module",
    position: {
      x: ((a?.position.x ?? 0) + (b?.position.x ?? 0)) / 2,
      y: ((a?.position.y ?? 0) + (b?.position.y ?? 0)) / 2 + 80,
    },
    data: { module_id: conv.module_id, params: {} },
  };
  let edges = withEdge(g.edges, { source: c.source, sourceHandle: c.sourceHandle, target: id, targetHandle: conv.input });
  edges = withEdge(edges, { source: id, sourceHandle: conv.output, target: c.target, targetHandle: c.targetHandle });
  return { nodes: [...g.nodes, node], edges };
}

/** The persisted graph: React Flow's runtime fields (selection, measurements) stripped. */
export function toFlowGraph(g: EditorGraph): FlowGraph {
  return {
    nodes: g.nodes.map(
      (n) =>
        ({
          id: n.id,
          type: n.type,
          position: { x: Math.round(n.position.x), y: Math.round(n.position.y) },
          data: n.data,
        }) as FlowGraphNode,
    ),
    edges: g.edges.map(
      (e): FlowGraphEdge => ({
        id: e.id,
        source: e.source,
        sourceHandle: e.sourceHandle ?? "",
        target: e.target,
        targetHandle: e.targetHandle ?? "",
      }),
    ),
  };
}

export function fromFlowGraph(g: FlowGraph): EditorGraph {
  // The log source is the run's log: exactly one, never deleted.
  const nodes = g.nodes.some((n) => n.type === "source" && n.data.kind === "log")
    ? g.nodes
    : [...SEED_GRAPH.nodes, ...g.nodes];
  return {
    nodes: nodes.map((n) => ({
      ...n,
      ...(n.type === "source" && n.data.kind === "log" ? { deletable: false } : {}),
    })) as FlowNode[],
    edges: g.edges.map((e) => ({ ...e })),
  };
}

/** A new flow: just its log source. */
export const SEED_GRAPH: FlowGraph = {
  nodes: [{ id: "source-log", type: "source", position: { x: 0, y: 0 }, data: { kind: "log" } }],
  edges: [],
};
