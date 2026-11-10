"use client";

/**
 * `PetriNetView` – the platform viewer for `petri_net` artifacts – plus the
 * place / transition node components it renders. Discovery's Petri tab and
 * ocel_discovery's per-object-type OCPN both render through it; module-specific
 * bits (model choice, persisted drags, empty states) stay with the caller.
 * Runtime external.
 */

import { useCallback, useEffect, useRef, useState, type ReactNode } from "react";
import {
  Handle,
  useEdgesState,
  useNodeId,
  useNodesState,
  useUpdateNodeInternals,
  type Edge,
  type Node,
  type NodeMouseHandler,
  type NodeProps,
} from "@xyflow/react";

import { cn } from "@/lib/cn";
import {
  DEFAULT_GENERAL,
  DEFAULT_PETRI,
  type GeneralSettings,
  type LayoutDirection,
  type NodePositions,
  type PetriRenderSettings,
} from "@/lib/stores/visualization-settings";
import { CanvasShell } from "@/components/visualizations/canvases/shared/canvas-shell";
import { CanvasLayoutSkeleton } from "@/components/visualizations/canvases/shared/canvas-skeleton";
import { VizEmpty } from "@/components/visualizations/viz-shell";

import { ElkEdge } from "./edges";
import { elkLayout, handlePositions, mapDirection, runAfterPaint, truncate } from "./layout";

// -- Nodes ------------------------------------------------------------------

/**
 * xyflow only re-measures `handleBounds` when the node's measured size changes
 * – moving a `<Handle>` to another side leaves the old bounds cached, so the
 * drag-fallback bezier would keep leaving from the pre-flip side. Re-measure on
 * a flip only: the ResizeObserver measures a new node, and a per-node update at
 * mount resolves a queued `fitView` before the other nodes are measured (the
 * fit then frames a fraction of the net).
 */
function useRemeasureOnFlip(direction: LayoutDirection) {
  const id = useNodeId();
  const update = useUpdateNodeInternals();
  const last = useRef(direction);
  useEffect(() => {
    if (last.current === direction) return;
    last.current = direction;
    if (id) update(id);
  }, [id, direction, update]);
}

export interface PlaceNodeData extends Record<string, unknown> {
  label: string;
  /** Untruncated label, for the tooltip. */
  title: string;
  isInitial: boolean;
  isFinal: boolean;
  tokens: number;
  direction: LayoutDirection;
}

export type PlaceNode = Node<PlaceNodeData, "place">;

export function PlaceNode({ data, selected }: NodeProps<PlaceNode>) {
  const { source, target } = handlePositions(data.direction);
  useRemeasureOnFlip(data.direction);

  return (
    // `h-full w-full`: the view pins this wrapper to exactly the size ELK was
    // told (`pinNodeSize`), and the box must not grow past it – a node wider
    // than its reserved slot sits on the channel ELK routed edges through.
    <div className="relative h-full w-full">
      <Handle type="target" position={target} className="!h-2 !w-2 !border-0 !bg-muted-foreground" />
      <div
        className={cn(
          "flex h-full w-full items-center justify-center rounded-full border bg-card shadow-sm transition-all cursor-pointer",
          "hover:scale-110 hover:shadow-md hover:border-primary/50",
          selected && "ring-2 ring-primary ring-offset-2 ring-offset-background",
          !selected && (data.isInitial || data.isFinal) && "ring-2 ring-foreground ring-offset-2 ring-offset-background",
        )}
        title={data.title || (data.isInitial ? "Source place" : data.isFinal ? "Sink place" : undefined)}
      >
        {data.tokens > 0 && <span className="h-2 w-2 rounded-full bg-foreground" />}
      </div>
      <Handle type="source" position={source} className="!h-2 !w-2 !border-0 !bg-muted-foreground" />
    </div>
  );
}

export interface TransitionNodeData extends Record<string, unknown> {
  label: string;
  /** Untruncated label, for the tooltip. */
  title: string;
  isInvisible: boolean;
  direction: LayoutDirection;
}

export type TransitionNode = Node<TransitionNodeData, "transition">;

