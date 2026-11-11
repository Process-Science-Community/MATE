"use client";

import { useMemo } from "react";
import { MarkerType, type Edge, type Node } from "@xyflow/react";

import { formatDurationShort, formatNumber } from "@/lib/format";
import type { GraphData, GraphEdge, VizComponentProps } from "@/lib/visualizations/types";
import { VizEmpty } from "@/components/visualizations/viz-shell";
import { sequentialScale } from "@/components/dashboards/kit/palette";
import { optBool, optNum, optStr } from "@/components/visualizations/tables/cells";
import { ElkCanvas, type FlowDirection } from "./elk-canvas";
import { filterGraph } from "./graph-filter";

const NODE_W = 130;
const NODE_H = 40;
const PLACE = 22;
const START = "__start__";
const END = "__end__";

interface MapOptions {
  metric: "frequency" | "performance";
  edgeLabel: string;
  edgeWidth: string;
  nodePct: number;
  edgePct: number;
  showTerminals: boolean;
  hideSelfLoops: boolean;
  direction: FlowDirection;
}

function readOptions(o: Record<string, unknown>): MapOptions {
  return {
    metric: optStr(o, "metric") === "performance" ? "performance" : "frequency",
    edgeLabel: optStr(o, "edge_label", "metric"),
    edgeWidth: optStr(o, "edge_width", "linear"),
    nodePct: optNum(o, "node_pct", 100),
    edgePct: optNum(o, "edge_pct", 100),
    showTerminals: optBool(o, "show_terminals", true),
    hideSelfLoops: optBool(o, "hide_self_loops", false),
    direction: optStr(o, "direction") === "TB" ? "TB" : "LR",
  };
}

const perf = (e: GraphEdge) => e.performanceSeconds;

function edgeText(e: GraphEdge, o: MapOptions): string | undefined {
  const kind = o.edgeLabel === "metric" ? o.metric : o.edgeLabel;
  if (kind === "frequency" && e.value != null) return formatNumber(e.value);
  if (kind === "performance" && perf(e) != null) return formatDurationShort(perf(e)!);
  return undefined;
}

function terminal(id: string, label: string): Node {
  return {
    id,
    position: { x: 0, y: 0 },
    data: { label },
    draggable: false,
    selectable: false,
    style: {
      width: 56,
      height: 24,
      borderRadius: 999,
      border: "1px solid var(--border)",
      background: "var(--muted)",
      fontSize: 10,
      fontWeight: 600,
      display: "flex",
      alignItems: "center",
      justifyContent: "center",
      padding: 0,
    },
  };
}

/** Pure graph → styled React Flow nodes/edges for the process map. */
function build(g: GraphData, o: MapOptions): { nodes: Node[]; edges: Edge[] } {
  const f = filterGraph(g, o.nodePct / 100, o.edgePct / 100, o.hideSelfLoops);
  const maxFreq = Math.max(1, ...f.nodes.map((n) => n.value ?? 0));
  const nodes: Node[] = f.nodes.map((n) => {
    const isPlace = n.kind === "place";
    const t = (n.value ?? 0) / maxFreq;
    return {
      id: n.id,
      position: { x: 0, y: 0 },
      data: {
        label: (
          <div className="min-w-0 leading-tight">
            <div className="truncate">{n.label}</div>
            {!isPlace && n.value != null && (
              <div className="text-[9px] text-muted-foreground">{formatNumber(n.value)}</div>
            )}
          </div>
        ),
      },
      // Read-only map: CanvasShell enables drag/select by default.
      draggable: false,
      selectable: false,
      style: isPlace
        ? { width: PLACE, height: PLACE, borderRadius: "50%", background: "var(--muted)", border: "1px solid var(--border)", padding: 0 }
        : {
            width: NODE_W,
            height: NODE_H,
            borderRadius: 8,
            border: "1px solid var(--border)",
            background: `color-mix(in oklab, var(--ff-seq-550) ${Math.round(6 + t * 50)}%, var(--card))`,
            color: "var(--foreground)",
            fontSize: 11,
            display: "flex",
            alignItems: "center",
            justifyContent: "center",
            padding: "0 8px",
            textAlign: "center" as const,
          },
    };
  });

  const weight = (e: GraphEdge) => (o.metric === "performance" ? (perf(e) ?? 0) : (e.value ?? 0));
  const maxW = Math.max(1e-9, ...f.edges.map(weight));
  const width = (e: GraphEdge) => {
    if (o.edgeWidth === "off") return 1.5;
    const t = o.edgeWidth === "log" ? Math.log1p(weight(e)) / Math.log1p(maxW) : weight(e) / maxW;
    return 1 + 3 * (Number.isFinite(t) ? t : 0);
  };
  const arrow = g.directed === false ? undefined : { type: MarkerType.ArrowClosed, width: 14, height: 14 };
  const edges: Edge[] = f.edges.map((e) => ({
    id: e.id,
    source: e.source,
    target: e.target,
    selectable: false,
    label: edgeText(e, o),
    markerEnd: arrow,
    style: {
      strokeWidth: width(e),
      stroke:
        o.metric === "performance" && perf(e) != null
          ? sequentialScale(weight(e) / maxW, { ordinal: true })
          : "var(--muted-foreground)",
    },
    labelStyle: { fontSize: 9, fill: "var(--muted-foreground)" },
    labelBgStyle: { fill: "var(--card)", fillOpacity: 0.85 },
  }));

  if (o.showTerminals) {
    const visible = new Set(f.nodes.map((n) => n.id));
    const starts = (g.start ?? []).filter((id) => visible.has(id));
    const ends = (g.end ?? []).filter((id) => visible.has(id));
    const dashed = { stroke: "var(--muted-foreground)", strokeDasharray: "4 3", strokeWidth: 1 };
    if (starts.length) {
      nodes.unshift(terminal(START, "Start"));
      for (const id of starts) edges.push({ id: `${START}->${id}`, source: START, target: id, selectable: false, markerEnd: arrow, style: dashed });
    }
    if (ends.length) {
      nodes.push(terminal(END, "End"));
      for (const id of ends) edges.push({ id: `${id}->${END}`, source: id, target: END, selectable: false, markerEnd: arrow, style: dashed });
    }
  }
  return { nodes, edges };
}

/** Read-only node-link process map for a `graph`-shaped dataset (DFG, heuristics
 * net, relation graph), laid out with ELK. Node shade = frequency; edge width,
 * colour and label follow the chosen metric. Rank sliders keep the top N % of
 * nodes/edges (the most frequent connections always stay so nothing floats). */
export function ProcessMapViz({ dataset, options }: VizComponentProps) {
  const graph = dataset.shape === "graph" ? (dataset.data as GraphData) : null;
  const o = readOptions(options ?? {});
  const key = JSON.stringify(o);
  // `dataset` is memoized per fetch by the card, so this rebuilds only when
  // the data or an option value changes.
  // eslint-disable-next-line react-hooks/exhaustive-deps
  const built = useMemo(() => (graph ? build(graph, o) : null), [graph, key]);

  if (!graph || graph.nodes.length === 0 || !built) {
    return <VizEmpty message={dataset.meta?.note ?? "No process model."} />;
  }
  return <ElkCanvas nodes={built.nodes} edges={built.edges} direction={o.direction} />;
}
