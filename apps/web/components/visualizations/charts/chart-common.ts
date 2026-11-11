import { formatNumber } from "@/lib/format";
import type {
  ColumnSpec,
  ColumnType,
  DatasetEnvelope,
  FieldMapping,
  KpiMeasure,
  TableData,
} from "@/lib/visualizations/types";

/** Viz option (table + KPI vizzes): column ids / measure keys whose seconds
 * value is shown as a duration. Picked per column in the viz settings. */
export const DURATION_OPTION = "duration_columns";

export function durationKeys(options: Record<string, unknown> | undefined): string[] {
  const v = options?.[DURATION_OPTION];
  return Array.isArray(v) ? v.filter((k): k is string => typeof k === "string") : [];
}

/** Seconds as the two largest units: "1d 2h", "3h 4m", "12m", "40s". */
export function formatDurationShort(seconds: number): string {
  if (!Number.isFinite(seconds)) return "—";
  const sign = seconds < 0 ? "-" : "";
  const s = Math.round(Math.abs(seconds));
  const d = Math.floor(s / 86_400);
  const h = Math.floor((s % 86_400) / 3_600);
  const m = Math.floor((s % 3_600) / 60);
  if (d) return `${sign}${d}d${h ? ` ${h}h` : ""}`;
  if (h) return `${sign}${h}h${m ? ` ${m}m` : ""}`;
  if (m) return `${sign}${m}m`;
  return `${sign}${s}s`;
}

/** One KPI measure as text, honouring its `format` and the duration option. */
export function formatMeasure(m: KpiMeasure, options?: Record<string, unknown>): string {
  if (m.value == null) return "—";
  if (typeof m.value === "string") return m.value;
  const v = m.value;
  if (m.format === "duration" || durationKeys(options).includes(m.key)) return formatDurationShort(v);
  if (m.format === "percent") return `${(v * 100).toFixed(1)}%`;
  if (m.format === "integer") return formatNumber(Math.round(v));
  return formatNumber(v);
}

/** Categorical palette for series/cells. Kept explicit (not theme tokens) so
 * recharts `fill`/`stroke` get a concrete color without a CSS-var round-trip. */
export const CHART_COLORS = [
  "#6366f1",
  "#10b981",
  "#f59e0b",
  "#ef4444",
  "#06b6d4",
  "#8b5cf6",
  "#ec4899",
  "#84cc16",
  "#f97316",
  "#14b8a6",
];

const NUMERIC: ColumnType[] = ["number", "integer", "duration"];

export function asTable(d: DatasetEnvelope): TableData {
  return d.shape === "table" && d.data && "columns" in d.data
    ? (d.data as TableData)
    : { columns: [], rows: [] };
}

export function numericCols(cols: ColumnSpec[]): ColumnSpec[] {
  return cols.filter((c) => NUMERIC.includes(c.type));
}

export function categoryCols(cols: ColumnSpec[]): ColumnSpec[] {
  return cols.filter((c) => !NUMERIC.includes(c.type));
}

function asArray(v: string | string[] | undefined): string[] {
  if (v == null) return [];
  return Array.isArray(v) ? v : [v];
}

/** x column: explicit mapping → first datetime → first category → first column. */
export function resolveX(cols: ColumnSpec[], mapping: FieldMapping): string | undefined {
  const m = mapping.x;
  if (typeof m === "string" && cols.some((c) => c.id === m)) return m;
  return cols.find((c) => c.type === "datetime")?.id ?? categoryCols(cols)[0]?.id ?? cols[0]?.id;
}

/** y measures: explicit mapping → first numeric column. */
export function resolveYs(cols: ColumnSpec[], mapping: FieldMapping): string[] {
  const m = asArray(mapping.y).filter((id) => cols.some((c) => c.id === id));
  if (m.length) return m;
  const first = numericCols(cols)[0]?.id;
  return first ? [first] : [];
}

export function resolveSeries(cols: ColumnSpec[], mapping: FieldMapping): string | undefined {
  const m = mapping.series;
  return typeof m === "string" && cols.some((c) => c.id === m) ? m : undefined;
}

/** A single value column: explicit mapping → first numeric column. */
export function resolveValue(cols: ColumnSpec[], mapping: FieldMapping, key = "value"): string | undefined {
  const m = mapping[key];
  if (typeof m === "string" && cols.some((c) => c.id === m)) return m;
  return numericCols(cols)[0]?.id;
}

export function labelOf(cols: ColumnSpec[], id: string): string {
  return cols.find((c) => c.id === id)?.label ?? id;
}

/**
 * Build chart data. With a `series` column (and a single y) pivot long→wide so
 * each distinct series value becomes its own key (summed per x). Otherwise pass
 * the rows through and plot each y measure as its own series.
 */
export function buildChartData(
  table: TableData,
  x: string,
  ys: string[],
  series?: string,
): { data: Array<Record<string, unknown>>; keys: string[] } {
  if (series && ys.length === 1) {
    const y = ys[0];
    const byX = new Map<string, Record<string, unknown>>();
    const keys = new Set<string>();
    for (const row of table.rows) {
      const xv = String(row[x] ?? "");
      const sv = String(row[series] ?? "");
      keys.add(sv);
      const bucket = byX.get(xv) ?? { [x]: row[x] };
      const n = Number(row[y]);
      bucket[sv] = (Number(bucket[sv]) || 0) + (Number.isFinite(n) ? n : 0);
      byX.set(xv, bucket);
    }
    return { data: [...byX.values()], keys: [...keys] };
  }
  return { data: table.rows, keys: ys };
}
