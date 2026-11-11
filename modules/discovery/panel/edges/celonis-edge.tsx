"use client";

import {
  BaseEdge,
  getBezierPath,
  useInternalNode,
  type EdgeProps,
} from "@xyflow/react";

import {
  buildCelonisEdgePath,
  type CelonisEdgeGeometry,
  type CelonisRect,
} from "../layout/celonis-flow";
import { renderLabel } from "@/components/visualizations/artifacts/edges";

/**
 * Renderer for the "Process flow (Celonis)" edges. All routing decisions live
 * in the layout's per-edge PLAN (`geo.plan`); this component rebuilds the
 * measured Celonis geometry from the CURRENT node rects on every render via
 * `buildCelonisEdgePath` — so dragging re-routes continuously and entries pick
 * whichever node side the approach hits. The arrowhead is the measured
 * Celonis triangle drawn manually (SVG marker orientation is unreliable on
 * curved paths). No animation, thin uniform-ish strokes — styling comes from
 * the canvas via `style`.
 */

export function CelonisEdge(props: EdgeProps) {
  const {
    id,
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

  const geo = (data as { celonis?: CelonisEdgeGeometry } | undefined)?.celonis;

  // Live node rects (update continuously during drags). Hooks must run
  // unconditionally; they're cheap subscriptions.
  const srcNode = useInternalNode(props.source);
  const tgtNode = useInternalNode(props.target);

  if (geo?.plan && srcNode && tgtNode) {
    const rectOf = (n: NonNullable<typeof srcNode>): CelonisRect => ({
      x: n.internals.positionAbsolute.x,
      y: n.internals.positionAbsolute.y,
      w: n.measured.width ?? 0,
      h: n.measured.height ?? 0,
    });
    const built = buildCelonisEdgePath(rectOf(srcNode), rectOf(tgtNode), geo.plan);
    const strokeColor =
      (style as React.CSSProperties | undefined)?.stroke?.toString() ?? "var(--muted-foreground)";
    return (
      <>
        <BaseEdge id={id} path={built.path} style={style} />
        <g
          transform={`translate(${built.arrow.x} ${built.arrow.y}) rotate(${built.arrow.angle}) scale(${built.arrow.scale})`}
          style={{ pointerEvents: "none" }}
        >
          <path d="M 0 0 L -9.746 9.997 L -9.74 -10.003 Z" style={{ fill: strokeColor }} />
        </g>
        {renderLabel({
          label,
          labelX: built.labelPoint.x,
          labelY: built.labelPoint.y,
          labelStyle,
          labelBgPadding,
          labelBgBorderRadius,
        })}
      </>
    );
  }

  // Plan missing (shouldn't happen in steady state — e.g. a mid-refresh
  // frame): plain bezier keeps the edge visible until the next layout.
  const [path, labelX, labelY] = getBezierPath({
    sourceX,
    sourceY,
    targetX,
    targetY,
    sourcePosition,
    targetPosition,
  });
  return (
    <>
      <BaseEdge id={id} path={path} style={style} />
      {renderLabel({ label, labelX, labelY, labelStyle, labelBgPadding, labelBgBorderRadius })}
    </>
  );
}
