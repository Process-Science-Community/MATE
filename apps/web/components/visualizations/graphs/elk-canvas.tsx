"use client";

import { useEffect, useState, type ReactNode } from "react";
import { Position, type Edge, type Node } from "@xyflow/react";
import ELK from "elkjs/lib/elk.bundled.js";

import { VizEmpty } from "@/components/visualizations/viz-shell";
import { CanvasShell } from "@/components/visualizations/canvases/shared/canvas-shell";

const elk = new ELK();

export type FlowDirection = "LR" | "TB";

const size = (n: Node, key: "width" | "height", d: number): number =>
  typeof n.style?.[key] === "number" ? (n.style[key] as number) : d;

async function layout(nodes: Node[], edges: Edge[], direction: FlowDirection) {
  const ids = new Set(nodes.map((n) => n.id));
  const kept = edges.filter((e) => ids.has(e.source) && ids.has(e.target));
  const res = await elk.layout({
    id: "root",
    layoutOptions: {
      "elk.algorithm": "layered",
      "elk.direction": direction === "TB" ? "DOWN" : "RIGHT",
      "elk.layered.spacing.nodeNodeBetweenLayers": "40",
      "elk.spacing.nodeNode": "28",
      "elk.edgeRouting": "ORTHOGONAL",
      "elk.layered.nodePlacement.strategy": "BRANDES_KOEPF",
    },
    children: nodes.map((n) => ({ id: n.id, width: size(n, "width", 160), height: size(n, "height", 40) })),
    edges: kept.map((e) => ({ id: e.id, sources: [e.source], targets: [e.target] })),
  });
  const pos = new Map((res.children ?? []).map((c) => [c.id, { x: c.x ?? 0, y: c.y ?? 0 }]));
  const tb = direction === "TB";
  return {
    nodes: nodes.map((n) => ({
      ...n,
      position: pos.get(n.id) ?? { x: 0, y: 0 },
      sourcePosition: tb ? Position.Bottom : Position.Right,
      targetPosition: tb ? Position.Top : Position.Left,
    })),
    edges: kept,
  };
}

/** Read-only node-link canvas: ELK layered layout of pre-styled React Flow
 * nodes (size from `style.width/height`), drawn in the shared CanvasShell.
 * Callers memoize `nodes`/`edges`; the layout reruns only when they change. */
export function ElkCanvas({
  nodes,
  edges,
  direction = "LR",
  settings,
  busy,
}: {
  nodes: Node[];
  edges: Edge[];
  direction?: FlowDirection;
  /** Popover body of the canvas control cluster. */
  settings?: ReactNode;
  /** A refetch is in flight while the current graph stays on screen. */
  busy?: boolean;
}) {
  const [state, setState] = useState<{ nodes: Node[]; edges: Edge[] } | "error" | null>(null);

  useEffect(() => {
    let alive = true;
    layout(nodes, edges, direction)
      .then((r) => alive && setState(r))
      .catch(() => alive && setState("error"));
    return () => {
      alive = false;
    };
  }, [nodes, edges, direction]);

  if (state === "error") return <VizEmpty message="Could not lay out this graph." />;
  if (!state) return <VizEmpty message="Laying out…" />;
  return (
    <CanvasShell
      nodes={state.nodes}
      edges={state.edges}
      // Read-only: hide the connection handle dots.
      className="h-full w-full overflow-hidden [&_.react-flow__handle]:opacity-0"
      // A card-sized canvas has no room for the minimap; it would cover the graph.
      miniMap={false}
      fitViewKey={`${state.nodes.length}-${state.edges.length}-${direction}`}
      settings={settings}
      busy={busy}
    />
  );
}