export function TransitionNode({ data, selected }: NodeProps<TransitionNode>) {
  const { source, target } = handlePositions(data.direction);
  useRemeasureOnFlip(data.direction);

  if (data.isInvisible) {
    return (
      <div className="relative h-full w-full">
        <Handle type="target" position={target} className="!h-2 !w-2 !border-0 !bg-muted-foreground" />
        <div
          className={cn(
            "h-full w-full rounded-md bg-foreground shadow-sm cursor-pointer transition-all hover:scale-110 hover:shadow-md",
            selected && "ring-2 ring-primary ring-offset-2 ring-offset-background",
          )}
          title="τ (silent)"
        />
        <Handle type="source" position={source} className="!h-2 !w-2 !border-0 !bg-muted-foreground" />
      </div>
    );
  }
  return (
    // `h-full w-full` rather than a min width: the view measures the label and
    // pins this wrapper to the width ELK reserved, so the box can't grow past
    // its slot and the `truncate` span below actually clips.
    <div className="relative h-full w-full">
      <Handle type="target" position={target} className="!h-2 !w-2 !border-0 !bg-muted-foreground" />
      <div
        className={cn(
          "flex h-full w-full items-center justify-center rounded-md border bg-card px-3 text-sm font-medium",
          "shadow-sm transition-all cursor-pointer hover:-translate-y-0.5 hover:shadow-md hover:border-primary/40",
          selected && "ring-2 ring-primary ring-offset-2 ring-offset-background shadow-md",
        )}
        title={data.title}
      >
        <span className="truncate">{data.label}</span>
      </div>
      <Handle type="source" position={source} className="!h-2 !w-2 !border-0 !bg-muted-foreground" />
    </div>
  );
}

const nodeTypes = { place: PlaceNode, transition: TransitionNode } as const;
const edgeTypes = { elk: ElkEdge } as const;

// -- Label sizing -----------------------------------------------------------

const PLACE_SIZE = { width: 36, height: 36 } as const;
/** The τ box. */
const TAU_SIZE = { width: 48, height: 28 } as const;
const TRANSITION_HEIGHT = 36;
const TRANSITION_MIN_WIDTH = 120;
const TRANSITION_MAX_WIDTH = 240;
/** `px-3` on both sides plus a couple of px so the label never touches it. */
const TRANSITION_PADDING = 26;
const FALLBACK_PX_PER_CHAR = 7.3;

let measureCtx: CanvasRenderingContext2D | null | undefined;
const widthCache = new Map<string, number>();

/**
 * Rendered width of a transition label, in px. A box that renders wider than
 * the slot ELK reserved sits on the channel ELK routed edges through, so the
 * label is measured before layout. Canvas `measureText` is synchronous and
 * doesn't force a DOM layout.
 */
function measureLabelWidth(text: string): number {
  const hit = widthCache.get(text);
  if (hit !== undefined) return hit;
  if (measureCtx === undefined) {
    measureCtx = typeof document === "undefined" ? null : document.createElement("canvas").getContext("2d");
    if (measureCtx) {
      // Matches `TransitionNode`'s box: Tailwind `text-sm font-medium`.
      const family = getComputedStyle(document.body).fontFamily || "sans-serif";
      measureCtx.font = `500 14px ${family}`;
    }
  }
  const width = measureCtx ? measureCtx.measureText(text).width : text.length * FALLBACK_PX_PER_CHAR;
  widthCache.set(text, width);
  return width;
}

/** Corner fillet for the routed arcs, from the edge-routing preference. */
function cornerRadiusFor(routing: string): number {
  if (routing === "straight") return 0;
  if (routing === "spline") return 24;
  return 10;
}

// -- View -------------------------------------------------------------------

/**
 * A `petri_net` artifact payload (`PetriNetPayload` in `@mate/module-sdk-ts`),
 * with the optional fields discovery's serializer adds.
 */
export interface PetriNetViewNet {
  places: { id: string; label?: string | null; is_initial?: boolean; is_final?: boolean; tokens?: number }[];
  transitions: { id: string; label?: string | null; name?: string | null; is_invisible?: boolean }[];
  arcs: { id?: string; source: string; target: string; weight?: number }[];
}

