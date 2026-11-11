"use client";

import { useMemo } from "react";
import { MarkerType, type Edge, type Node } from "@xyflow/react";

import type { GraphData, VizComponentProps } from "@/lib/visualizations/types";
import { VizEmpty } from "@/components/visualizations/viz-shell";
import { optBool, optStr } from "@/components/visualizations/tables/cells";
import { ElkCanvas, type FlowDirection } from "./elk-canvas";

const PLACE = 26;
const TRANS_W = 130;
const TRANS_H = 36;

const base = { display: "flex", alignItems: "center", justifyContent: "center", padding: 0 } as const;

/** Pure graph → styled Petri-net nodes/edges. Places are circles (a token dot
 * or count on marked places, a double ring on final ones), transitions boxes,
 * silent transitions black bars. Initial/final come from the node flags, else
 * `graph.start`/`end`, else the structure (no in-arcs / no out-arcs). */
function build(g: GraphData, showWeights: boolean): { nodes: Node[]; edges: Edge[] } {
  const hasIn = new Set(g.edges.map((e) => e.target));
  const hasOut = new Set(g.edges.map((e) => e.source));
  const start = new Set(g.start ?? []);
  const end = new Set(g.end ?? []);

  const nodes: Node[] = g.nodes.map((n) => {
    const common = { id: n.id, position: { x: 0, y: 0 }, draggable: false, selectable: false };
    if (n.kind === "place") {
      const initial = n.initial ?? (start.has(n.id) || !hasIn.has(n.id));
      const final = n.final ?? (end.has(n.id) || !hasOut.has(n.id));
      const tokens = n.tokens ?? (initial ? 1 : 0);
      return {
        ...common,
        data: {
          label:
            tokens > 1 ? (
              <span aria-label={`${tokens} tokens`} className="text-[10px] font-semibold">{tokens}</span>
            ) : tokens === 1 || initial ? (
              <span aria-label="initial marking" className="block h-2 w-2 rounded-full bg-foreground" />
            ) : null,
        },
        style: {
          ...base,
          width: PLACE,
          height: PLACE,
          borderRadius: "50%",
          background: "var(--card)",
          border: final ? "3px double var(--foreground)" : "1.5px solid var(--foreground)",
        },
      };
    }
    const silent = n.kind === "silent" || !n.label;
    if (silent) {
      return {
        ...common,
        data: { label: null },
        style: { ...base, width: 12, height: TRANS_H, borderRadius: 2, background: "var(--foreground)", border: "none" },
      };
    }
    return {
      ...common,
      data: { label: <span className="line-clamp-2 leading-tight">{n.label}</span> },
      style: {
        ...base,
        width: TRANS_W,
        height: TRANS_H,
        borderRadius: 4,
        background: "var(--card)",
        color: "var(--foreground)",
        border: "1.5px solid var(--foreground)",
        fontSize: 11,
        padding: "0 6px",
        textAlign: "center" as const,
      },
    };
  });

  const edges: Edge[] = g.edges.map((e) => ({
    id: e.id,
    source: e.source,
    target: e.target,
    selectable: false,
    label: showWeights && (e.value ?? 1) > 1 ? String(e.value) : undefined,
    markerEnd: { type: MarkerType.ArrowClosed, width: 12, height: 12 },
    style: { stroke: "var(--muted-foreground)", strokeWidth: 1.2 },
    labelStyle: { fontSize: 9, fill: "var(--muted-foreground)" },
    labelBgStyle: { fill: "var(--card)", fillOpacity: 0.85 },
  }));
  return { nodes, edges };
}

/** Petri net for a `graph`-shaped dataset whose nodes are `place` /
 * `transition` / `silent` (e.g. a `petri_net` route). Arc weights above 1 are
 * shown as labels. */
export function PetriNetViz({ dataset, options }: VizComponentProps) {
  const graph = dataset.shape === "graph" ? (dataset.data as GraphData) : null;
  const showWeights = optBool(options ?? {}, "show_weights", true);
  const direction: FlowDirection = optStr(options ?? {}, "direction") === "TB" ? "TB" : "LR";
  const built = useMemo(() => (graph ? build(graph, showWeights) : null), [graph, showWeights]);

  if (!graph || graph.nodes.length === 0 || !built) {
    return <VizEmpty message={dataset.meta?.note ?? "No Petri net."} />;
  }
  if (!graph.nodes.some((n) => n.kind === "place")) {
    return <VizEmpty message="This graph has no places. Use a Petri-net dataset, or the Process map." />;
  }
  return <ElkCanvas nodes={built.nodes} edges={built.edges} direction={direction} />;
}
