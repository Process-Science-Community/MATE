"use client";

/**
 * Edge renderers + shared edge geometry for graph canvases. `ElkEdge` draws the
 * routes `layout.elkLayout` returns (Petri nets, Heuristics); the helpers below
 * are also what discovery's server-layout DFG edges (`waypoint-edge`,
 * `routed-edge`, `celonis-edge`) build on. Runtime external.
 */

import {
  BaseEdge,
  EdgeLabelRenderer,
  getBezierPath,
  useInternalNode,
  type EdgeProps,
  type InternalNode,
  type Node,
} from "@xyflow/react";

import type { ElkRoute } from "./layout";

export const DRIFT_TOLERANCE_PX = 1.5;
/** Measured Celonis arrow triangle: tip at the origin, body pointing back. */
export const ARROW = "M 0 0 L -9.746 9.997 L -9.74 -10.003 Z";

export type Point = { x: number; y: number };

export interface Rect {
  x: number;
  y: number;
  w: number;
  h: number;
}

/** Layout-time node top-lefts, used to detect that a node has since moved. */
export interface ExpectedRects {
  sx: number;
  sy: number;
  tx: number;
  ty: number;
}

export function rectOf(node: InternalNode<Node>): Rect {
  return {
    x: node.internals.positionAbsolute.x,
    y: node.internals.positionAbsolute.y,
    w: node.measured.width ?? 220,
    h: node.measured.height ?? 59,
  };
}

export function arrowTransform(from: Point, to: Point, scale: number): string {
  const angle = (Math.atan2(to.y - from.y, to.x - from.x) * 180) / Math.PI;
  return `translate(${to.x} ${to.y}) rotate(${angle}) scale(${scale})`;
}

export function arrowTransformAt(x: number, y: number, angle: number, scale: number): string {
  return `translate(${x} ${y}) rotate(${angle}) scale(${scale})`;
}

/**
 * Has either endpoint moved away from where the layout put it? A dragged node
 * (or a merged persisted position) leaves a precomputed route pinned to empty
 * space, so the renderers fall back to a plain bezier until the next pass.
 */
export function hasDrifted(
  srcNode: InternalNode<Node> | undefined,
  tgtNode: InternalNode<Node> | undefined,
  expected: ExpectedRects | undefined,
): boolean {
  if (!expected || !srcNode || !tgtNode) return true;
  return (
    Math.abs(srcNode.internals.positionAbsolute.x - expected.sx) > DRIFT_TOLERANCE_PX ||
    Math.abs(srcNode.internals.positionAbsolute.y - expected.sy) > DRIFT_TOLERANCE_PX ||
    Math.abs(tgtNode.internals.positionAbsolute.x - expected.tx) > DRIFT_TOLERANCE_PX ||
    Math.abs(tgtNode.internals.positionAbsolute.y - expected.ty) > DRIFT_TOLERANCE_PX
  );
}

export function strokeOf(style: EdgeProps["style"]): { color: string; width: number } {
  const css = style as React.CSSProperties | undefined;
  return {
    color: css?.stroke?.toString() ?? "var(--muted-foreground)",
    width: Number(css?.strokeWidth ?? 1.5),
  };
}

export function arrowScaleFor(strokeWidth: number): number {
  return Math.min(0.5 + strokeWidth / 12, 1);
}

export function renderLabel({
  label,
  labelX,
  labelY,
  labelStyle,
  labelBgPadding,
  labelBgBorderRadius,
}: {
  label: EdgeProps["label"];
  labelX: number;
  labelY: number;
  labelStyle?: React.CSSProperties;
  labelBgPadding?: [number, number];
  labelBgBorderRadius?: number;
}) {
  if (label === undefined || label === null || label === "") return null;
  return (
    <EdgeLabelRenderer>
      <div
        style={{
          position: "absolute",
          transform: `translate(-50%, -50%) translate(${labelX}px, ${labelY}px)`,
          padding: labelBgPadding
            ? `${labelBgPadding[1]}px ${labelBgPadding[0]}px`
            : "2px 4px",
          borderRadius: labelBgBorderRadius ?? 4,
          border: "1px solid var(--border)",
          background: "var(--card)",
          fontSize: 10,
          whiteSpace: "nowrap",
          color: "var(--muted-foreground)",
          pointerEvents: "all",
          ...labelStyle,
        }}
        className="nodrag nopan"
      >
        {label}
      </div>
    </EdgeLabelRenderer>
  );
}

