"use client";

import type { KpiData, VizComponentProps } from "@/lib/visualizations/types";
import { VizEmpty } from "@/components/visualizations/viz-shell";
import { formatMeasure } from "@/components/visualizations/charts/chart-common";

/** Grid of headline measures for a `kpi`-shaped dataset (complexity metrics,
 * conformance score, ...). */
export function KpiGridViz({ dataset, options }: VizComponentProps) {
  const data = dataset.shape === "kpi" ? (dataset.data as KpiData) : { items: [] };
  if (data.items.length === 0) {
    return <VizEmpty message={dataset.meta?.note} />;
  }
  return (
    <div className="grid h-full auto-rows-min grid-cols-2 gap-2 overflow-auto sm:grid-cols-3">
      {data.items.map((m) => (
        <div key={m.key} className="rounded-lg border border-border bg-muted/20 p-2.5">
          <p className="truncate text-[11px] text-muted-foreground" title={m.label}>
            {m.label}
          </p>
          <p className="mt-0.5 text-lg font-semibold tabular-nums">
            {formatMeasure(m, options)}
            {m.unit && <span className="ml-0.5 text-xs font-normal text-muted-foreground">{m.unit}</span>}
          </p>
        </div>
      ))}
    </div>
  );
}