export interface PetriNetViewProps {
  net: PetriNetViewNet;
  /** Render settings (direction, labels, markings, arc weights). */
  petri?: PetriRenderSettings;
  general?: Pick<GeneralSettings, "edgeRouting" | "nodeLabelMaxLength" | "showMinimap" | "showGrid">;
  /** Dragged positions merged over the auto layout (by node id). */
  positions?: NodePositions;
  /** A node was dragged – persist it if the owner keeps positions. */
  onPositionsChange?: (patch: NodePositions) => void;
  /** Reset was confirmed – the owner drops its persisted positions; the view
   *  restores the auto layout itself. */
  onResetPositions?: () => void;
  /** Arc stroke width, e.g. thicker variable arcs in an OCPN. Default 1.5. */
  arcWidth?: (arc: PetriNetViewNet["arcs"][number]) => number;
  /** Re-fit trigger, applied once the net it came with is laid out; defaults
   *  to the net's size. */
  fitViewKey?: string | number;
  /** Popover body of the canvas control cluster. */
  settings?: ReactNode;
  /** A refetch is in flight while the previous net stays on screen. */
  busy?: boolean;
  overlay?: ReactNode;
  /** Outer box; defaults to `CanvasShell`'s framed 640px canvas. */
  className?: string;
}

export function PetriNetView({
  net,
  petri = DEFAULT_PETRI,
  general = DEFAULT_GENERAL,
  positions,
  onPositionsChange,
  onResetPositions,
  arcWidth,
  fitViewKey,
  settings,
  busy,
  overlay,
  className,
}: PetriNetViewProps) {
  const direction = petri.layoutDirection;
  const [nodes, setNodes, onNodesChange] = useNodesState<Node>([]);
  const [edges, setEdges, onEdgesChange] = useEdgesState<Edge>([]);
  const [laid, setLaid] = useState(false);
  // A re-layout keeps the current net on screen with the busy chip.
  const [laying, setLaying] = useState(false);
  // Committed with the layout result: a key that moved with the input would
  // fit the previous net's bounds while ELK is still solving the new one.
  const [fitKey, setFitKey] = useState<string | number>();
  // First layout is deferred past first paint (skeleton) so elkjs' synchronous
  // main-thread solver doesn't block it; re-layouts run inline.
  const didFirstLayout = useRef(false);
  // Last ELK result BEFORE dragged positions are merged in – what reset puts
  // back. `positions` is deliberately not an effect dep (it changes on every
  // drag persist and would re-run the solver), so reset has to re-seed too.
  const autoNodesRef = useRef<Node[]>([]);
  const positionsRef = useRef(positions);
  positionsRef.current = positions;
  const arcWidthRef = useRef(arcWidth);
  arcWidthRef.current = arcWidth;

  useEffect(() => {
    let cancelled = false;
    let cancelScheduled: (() => void) | undefined;
    const layoutFitKey = fitViewKey ?? `pn-${net.places.length}-${net.transitions.length}`;

    const visibleTransitions = petri.showInvisibleTransitions
      ? net.transitions
      : net.transitions.filter((t) => !t.is_invisible);
    const visibleTransitionIds = new Set(visibleTransitions.map((t) => t.id));

    const placeNodes: Node[] = net.places.map((p) => ({
      id: p.id,
      type: "place",
      position: { x: 0, y: 0 },
      ariaLabel: p.label || undefined,
      data: {
        label: truncate(p.label ?? "", general.nodeLabelMaxLength),
        title: p.label ?? "",
        isInitial: petri.highlightMarkings ? Boolean(p.is_initial) : false,
        isFinal: petri.highlightMarkings ? Boolean(p.is_final) : false,
        tokens: petri.placeMode === "count" ? (p.tokens ?? 0) : 0,
        direction,
      } satisfies PlaceNodeData,
    }));

    const transitionNodes: Node[] = visibleTransitions.map((t) => {
      const id = t.name || t.id;
      let label: string;
      if (petri.transitionLabelMode === "id") label = id;
      else if (petri.transitionLabelMode === "both") label = t.label ? `${t.label} · ${id}` : id;
      else label = t.label || "τ";
      return {
        id: t.id,
        type: "transition",
        position: { x: 0, y: 0 },
        ariaLabel: label,
        data: {
          label: truncate(label, general.nodeLabelMaxLength),
          title: label,
          isInvisible: Boolean(t.is_invisible),
          direction,
        } satisfies TransitionNodeData,
      };
    });

    const visibleNodeIds = new Set<string>([...net.places.map((p) => p.id), ...visibleTransitionIds]);

    const cornerRadius = cornerRadiusFor(general.edgeRouting);
    const initialEdges: Edge[] = net.arcs
      .filter((arc) => visibleNodeIds.has(arc.source) && visibleNodeIds.has(arc.target))
      .map((arc, i) => ({
        id: arc.id ?? `a${i}`,
        source: arc.source,
        target: arc.target,
        type: "elk",
        data: { cornerRadius },
        label: petri.showArcWeights && (arc.weight ?? 1) > 1 ? String(arc.weight) : undefined,
        labelStyle: { fill: "var(--muted-foreground)", fontSize: 10 },
        labelBgPadding: [3, 2],
        labelBgBorderRadius: 4,
        labelBgStyle: { fill: "var(--card)", stroke: "var(--border)" },
        // No `markerEnd`: `ElkEdge` draws the arrowhead itself.
        style: { stroke: "var(--muted-foreground)", strokeWidth: arcWidthRef.current?.(arc) ?? 1.5 },
      }));

    const runLayout = () => {
      didFirstLayout.current = true;
      setLaying(true);
      elkLayout([...placeNodes, ...transitionNodes], initialEdges, {
        direction: mapDirection(direction),
        // Always ORTHOGONAL: `ElkEdge` reads the sections as a polyline, and
        // only this mode emits true right-angle corners. The routing
        // preference becomes the corner radius instead.
        edgeRouting: "ORTHOGONAL",
        mergeEdges: petri.mergeArcs,
        pinNodeSize: true,
        // `nodeNode` is the in-layer CROSS-axis gap: 28px between 36px-tall
        // nodes reads fine in LR, and is far too tight between ~200px-wide
        // transitions in TB.
        nodeNode: direction === "TB" ? 48 : 28,
        nodeNodeBetweenLayers: direction === "TB" ? 64 : 80,
        nodeSize: (n) => {
          if (n.type === "place") return PLACE_SIZE;
          const d = n.data as TransitionNodeData;
          if (d.isInvisible) return TAU_SIZE;
          const width = Math.round(measureLabelWidth(d.label) + TRANSITION_PADDING);
          return {
            width: Math.min(Math.max(width, TRANSITION_MIN_WIDTH), TRANSITION_MAX_WIDTH),
            height: TRANSITION_HEIGHT,
          };
        },
      }).then((result) => {
        if (cancelled) return;
        autoNodesRef.current = result.nodes;
        const dragged = positionsRef.current ?? {};
        setNodes(result.nodes.map((n) => (dragged[n.id] ? { ...n, position: dragged[n.id]! } : n)));
        setEdges(result.edges);
        setFitKey(layoutFitKey);
        setLaid(true);
        setLaying(false);
      });
    };

    if (didFirstLayout.current) runLayout();
    else cancelScheduled = runAfterPaint(runLayout);

    return () => {
      cancelled = true;
      cancelScheduled?.();
    };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [
    net,
    direction,
    petri.mergeArcs,
    general.edgeRouting,
    general.nodeLabelMaxLength,
    petri.showInvisibleTransitions,
    petri.transitionLabelMode,
    petri.placeMode,
    petri.highlightMarkings,
    petri.showArcWeights,
  ]);

  const onNodeDragStop = useCallback<NodeMouseHandler>(
    (_, node) => onPositionsChange?.({ [node.id]: { x: node.position.x, y: node.position.y } }),
    [onPositionsChange],
  );

  if (!laid) {
    // A caller-sized box (a dashboard card) keeps its size – the skeleton's own
    // 640px frame would overflow it.
    if (className) {
      return (
        <div className={className}>
          <VizEmpty message="Laying out…" />
        </div>
      );
    }
    return <CanvasLayoutSkeleton />;
  }
  return (
    <CanvasShell
      nodes={nodes}
      edges={edges}
      nodeTypes={nodeTypes}
      edgeTypes={edgeTypes}
      fitViewKey={fitKey}
      miniMap={general.showMinimap}
      showGrid={general.showGrid}
      busy={busy || laying}
      settings={settings}
      overlay={overlay}
      className={className}
      onReset={() => {
        onResetPositions?.();
        setNodes([...autoNodesRef.current]);
      }}
      onNodesChange={onNodesChange}
      onEdgesChange={onEdgesChange}
      onNodeDragStop={onNodeDragStop}
    />
  );
}