interface SelfLoopArgs {
  id: string;
  rect: Rect;
  style: EdgeProps["style"];
  strokeColor: string;
  arrowScale: number;
  label: EdgeProps["label"];
  labelStyle: EdgeProps["labelStyle"];
  labelBgPadding: EdgeProps["labelBgPadding"];
  labelBgBorderRadius: EdgeProps["labelBgBorderRadius"];
}

/** Right-side arc built from the LIVE rect, so it keeps hugging during drags. */
export function renderSelfLoop({
  id,
  rect,
  style,
  strokeColor,
  arrowScale,
  label,
  labelStyle,
  labelBgPadding,
  labelBgBorderRadius,
}: SelfLoopArgs) {
  const right = rect.x + rect.w;
  const midY = rect.y + rect.h / 2;
  const excursion = 46;
  const path =
    `M ${right - 4} ${midY + rect.h * 0.28} ` +
    `C ${right + excursion} ${midY + rect.h * 0.9}, ` +
    `${right + excursion} ${midY - rect.h * 0.9}, ` +
    `${right - 4} ${midY - rect.h * 0.28}`;
  return (
    <>
      <BaseEdge id={id} path={path} style={style} />
      <g
        transform={arrowTransform(
          { x: right + excursion * 0.4, y: midY - rect.h * 0.6 },
          { x: right - 4, y: midY - rect.h * 0.28 },
          arrowScale,
        )}
        style={{ pointerEvents: "none" }}
      >
        <path d={ARROW} style={{ fill: strokeColor }} />
      </g>
      {renderLabel({
        label,
        labelX: right + excursion,
        labelY: midY,
        labelStyle,
        labelBgPadding,
        labelBgBorderRadius,
      })}
    </>
  );
}

/**
 * Edge renderer for the ELK layered layouts (Petri net, Heuristics net).
 *
 * ELK already routes every edge into its own channel and returns the result as
 * section start/bend/end points; the built-in `smoothstep` edge ignores all of
 * it and re-derives a naive right-angle path between two centred handles, which
 * stacks arcs on top of each other. This draws ELK's actual route: straight
 * runs joined by filleted corners.
 *
 * Once a live node rect drifts from `data.elkRoute.expected` (a drag), the
 * route is pinned to empty space, so it falls back to a plain bezier until the
 * next layout pass. The arrowhead is drawn manually rather than via
 * `markerEnd`: SVG's `orient="auto"` takes its angle from the final
 * sub-segment, which a corner fillet can collapse.
 */
export interface ElkEdgeData extends Record<string, unknown> {
  elkRoute?: ElkRoute;
  /** Corner fillet radius; 0 draws hard right angles. */
  cornerRadius?: number;
}

