"use client";

import { seriesColor, SERIES_SLOTS } from "@/components/dashboards/kit/palette";
import type { VizComponentProps } from "@/lib/visualizations/types";
import { VizEmpty } from "@/components/visualizations/viz-shell";
import { asTable, categoryCols, columnFormat, formatValue, resolveValue } from "./chart-common";

/** One 100% bar split by category (shares of a whole), with a legend of
 * labels, values and shares. Past the palette's slots the tail folds into
 * "Other". Accepts a table or a kpi dataset (measures become categories). */
export function ProportionBarViz({ dataset, mapping, options }: VizComponentProps) {
  const table = asTable(dataset);
  const cols = table.columns;
  const cat = (typeof mapping.category === "string" && mapping.category) || categoryCols(cols)[0]?.id;
  const val = resolveValue(cols, mapping);
  if (!cat || !val || table.rows.length === 0) {
    return <VizEmpty message={dataset.meta?.note ?? "Map a category and a value."} />;
  }
  const parts = table.rows
    .map((r) => ({ name: String(r[cat] ?? ""), value: Number(r[val]) || 0, format: r.format }))
    .filter((p) => p.value > 0);
  if (parts.length > SERIES_SLOTS) {
    parts.sort((a, b) => b.value - a.value); // fold the smallest, not the last rows
    const rest = parts.splice(SERIES_SLOTS - 1);
    parts.push({ name: "Other", value: rest.reduce((s, p) => s + p.value, 0), format: undefined });
  }
  const sum = parts.reduce((s, p) => s + p.value, 0);
  if (!sum) return <VizEmpty message={dataset.meta?.note} />;

  const colorOf = (p: { name: string }, i: number) =>
    p.name === "Other" && i === SERIES_SLOTS - 1 ? "var(--muted-foreground)" : seriesColor(i);
  const share = (v: number) => `${((v / sum) * 100).toFixed(1)}%`;
  // kpi rows carry their measure's format; a table column has one for all rows.
  const colFmt = columnFormat(cols, val, options);
  const fmt = (p: { value: number; format?: unknown }) => formatValue(p.value, colFmt ?? p.format);
  const labels = options.show_labels !== false;

  return (
    // safe center: an overflowing legend scrolls instead of being cut off at the top.
    <div className="flex h-full w-full flex-col gap-2 overflow-auto [justify-content:safe_center]">
      {/* shrink-0: the overflow-hidden bar would otherwise collapse to 0 when the legend is tall. */}
      <div className="flex h-6 w-full shrink-0 overflow-hidden rounded-md" role="img" aria-label="Proportions">
        {parts.map((p, i) => (
          <div
            key={p.name}
            title={`${p.name}: ${fmt(p)} (${share(p.value)})`}
            className="h-full border-r border-background last:border-r-0"
            style={{ width: `${(p.value / sum) * 100}%`, backgroundColor: colorOf(p, i) }}
          />
        ))}
      </div>
      {labels && (
        <ul className="flex flex-wrap gap-x-3 gap-y-1 text-[11px]">
          {parts.map((p, i) => (
            <li key={p.name} className="flex items-center gap-1.5">
              <span className="h-2 w-2 shrink-0 rounded-sm" style={{ backgroundColor: colorOf(p, i) }} />
              <span className="text-foreground">{p.name}</span>
              <span className="tabular-nums text-muted-foreground">
                {fmt(p)} · {share(p.value)}
              </span>
            </li>
          ))}
        </ul>
      )}
    </div>
  );
}
