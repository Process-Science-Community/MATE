import type { WidgetPropSchema } from "@/lib/dashboard-queries";
import { VALUE_FORMATS, formatDurationShort, formatValue, type ValueFormat } from "@/lib/format";

export { VALUE_FORMATS, formatDurationShort, formatValue, type ValueFormat } from "@/lib/format";
import type {
  ColumnSpec,
  ColumnType,
  DatasetEnvelope,
  FieldMapping,
  KpiData,
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


export const VALUE_FORMAT_PROP: WidgetPropSchema = {
  type: "string",
  title: "Value format",
  enum: [...VALUE_FORMATS],
  enumLabels: ["Number", "Integer", "Percent (0-1)", "Percent (0-100)", "Duration (s)", "Date/time", "Text"],
  default: "number",
};

export const LEGEND_PROP: WidgetPropSchema = {
  type: "string",
  title: "Legend",
  enum: ["off", "bottom", "right"],
  enumLabels: ["Off", "Bottom", "Right"],
  default: "bottom",
  ui: { widget: "segmented" },
};

/** One KPI measure as text, honouring its `format`, the duration option, an
 * explicit `value_format` override (not "auto") and `decimals`. */
export function formatMeasure(m: KpiMeasure, options?: Record<string, unknown>): string {
  if (m.value == null) return "—";
  if (typeof m.value === "string") return m.value;
  const decimals = typeof options?.decimals === "number" ? options.decimals : undefined;
  const override = options?.value_format;
  if (typeof override === "string" && override !== "auto") return formatValue(m.value, override, decimals);
  if (m.format === "duration" || durationKeys(options).includes(m.key)) return formatDurationShort(m.value);
  return formatValue(m.value, m.format, decimals);
}

/** An option that is a list of ids: a string[] or a comma-separated string. */
export function listOption(options: Record<string, unknown> | undefined, key: string): string[] {
  const v = options?.[key];
  const raw = Array.isArray(v) ? v : typeof v === "string" ? v.split(",") : [];
  return raw.map((k) => String(k).trim()).filter(Boolean);
}

/** `legend` option: "off" | "bottom" | "right"; the legacy boolean `false` means off. */
export function legendPos(options: Record<string, unknown>): "off" | "bottom" | "right" {
  const v = options.legend;
  if (v === false || v === "off") return "off";
  return v === "right" ? "right" : "bottom";
}

/** recharts `<Legend>` props for a legend position. */
export function legendProps(pos: "bottom" | "right") {
  return pos === "right"
    ? ({ layout: "vertical", align: "right", verticalAlign: "middle", wrapperStyle: { fontSize: 11 } } as const)
    : ({ wrapperStyle: { fontSize: 11 } } as const);
}

export function kpiItems(d: DatasetEnvelope): KpiMeasure[] {
  return d.shape === "kpi" && d.data && "items" in d.data ? (d.data as KpiData).items : [];
}

/** The measures named in `keys` (in that order), or all of them when empty. */
export function pickMeasures(items: KpiMeasure[], keys: string[]): KpiMeasure[] {
  if (!keys.length) return items;
  return keys.map((k) => items.find((m) => m.key === k)).filter((m): m is KpiMeasure => !!m);
}

/** The measure named by the `measure` option, else the first numeric one, else the first. */
export function pickMeasure(items: KpiMeasure[], options: Record<string, unknown>): KpiMeasure | undefined {
  const key = typeof options.measure === "string" ? options.measure.trim() : "";
  return (key && items.find((m) => m.key === key)) || items.find((m) => typeof m.value === "number") || items[0];
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

const KPI_COLUMNS: ColumnSpec[] = [
  { id: "label", label: "Measure", type: "string", role: "dimension" },
  { id: "value", label: "Value", type: "number", role: "measure" },
];

/** The dataset as a table. A `kpi` dataset becomes one `{label, value, format}`
 * row per numeric measure, so category/value charts (pie, radar, ...) can plot
 * it; the value column is a duration when every measure is one. */
export function asTable(d: DatasetEnvelope): TableData {
  if (d.shape === "kpi") {
    const items = kpiItems(d).filter((m) => typeof m.value === "number");
    const rows = items.map((m) => ({ label: m.label, value: m.value, format: m.format }));
    const allDurations = items.length > 0 && items.every((m) => m.format === "duration");
    return {
      columns: allDurations ? [KPI_COLUMNS[0], { ...KPI_COLUMNS[1], type: "duration" }] : KPI_COLUMNS,
      rows,
    };
  }
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

/** How to print a column's values: an explicit `value_format` option wins,
 * else the column's own type/format (`duration` seconds, `percent100`, ...). */
export function columnFormat(
  cols: ColumnSpec[],
  id: string | undefined,
  options?: Record<string, unknown>,
): ValueFormat | undefined {
  const o = options?.value_format;
  if (typeof o === "string" && o !== "auto") return o as ValueFormat;
  const c = cols.find((col) => col.id === id);
  if (c?.type === "duration") return "duration";
  return c?.format as ValueFormat | undefined;
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
