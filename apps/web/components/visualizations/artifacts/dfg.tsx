"use client";

/**
 * `DfgView` – the platform viewer for `dfg` artifacts, on the generic
 * ELK process map. Discovery keeps its own `DfgCanvas` (server-persisted
 * layouts, filters, details panel); this is the plain read-only map any other
 * owner of a DFG – a dashboard card, a workspace – renders. Runtime external.
 */

import { useMemo, type ReactNode } from "react";

import { graphFromDfg } from "@/lib/visualizations/adapters";
import { ProcessMap } from "@/components/visualizations/graphs/process-map";

/** A `dfg` artifact payload (`DfgPayload` in `@mate/module-sdk-ts`). */
export type DfgViewData = {
  activities: { id: string; label?: string; frequency?: number }[];
  edges: { id?: string; source: string; target: string; frequency: number; performance_seconds?: number }[];
  start_activities?: Record<string, number>;
  end_activities?: Record<string, number>;
};

export function DfgView({
  dfg,
  settings,
  busy,
}: {
  dfg: DfgViewData;
  /** Popover body of the canvas control cluster. */
  settings?: ReactNode;
  /** A refetch is in flight while the current map stays on screen. */
  busy?: boolean;
}) {
  const graph = useMemo(() => graphFromDfg(dfg), [dfg]);
  return <ProcessMap graph={graph} emptyMessage="No activities in this DFG." settings={settings} busy={busy} />;
}
