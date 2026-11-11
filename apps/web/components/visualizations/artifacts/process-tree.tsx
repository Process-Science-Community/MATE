"use client";

/**
 * `ProcessTreeView` – the platform viewer for `process_tree` artifacts: a
 * d3-hierarchy tidy tree of operator / leaf nodes on `CanvasShell`. Runtime
 * external.
 */

import { useCallback, useEffect, useMemo, useState, type ReactNode } from "react";
import {
  Handle,
  MarkerType,
  Position,
  useEdgesState,
  useNodesState,
  type Edge,
  type Node,
  type NodeMouseHandler,
  type NodeProps,
} from "@xyflow/react";
import { GitBranch, GitMerge, Layers, Repeat, Workflow } from "lucide-react";

import { cn } from "@/lib/cn";
import {
  DEFAULT_GENERAL,
  DEFAULT_PROCESS_TREE,
  type GeneralSettings,
  type NodePositions,
  type ProcessTreeRenderSettings,
} from "@/lib/stores/visualization-settings";
import { CanvasShell } from "@/components/visualizations/canvases/shared/canvas-shell";
import { CanvasLayoutSkeleton } from "@/components/visualizations/canvases/shared/canvas-skeleton";

import { mapEdgeType, treeLayout, truncate } from "./layout";

// -- Nodes ------------------------------------------------------------------

export interface PtLeafNodeData extends Record<string, unknown> {
  label: string;
  /** When true the leaf is the silent τ marker. */
  isInvisible?: boolean;
}

export type PtLeafNode = Node<PtLeafNodeData, "pt_leaf">;

export function PtLeafNode({ data, selected }: NodeProps<PtLeafNode>) {
  if (data.isInvisible) {
    return (
      <div className="relative">
        <Handle type="target" position={Position.Top} className="!h-2 !w-2 !border-0 !bg-muted-foreground" />
        <div
          className={cn(
            "h-6 w-10 rounded-md bg-foreground shadow-sm cursor-pointer transition-all hover:scale-110 hover:shadow-md",
            selected && "ring-2 ring-primary ring-offset-2 ring-offset-background",
          )}
          title="τ"
        />
      </div>
    );
  }
  return (
    <div className="relative">
      <Handle type="target" position={Position.Top} className="!h-2 !w-2 !border-0 !bg-muted-foreground" />
      <div
        className={cn(
          "flex h-7 min-w-[110px] items-center justify-center rounded-md border bg-card px-3 text-xs font-medium",
          "shadow-sm transition-all cursor-pointer hover:-translate-y-0.5 hover:shadow-md hover:border-primary/40",
          selected && "ring-2 ring-primary ring-offset-2 ring-offset-background shadow-md",
        )}
      >
        <span className="truncate">{data.label}</span>
      </div>
    </div>
  );
}

export interface PtOperatorNodeData extends Record<string, unknown> {
  operator: string;
}

export type PtOperatorNode = Node<PtOperatorNodeData, "pt_operator">;

const OP_META: Record<string, { label: string; icon: typeof Workflow; tint: string }> = {
  sequence: { label: "Sequence", icon: Workflow, tint: "bg-muted text-foreground" },
  xor: { label: "XOR", icon: GitBranch, tint: "bg-chart-4/25 text-foreground" },
  parallel: { label: "Parallel", icon: Layers, tint: "bg-chart-2/25 text-foreground" },
  loop: { label: "Loop", icon: Repeat, tint: "bg-chart-5/25 text-foreground" },
  or: { label: "OR", icon: GitMerge, tint: "bg-chart-3/25 text-foreground" },
};

export function PtOperatorNode({ data, selected }: NodeProps<PtOperatorNode>) {
  const meta = OP_META[data.operator] ?? { label: data.operator, icon: Workflow, tint: "bg-muted text-foreground" };
  const Icon = meta.icon;
  return (
    <div className="relative">
      <Handle type="target" position={Position.Top} className="!h-2 !w-2 !border-0 !bg-muted-foreground" />
      <div
        className={cn(
          "flex h-9 items-center gap-2 rounded-full border px-3 shadow-sm transition-all cursor-pointer",
          "hover:-translate-y-0.5 hover:shadow-md hover:border-primary/40",
          selected && "ring-2 ring-primary ring-offset-2 ring-offset-background shadow-md",
          meta.tint,
        )}
      >
        <Icon className="h-3.5 w-3.5" />
        <span className="text-xs font-medium uppercase tracking-wide">{meta.label}</span>
      </div>
      <Handle type="source" position={Position.Bottom} className="!h-2 !w-2 !border-0 !bg-muted-foreground" />
    </div>
  );
}

const nodeTypes = { pt_operator: PtOperatorNode, pt_leaf: PtLeafNode } as const;

// -- Layout -----------------------------------------------------------------

const NODE_W = 130;
const NODE_H = 40;
const LEVEL_GAP = 70;

/** A `process_tree` artifact node (`ProcessTreeNode` in `@mate/module-sdk-ts`).
 *  `id` is optional in the contract; missing ids are derived from the path. */
export interface ProcessTreeViewNode {
  id?: string;
  operator?: string | null;
  label?: string | null;
  children?: ProcessTreeViewNode[];
}

interface IdNode {
  id: string;
  operator: string | null;
  label: string | null;
  children: IdNode[];
}

function withIds(node: ProcessTreeViewNode, path: string): IdNode {
  return {
    id: node.id ?? path,
    operator: node.operator ?? null,
    label: node.label ?? null,
    children: (node.children ?? []).map((c, i) => withIds(c, `${path}.${i}`)),
  };
}

