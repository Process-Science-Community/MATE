"use client";

import type { VizComponentProps } from "@/lib/visualizations/types";
import { KpiGrid, KpiTile } from "@/components/dashboards/kit/kpi";
import { VizEmpty } from "@/components/visualizations/viz-shell";
import {
  formatMeasure,
  kpiItems,
  listOption,
  pickMeasures,
} from "@/components/visualizations/charts/chart-common";

/** Headline measures for a `kpi`-shaped dataset (complexity metrics,
 * conformance score, ...). `measures` picks and orders them; `layout` is a
 * wrapping grid or a single horizontal strip. */
export function KpiGridViz({ dataset, options }: VizComponentProps) {
  const items = pickMeasures(kpiItems(dataset), listOption(options, "measures"));
  if (items.length === 0) {
    return <VizEmpty message={dataset.meta?.note} />;
  }
  const tiles = items.map((m) => (
    <KpiTile
      key={m.key}
      label={m.label}
      value={`${formatMeasure(m, options)}${m.unit ? ` ${m.unit}` : ""}`}
      className={options.layout === "strip" ? "min-w-[120px] flex-1" : undefined}
    />
  ));
  return options.layout === "strip" ? (
    <div className="flex h-full items-start gap-2 overflow-x-auto">{tiles}</div>
  ) : (
    <KpiGrid minTileWidth={130} className="h-full auto-rows-min overflow-auto">
      {tiles}
    </KpiGrid>
  );
}