export function ElkEdge(props: EdgeProps) {
  const {
    id,
    source,
    target,
    sourceX,
    sourceY,
    targetX,
    targetY,
    sourcePosition,
    targetPosition,
    style,
    label,
    labelStyle,
    labelBgPadding,
    labelBgBorderRadius,
    data,
  } = props;

  const edgeData = (data ?? {}) as ElkEdgeData;
  // Hooks must run unconditionally; they're cheap subscriptions.
  const srcNode = useInternalNode(source);
  const tgtNode = useInternalNode(target);

  const { color: strokeColor, width: strokeWidth } = strokeOf(style);
  const arrowScale = arrowScaleFor(strokeWidth);
  const route = edgeData.elkRoute;

  if (!route || route.points.length < 2 || hasDrifted(srcNode, tgtNode, route.expected)) {
    const [bezier, labelX, labelY] = getBezierPath({
      sourceX,
      sourceY,
      targetX,
      targetY,
      sourcePosition,
      targetPosition,
    });
    return (
      <>
        <BaseEdge id={id} path={bezier} style={style} />
        {renderLabel({ label, labelX, labelY, labelStyle, labelBgPadding, labelBgBorderRadius })}
      </>
    );
  }

  // Re-anchor the endpoints to the LIVE rects. Anchoring to `sourceX/sourceY`
  // instead would drag the first point to the node's centred handle and break
  // the first orthogonal run – ELK's port sits off-centre by design, that
  // offset IS the fan-out that keeps sibling arcs apart.
  const srcRect = rectOf(srcNode!);
  const tgtRect = rectOf(tgtNode!);
  const points: Point[] = [
    { x: srcRect.x + route.sourceOffset.x, y: srcRect.y + route.sourceOffset.y },
    ...route.points.slice(1, -1),
    { x: tgtRect.x + route.targetOffset.x, y: tgtRect.y + route.targetOffset.y },
  ];

  const path = roundedPolyline(points, edgeData.cornerRadius ?? 10);
  const tip = points[points.length - 1]!;
  const beforeTip = points[points.length - 2]!;
  const anchor = midSegment(points);

  return (
    <>
      <BaseEdge id={id} path={path} style={style} />
      <g transform={arrowTransform(beforeTip, tip, arrowScale)} style={{ pointerEvents: "none" }}>
        <path d={ARROW} style={{ fill: strokeColor }} />
      </g>
      {renderLabel({
        label,
        labelX: anchor.x,
        labelY: anchor.y,
        labelStyle,
        labelBgPadding,
        labelBgBorderRadius,
      })}
    </>
  );
}

/** Midpoint of the middle segment – a straight run, so the label sits clear. */
function midSegment(points: Point[]): Point {
  const i = Math.floor((points.length - 1) / 2);
  const a = points[i]!;
  const b = points[i + 1] ?? a;
  return { x: (a.x + b.x) / 2, y: (a.y + b.y) / 2 };
}

/**
 * Straight `L` runs joined by a quadratic corner at each interior point. The
 * radius is clamped to half of the shorter adjacent run so a fillet can never
 * eat past the neighbouring bend; the closing `L` guarantees a clean tangent
 * at the target for the arrowhead.
 */
function roundedPolyline(points: Point[], cornerRadius: number): string {
  if (points.length === 0) return "";
  if (points.length === 1) return `M ${points[0]!.x} ${points[0]!.y}`;
  if (points.length === 2 || cornerRadius <= 0) {
    return points.map((p, i) => `${i === 0 ? "M" : "L"} ${p.x} ${p.y}`).join(" ");
  }

  let d = `M ${points[0]!.x} ${points[0]!.y}`;
  for (let i = 1; i < points.length - 1; i++) {
    const prev = points[i - 1]!;
    const curr = points[i]!;
    const next = points[i + 1]!;

    const v1x = curr.x - prev.x;
    const v1y = curr.y - prev.y;
    const v2x = next.x - curr.x;
    const v2y = next.y - curr.y;
    const len1 = Math.hypot(v1x, v1y);
    const len2 = Math.hypot(v2x, v2y);

    if (len1 === 0 || len2 === 0) {
      d += ` L ${curr.x} ${curr.y}`;
      continue;
    }

    const r = Math.min(cornerRadius, len1 / 2, len2 / 2);
    const before = { x: curr.x - (v1x / len1) * r, y: curr.y - (v1y / len1) * r };
    const after = { x: curr.x + (v2x / len2) * r, y: curr.y + (v2y / len2) * r };

    d += ` L ${before.x} ${before.y} Q ${curr.x} ${curr.y} ${after.x} ${after.y}`;
  }
  d += ` L ${points[points.length - 1]!.x} ${points[points.length - 1]!.y}`;
  return d;
}