interface FlattenOptions {
  edgeType: ReturnType<typeof mapEdgeType>;
  maxDepth: number | null;
  foldTau: boolean;
  labelMax: number;
  orientation: ProcessTreeRenderSettings["orientation"];
}

function flatten(
  node: IdNode,
  positions: Map<string, { x: number; y: number }>,
  out: { nodes: Node[]; edges: Edge[] },
  o: FlattenOptions,
  depth: number,
  parent?: string,
) {
  if (o.maxDepth !== null && depth > o.maxDepth) return;
  if (o.foldTau && !node.operator && !node.label) return;

  const pos = positions.get(node.id) ?? { x: 0, y: 0 };
  const position =
    o.orientation === "horizontal" ? { x: pos.y, y: pos.x - NODE_W / 2 } : { x: pos.x - NODE_W / 2, y: pos.y };

  if (node.operator) {
    out.nodes.push({ id: node.id, type: "pt_operator", position, data: { operator: node.operator } });
  } else {
    const data: PtLeafNodeData = {
      label: node.label ? truncate(node.label, o.labelMax) : "τ",
      isInvisible: !node.label,
    };
    out.nodes.push({ id: node.id, type: "pt_leaf", position, data });
  }

  if (parent) {
    out.edges.push({
      id: `${parent}__${node.id}`,
      source: parent,
      target: node.id,
      type: o.edgeType,
      style: { stroke: "var(--muted-foreground)", strokeWidth: 1.2, opacity: 0.6 },
      markerEnd: { type: MarkerType.ArrowClosed, color: "var(--muted-foreground)" },
    });
  }

  for (const child of node.children) flatten(child, positions, out, o, depth + 1, node.id);
}

// -- View -------------------------------------------------------------------

export interface ProcessTreeViewProps {
  /** The `process_tree` artifact payload. */
  tree: { root: ProcessTreeViewNode };
  render?: ProcessTreeRenderSettings;
  general?: Pick<GeneralSettings, "edgeRouting" | "nodeLabelMaxLength" | "showMinimap" | "showGrid">;
  /** Dragged positions merged over the tidy-tree layout (by node id). */
  positions?: NodePositions;
  onPositionsChange?: (patch: NodePositions) => void;
  /** Reset was confirmed – the owner drops its persisted positions; the view
   *  restores the computed layout itself. */
  onResetPositions?: () => void;
  /** Popover body of the canvas control cluster. */
  settings?: ReactNode;
  /** A refetch is in flight while the previous tree stays on screen. */
  busy?: boolean;
  /** Outer box; defaults to `CanvasShell`'s framed 640px canvas. */
  className?: string;
}

export function ProcessTreeView({
  tree,
  render = DEFAULT_PROCESS_TREE,
  general = DEFAULT_GENERAL,
  positions,
  onPositionsChange,
  onResetPositions,
  settings,
  busy,
  className,
}: ProcessTreeViewProps) {
  const { laidNodes, laidEdges, key } = useMemo(() => {
    const root = withIds(tree.root, "n");
    const pos = treeLayout(root, { nodeSize: [NODE_W + 24, NODE_H + LEVEL_GAP], nonSiblingSeparation: 1.4 });
    const out = { nodes: [] as Node[], edges: [] as Edge[] };
    flatten(
      root,
      pos,
      out,
      {
        edgeType: mapEdgeType(general.edgeRouting),
        maxDepth: render.maxDepth,
        foldTau: render.foldTauLeaves,
        labelMax: general.nodeLabelMaxLength,
        orientation: render.orientation,
      },
      0,
    );
    return { laidNodes: out.nodes, laidEdges: out.edges, key: out.nodes.length };
  }, [
    tree,
    general.edgeRouting,
    general.nodeLabelMaxLength,
    render.maxDepth,
    render.foldTauLeaves,
    render.orientation,
  ]);

  const [nodes, setNodes, onNodesChange] = useNodesState<Node>([]);
  const [edges, setEdges, onEdgesChange] = useEdgesState<Edge>([]);
  const [seeded, setSeeded] = useState(false);

  useEffect(() => {
    const dragged = positions ?? {};
    setNodes(laidNodes.map((n) => (dragged[n.id] ? { ...n, position: dragged[n.id]! } : n)));
    setEdges(laidEdges);
    setSeeded(true);
    // `positions` is deliberately not a dep: it changes on every drag persist.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [laidNodes, laidEdges]);

  const onNodeDragStop = useCallback<NodeMouseHandler>(
    (_, node) => onPositionsChange?.({ [node.id]: { x: node.position.x, y: node.position.y } }),
    [onPositionsChange],
  );

  if (!seeded) return <CanvasLayoutSkeleton />;
  return (
    <CanvasShell
      nodes={nodes}
      edges={edges}
      nodeTypes={nodeTypes}
      fitViewKey={key}
      miniMap={general.showMinimap}
      showGrid={general.showGrid}
      busy={busy}
      settings={settings}
      className={className}
      // Clearing the owner's store isn't enough: positions aren't a dep of the
      // seeding effect, so reset also re-seeds React Flow from `laidNodes`.
      onReset={() => {
        onResetPositions?.();
        setNodes([...laidNodes]);
      }}
      onNodesChange={onNodesChange}
      onEdgesChange={onEdgesChange}
      onNodeDragStop={onNodeDragStop}
    />
  );
}
