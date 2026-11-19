"use client";

import { Fragment } from "react";

import type { VizComponentProps } from "@/lib/visualizations/types";
import { VizEmpty } from "@/components/visualizations/viz-shell";
import {
  asTable,
  categoryCols,
  columnFormat,
  formatValue,
  legendPos,
  numericCols,
  resolveValue,
} from "./chart-common";

/** Colour at `t` (0..1, or -1..1 when diverging), mixed from the kit palette
 * tokens so it follows the theme. */
function cellColor(t: number, diverging: boolean): string {
  const pct = Math.round(Math.min(1, Math.abs(t)) * 100);
  if (diverging) {
    const end = t < 0 ? "var(--ff-div-low)" : "var(--ff-div-high)";
    return `color-mix(in oklab, ${end} ${pct}%, var(--ff-div-mid))`;
  }
  return `color-mix(in oklab, var(--ff-seq-700) ${pct}%, var(--ff-seq-100))`;
}

const SCALES: Record<string, (v: number) => number> = {
  linear: (v) => v,
  sqrt: (v) => Math.sqrt(v),
  log: (v) => Math.log1p(v),
};

const keyOf = (x: string, y: string) => JSON.stringify([x, y]);

/** Matrix heatmap from a long table: x and y category columns plus a numeric
 * value (duplicates are summed). A dense matrix arrives as `{row, col, value}`
 * rows from the adapter, so `col`/`row` are the default x/y. A wide table with
 * one category column (activity stats, a time series) shows category x numeric
 * column instead, each column shaded against its own max. Hand-rolled grid
 * (no recharts primitive). */
