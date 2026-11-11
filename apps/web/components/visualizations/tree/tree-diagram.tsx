"use client";

import { useMemo } from "react";
import { MarkerType, Position, type Edge, type Node } from "@xyflow/react";
import { hierarchy, tree } from "d3-hierarchy";

import { formatNumber } from "@/lib/format";
import type { TreeData, TreeNode, VizComponentProps } from "@/lib/visualizations/types";
import { VizEmpty } from "@/components/visualizations/viz-shell";
import { CanvasShell } from "@/components/visualizations/canvases/shared/canvas-shell";
import { optBool, optNum, optStr } from "@/components/visualizations/tables/cells";

const LEAF_W = 130;
const LEAF_H = 36;
const OP = 36;

const OPERATORS: Record<string, { sym: string; name: string }> = {
  sequence: { sym: "→", name: "Sequence" },
  "->": { sym: "→", name: "Sequence" },
  xor: { sym: "×", name: "Exclusive choice" },
  X: { sym: "×", name: "Exclusive choice" },
  parallel: { sym: "∧", name: "Parallel" },
  "+": { sym: "∧", name: "Parallel" },
  loop: { sym: "↺", name: "Loop" },
  "*": { sym: "↺", name: "Loop" },
  or: { sym: "∨", name: "Inclusive choice" },
  O: { sym: "∨", name: "Inclusive choice" },
};

/** Operator of a process-tree node: the adapter's `kind` when present, else an
 * operator-named label on an inner node (leaves are activities, never operators). */
function operatorOf(n: TreeNode) {
  return (n.kind && OPERATORS[n.kind]) || (n.children.length > 0 ? OPERATORS[n.label] : undefined);
}

const isTau = (n: TreeNode) => n.children.length === 0 && /^(|·|tau|τ)$/i.test(n.label.trim());

/** Copy of the tree cut at `maxDepth` (0 = all) and, if asked, without τ leaves. */
function prune(n: TreeNode, depth: number, maxDepth: number, foldTau: boolean): TreeNode {
  const children =
    maxDepth > 0 && depth >= maxDepth
      ? []
      : n.children.filter((c) => !(foldTau && isTau(c))).map((c) => prune(c, depth + 1, maxDepth, foldTau));
  return { ...n, children };
}

const MAX_NODES = 500;

/** Copy keeping the first `max` nodes breadth-first: an uncapped prefix tree
 * (one node per distinct prefix) would freeze the page. */
function cap(root: TreeNode, max: number): { root: TreeNode; capped: boolean } {
  const top: TreeNode = { ...root, children: [] };
  const queue: [TreeNode, TreeNode][] = [[root, top]];
  let kept = 1;
  let capped = false;
  for (let i = 0; i < queue.length; i++) {
    const [src, dst] = queue[i];
    for (const c of src.children) {
      if (kept >= max) {
        capped = true;
        break;
      }
      kept++;
      const copy: TreeNode = { ...c, children: [] };
      dst.children.push(copy);
      queue.push([c, copy]);
    }
  }
  return { root: top, capped };
}

function build(root: TreeNode, horizontal: boolean): { nodes: Node[]; edges: Edge[] } {
  const maxValue = hierarchy(root, (n) => n.children)
    .descendants()
    .reduce((m, d) => Math.max(m, d.data.value ?? 0), 1);
  // d3 lays out top-down: x across siblings, y down levels; LR swaps them.
  const laid = tree<TreeNode>()
    .nodeSize(horizontal ? [LEAF_H + 14, LEAF_W + 60] : [LEAF_W + 20, LEAF_H + 50])
    .separation((a, b) => (a.parent === b.parent ? 1 : 1.3))(hierarchy(root, (n) => n.children));

  const nodes: Node[] = [];
  const edges: Edge[] = [];
  laid.each((d) => {
    const n = d.data;
    const op = operatorOf(n);
    const tau = !op && isTau(n);
    const w = op ? OP : tau ? 40 : LEAF_W;
    const h = op ? OP : LEAF_H;
    const t = (n.value ?? 0) / maxValue;
    nodes.push({
      id: n.id,
      position: horizontal ? { x: d.y, y: d.x - h / 2 } : { x: d.x - w / 2, y: d.y },
      sourcePosition: horizontal ? Position.Right : Position.Bottom,
      targetPosition: horizontal ? Position.Left : Position.Top,
      draggable: false,
      selectable: false,
      data: {
        label: op ? (
          <span title={op.name} aria-label={op.name} className="text-sm font-semibold">
            {op.sym}
          </span>
        ) : tau ? (
          "τ"
        ) : (
          <div className="min-w-0 leading-tight">
            <div className="truncate">{n.label}</div>
            {n.value != null && <div className="text-[9px] text-muted-foreground">{formatNumber(n.value)}</div>}
          </div>
        ),
      },
      style: {
        width: w,
        height: h,
        padding: op ? 0 : "0 8px",
        display: "flex",
        alignItems: "center",
        justifyContent: "center",
        fontSize: 11,
        textAlign: "center" as const,
        color: tau ? "var(--card)" : "var(--foreground)",
        borderRadius: op ? "50%" : 8,
        border: "1px solid var(--border)",
        background: tau
          ? "var(--foreground)"
          : op
            ? "var(--muted)"
            : `color-mix(in oklab, var(--ff-seq-550) ${Math.round(6 + t * 50)}%, var(--card))`,
      },
    });
    if (d.parent) {
      edges.push({
        id: `${d.parent.data.id}->${n.id}`,
        source: d.parent.data.id,
        target: n.id,
        selectable: false,
        markerEnd: { type: MarkerType.ArrowClosed, width: 12, height: 12 },
        style: { stroke: "var(--muted-foreground)", strokeWidth: 1.2, opacity: 0.7 },
      });
    }
  });
  return { nodes, edges };
}

/** Node-link diagram for a `tree`-shaped dataset: process trees show operators
 * (→ sequence, × choice, ∧ parallel, ↺ loop, ∨ or) as round nodes and τ as a
 * black box; prefix trees shade each node by its frequency. Sibling order is
 * kept (it matters for sequences). */
export function TreeDiagramViz({ dataset, options }: VizComponentProps) {
  const data = dataset.shape === "tree" ? (dataset.data as TreeData) : null;
  const o = options ?? {};
  const horizontal = optStr(o, "orientation") === "LR";
  const maxDepth = Math.max(0, Math.round(optNum(o, "max_depth", 0)));
  const foldTau = optBool(o, "fold_tau", false);
  const built = useMemo(() => {
    if (!data) return null;
    const { root, capped } = cap(prune(data.root, 0, maxDepth, foldTau), MAX_NODES);
    return { ...build(root, horizontal), capped };
  }, [data, maxDepth, foldTau, horizontal]);

  if (!data || !built || isTau(data.root)) {
    return <VizEmpty message={dataset.meta?.note ?? "No tree."} />;
  }
  return (
    <div className="relative h-full w-full">
      <CanvasShell
        nodes={built.nodes}
        edges={built.edges}
        className="h-full w-full overflow-hidden"
        fitViewKey={`${built.nodes.length}-${horizontal}`}
      />
      {built.capped && (
        <div className="pointer-events-none absolute left-2 top-2 rounded bg-background/80 px-1.5 text-[10px] text-muted-foreground">
          Showing the first {MAX_NODES} nodes; set a max depth to see fewer levels.
        </div>
      )}
    </div>
  );
}