export function HeatmapViz({ output, mapping, options }: VizComponentProps) {
  const table = asTable(output);
  const cols = table.columns;
  const cats = categoryCols(cols);
  const has = (id: string) => cats.some((c) => c.id === id);
  const matrix = has("col") && has("row");
  const mappedX = typeof mapping.x === "string" && mapping.x ? mapping.x : undefined;
  const mappedY = typeof mapping.y === "string" && mapping.y ? mapping.y : undefined;
  const xCol = mappedX || (matrix ? "col" : cats[0]?.id);
  const yCol = mappedY || (matrix ? "row" : cats[1]?.id);
  const val = resolveValue(cols, mapping);
  const nums = numericCols(cols);
  const wide = !matrix && !mappedX && !mappedY && cats.length === 1 && nums.length > 0;
  if (!wide && (!xCol || !yCol || !val || table.rows.length === 0)) {
    return <VizEmpty message={output.meta?.note ?? "Needs two category columns and a value."} />;
  }

  const xs: string[] = [];
  const ys: string[] = [];
  const cell = new Map<string, number>();
  const add = (x: string, y: string, raw: unknown) => {
    const v = Number(raw);
    if (raw == null || raw === "" || !Number.isFinite(v)) return;
    if (!xs.includes(x)) xs.push(x);
    if (!ys.includes(y)) ys.push(y);
    cell.set(keyOf(x, y), (cell.get(keyOf(x, y)) ?? 0) + v);
  };
  for (const r of table.rows) {
    if (wide) for (const c of nums) add(c.label, String(r[cats[0].id] ?? ""), r[c.id]);
    else add(String(r[xCol!] ?? ""), String(r[yCol!] ?? ""), r[val!]);
  }
  if (cell.size === 0) return <VizEmpty message={output.meta?.note} />;
  // Wide mode: per-column format and scale (seconds and counts never share one).
  const colOf = new Map(nums.map((c) => [c.label, c.id]));

  const maxCells = Math.max(2, Math.floor(Number(options.max_cells) || 60));
  const X = xs.slice(0, maxCells);
  const Y = ys.slice(0, maxCells);
  const hideDiag = options.hide_diagonal === true;
  const diverging = options.palette === "diverging";
  const scale = SCALES[String(options.scale)] ?? SCALES.linear;
  const fmt = (v: number, x?: string) =>
    // Wide mode: each column's own unit beats a preset's single value_format.
    formatValue(v, wide && x ? columnFormat(cols, colOf.get(x)) : columnFormat(cols, val, options));
  const showValues = options.show_values === true;
  const rotate = options.rotate_labels === true;
  const legend = legendPos(options);
  // Square cells for a matrix; a wide table's few columns would make huge squares.
  const cellBox = wide ? "h-5" : "aspect-square";
  const valueAt = (x: string, y: string) => (hideDiag && x === y ? undefined : cell.get(keyOf(x, y)));

  let maxAbs = 0;
  let minV = Infinity;
  let maxV = -Infinity;
  for (const x of X) {
    for (const y of Y) {
      const v = valueAt(x, y);
      if (v == null) continue;
      maxAbs = Math.max(maxAbs, Math.abs(v));
      minV = Math.min(minV, v);
      maxV = Math.max(maxV, v);
    }
  }
  const norm = scale(maxAbs) || 1;
  const colMax = new Map(
    X.map((x) => [x, scale(Math.max(0, ...Y.map((y) => Math.abs(valueAt(x, y) ?? 0)))) || 1]),
  );
  const tOf = (v: number, x: string) =>
    Math.sign(v) * (scale(Math.abs(v)) / (wide ? colMax.get(x)! : norm));
  const truncated = xs.length > X.length || ys.length > Y.length;

  const ramp = diverging
    ? `linear-gradient(to right, ${cellColor(-1, true)}, ${cellColor(0, true)}, ${cellColor(1, true)})`
    : `linear-gradient(to right, ${cellColor(0, false)}, ${cellColor(1, false)})`;
  // Wide mode has a scale per column, so no single value legend.
  const legendEl =
    legend === "off" || wide || !Number.isFinite(minV) ? null : (
      <div className="flex items-center gap-1.5 text-[9px] tabular-nums text-muted-foreground">
        <span>{fmt(diverging ? -maxAbs : Math.min(0, minV))}</span>
        <div className="h-2 w-24 rounded-sm" style={{ background: ramp }} />
        <span>{fmt(diverging ? maxAbs : maxV)}</span>
      </div>
    );

  return (
    <div className={`flex h-full w-full gap-2 ${legend === "right" ? "flex-row items-start" : "flex-col"}`}>
      <div className="min-h-0 min-w-0 flex-1 overflow-auto">
        <div
          className={`${wide ? "grid" : "inline-grid"} gap-px`}
          style={{ gridTemplateColumns: `auto repeat(${X.length}, minmax(${showValues ? 28 : 14}px, 1fr))` }}
        >
          <div />
          {X.map((x) => (
            <div
              key={x}
              className="truncate px-0.5 text-[9px] text-muted-foreground"
              style={rotate ? { writingMode: "vertical-rl", transform: "rotate(180deg)", maxHeight: 96 } : undefined}
              title={x}
            >
              {x}
            </div>
          ))}
          {Y.map((y) => (
            <Fragment key={y}>
              <div className="max-w-[120px] truncate pr-1 text-[9px] text-muted-foreground" title={y}>
                {y}
              </div>
              {X.map((x) => {
                const v = valueAt(x, y);
                if (v == null) return <div key={x} className={`${cellBox} rounded-[1px] bg-muted/30`} />;
                const t = tOf(v, x);
                return (
                  <div
                    key={x}
                    title={`${y} · ${x}: ${fmt(v, x)}`}
                    className={`flex ${cellBox} items-center justify-center overflow-hidden rounded-[1px] text-[9px] tabular-nums`}
                    style={{
                      backgroundColor: cellColor(t, diverging),
                      // seq-100 is the ramp's far end, so it contrasts with strong cells in both themes.
                      color: Math.abs(t) > 0.55 ? (diverging ? "white" : "var(--ff-seq-100)") : "var(--foreground)",
                    }}
                  >
                    {showValues && fmt(v, x)}
                  </div>
                );
              })}
            </Fragment>
          ))}
        </div>
      </div>
      {(legendEl || truncated) && (
        <div className="flex shrink-0 flex-wrap items-center gap-x-3 gap-y-1">
          {legendEl}
          {truncated && (
            <span className="text-[9px] text-muted-foreground">
              Showing {X.length} of {xs.length} columns, {Y.length} of {ys.length} rows
            </span>
          )}
        </div>
      )}
    </div>
  );
}
