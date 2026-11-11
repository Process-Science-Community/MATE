"use client";

import {
  CartesianGrid,
  Cell,
  Funnel,
  FunnelChart,
  LabelList,
  PolarAngleAxis,
  PolarGrid,
  Radar,
  RadarChart,
  RadialBar,
  RadialBarChart,
  ResponsiveContainer,
  Scatter,
  ScatterChart,
  Tooltip,
  Treemap,
  XAxis,
  YAxis,
  ZAxis,
} from "recharts";

import { formatNumber } from "@/lib/format";
import type { VizComponentProps } from "@/lib/visualizations/types";
import { statusColor } from "@/components/dashboards/kit/palette";
import { VizEmpty } from "@/components/visualizations/viz-shell";
import {
  asTable,
  categoryCols,
  columnFormat,
  formatValue,
  CHART_COLORS,
  formatMeasure,
  kpiItems,
  labelOf,
  listOption,
  numericCols,
  pickMeasure,
  pickMeasures,
  resolveValue,
} from "./chart-common";

const numFmt = (v: unknown) => (typeof v === "number" ? formatNumber(v) : String(v ?? ""));
const AXIS = { tick: { fontSize: 10 }, stroke: "currentColor", className: "text-muted-foreground" } as const;

type Tone = "good" | "warning" | "danger" | "neutral";
const TONE_COLOR: Record<Tone, string> = {
  good: statusColor("good"),
  warning: statusColor("warning"),
  danger: statusColor("critical"),
  neutral: "var(--muted-foreground)",
};

/** `bands` option: "lt:tone:label" entries, comma-separated, first match wins;
 * an entry without `lt` matches everything (e.g. "0.01:good:Significant,
 * 0.05:warning:Marginal,:neutral:Not significant"). */
export function bandFor(bands: unknown, v: number): { tone: Tone; label: string } | undefined {
  if (typeof bands !== "string") return undefined;
  for (const entry of bands.split(",")) {
    const [lt, tone, ...label] = entry.split(":").map((p) => p.trim());
    if (lt !== "" && !(v < Number(lt))) continue;
    const t = (["good", "warning", "danger", "neutral"] as const).find((x) => x === tone) ?? "neutral";
    return { tone: t, label: label.join(":") };
  }
  return undefined;
}

/** Single headline number: the `measure` option, else the first numeric
 * measure. `prefix`, `decimals` and threshold `bands` (verdict) apply. */
export function NumberTileViz({ dataset, options }: VizComponentProps) {
  const m = pickMeasure(kpiItems(dataset), options);
  if (!m) return <VizEmpty message={dataset.meta?.note} />;
  const band = typeof m.value === "number" ? bandFor(options.bands, m.value) : undefined;
  const prefix = typeof options.prefix === "string" ? options.prefix : "";
  return (
    <div className="flex h-full flex-col items-center justify-center text-center">
      <p className="text-xs text-muted-foreground">{m.label}</p>
      <p className="mt-1 text-4xl font-bold tabular-nums" style={band ? { color: TONE_COLOR[band.tone] } : undefined}>
        {prefix}
        {formatMeasure(m, options)}
        {m.unit && <span className="ml-1 text-base font-normal text-muted-foreground">{m.unit}</span>}
      </p>
      {band?.label && (
        <p className="mt-1 rounded-full border px-2 py-0.5 text-[11px]" style={{ borderColor: TONE_COLOR[band.tone] }}>
          {band.label}
        </p>
      )}
    </div>
  );
}

const optNum = (v: unknown) => (typeof v === "number" && Number.isFinite(v) ? v : undefined);

/** Radial gauge for one KPI measure (`measure` option, else the first). The
 * scale is `min`..`max`; without `max` a percent measure uses 0..1 and any
 * other measure its share of the sum of all measures (legacy behaviour). */
export function GaugeViz({ dataset, options }: VizComponentProps) {
  const items = kpiItems(dataset);
  const m = pickMeasure(items, options);
  if (!m || typeof m.value !== "number") {
    return <VizEmpty message={dataset.meta?.note ?? "Needs a numeric KPI."} />;
  }
  const min = optNum(options.min) ?? 0;
  const total = items.reduce((s, i) => (typeof i.value === "number" ? s + i.value : s), 0) || 1;
  const max = optNum(options.max) ?? (m.format === "percent" ? 1 : m.format === "percent100" ? 100 : total);
  const frac = Math.max(0, Math.min(1, (m.value - min) / (max - min || 1)));
  const data = [{ name: m.label, value: frac * 100, fill: CHART_COLORS[0] }];
  return (
    <ResponsiveContainer width="100%" height="100%">
      <RadialBarChart innerRadius="68%" outerRadius="100%" data={data} startAngle={90} endAngle={-270}>
        <PolarAngleAxis type="number" domain={[0, 100]} tick={false} />
        <RadialBar dataKey="value" background cornerRadius={8} />
        <text x="50%" y="46%" textAnchor="middle" dominantBaseline="middle" className="fill-foreground text-xl font-bold">
          {formatMeasure(m, options)}
        </text>
        <text x="50%" y="66%" textAnchor="middle" className="fill-muted-foreground text-[10px]">
          {m.label}
        </text>
      </RadialBarChart>
    </ResponsiveContainer>
  );
}

/** KPI measures (`measures` option, else all; max 8) as concentric radial
 * bars on a shared `min`..`max` scale (default 0..largest). */
export function RadialBarViz({ dataset, options }: VizComponentProps) {
  const items = pickMeasures(kpiItems(dataset), listOption(options, "measures"))
    .filter((i) => typeof i.value === "number")
    .slice(0, 8);
  if (!items.length) return <VizEmpty message={dataset.meta?.note} />;
  const max = optNum(options.max) ?? Math.max(...items.map((i) => i.value as number), 1);
  const min = optNum(options.min) ?? 0;
  const data = items.map((i, idx) => ({
    name: i.label,
    value: i.value as number,
    fill: CHART_COLORS[idx % CHART_COLORS.length],
  }));
  const byName = new Map(items.map((i) => [i.label, i]));
  return (
    <div className="flex h-full w-full flex-col gap-1">
      <div className="min-h-0 flex-1">
        <ResponsiveContainer width="100%" height="100%">
          <RadialBarChart innerRadius="20%" outerRadius="100%" data={data}>
            <PolarAngleAxis type="number" domain={[min, max]} tick={false} />
            <RadialBar dataKey="value" cornerRadius={4} />
            <Tooltip
              formatter={(v, _n, p) => {
                const m = byName.get(String((p as { payload?: { name?: string } })?.payload?.name));
                return m ? formatMeasure(m, options) : numFmt(v);
              }}
              contentStyle={{ fontSize: 12 }}
            />
          </RadialBarChart>
        </ResponsiveContainer>
      </div>
      <ul className="flex shrink-0 flex-wrap justify-center gap-x-2.5 gap-y-0.5 text-[10px]">
        {data.map((d) => (
          <li key={d.name} className="flex items-center gap-1">
            <span className="h-2 w-2 shrink-0 rounded-sm" style={{ backgroundColor: d.fill }} />
            <span className="text-muted-foreground">{d.name}</span>
          </li>
        ))}
      </ul>
    </div>
  );
}

/** Radar/spider: a category column on the angles, one measure as the radius. */
export function RadarViz({ dataset, mapping }: VizComponentProps) {
  const table = asTable(dataset);
  const cols = table.columns;
  const cat = (typeof mapping.category === "string" && mapping.category) || categoryCols(cols)[0]?.id;
  const val = resolveValue(cols, mapping);
  if (!cat || !val || !table.rows.length) {
    return <VizEmpty message={dataset.meta?.note ?? "Map a category and a value."} />;
  }
  const data = table.rows.slice(0, 12).map((r) => ({ name: String(r[cat] ?? ""), value: Number(r[val]) || 0 }));
  return (
    <ResponsiveContainer width="100%" height="100%">
      <RadarChart data={data} cx="50%" cy="50%" outerRadius="65%">
        <PolarGrid className="stroke-border/40" />
        <PolarAngleAxis dataKey="name" tick={{ fontSize: 10 }} className="text-muted-foreground" />
        <Radar dataKey="value" stroke={CHART_COLORS[0]} fill={CHART_COLORS[0]} fillOpacity={0.4} />
        <Tooltip formatter={(v) => numFmt(v)} contentStyle={{ fontSize: 12 }} />
      </RadarChart>
    </ResponsiveContainer>
  );
}

/** Funnel: a category + value, sorted descending. */
export function FunnelViz({ dataset, mapping }: VizComponentProps) {
  const table = asTable(dataset);
  const cols = table.columns;
  const cat = (typeof mapping.category === "string" && mapping.category) || categoryCols(cols)[0]?.id;
  const val = resolveValue(cols, mapping);
  if (!cat || !val || !table.rows.length) {
    return <VizEmpty message={dataset.meta?.note ?? "Map a category and a value."} />;
  }
  const data = [...table.rows]
    .map((r) => ({ name: String(r[cat] ?? ""), value: Number(r[val]) || 0 }))
    .sort((a, b) => b.value - a.value)
    .slice(0, 10)
    .map((d, i) => ({ ...d, fill: CHART_COLORS[i % CHART_COLORS.length] }));
  return (
    <ResponsiveContainer width="100%" height="100%">
      {/* Room on the right for the stage labels. */}
      <FunnelChart margin={{ top: 4, right: 110, bottom: 4, left: 4 }}>
        <Tooltip formatter={(v) => numFmt(v)} contentStyle={{ fontSize: 12 }} />
        <Funnel dataKey="value" data={data} isAnimationActive>
          {/* Custom label: the default wraps multi-word names into a column over the next stage. */}
          <LabelList
            dataKey="name"
            content={({ x, y, width, height, value }) => (
              <text
                x={Number(x) + Number(width) + 6}
                y={Number(y) + Number(height) / 2}
                dominantBaseline="middle"
                className="fill-muted-foreground text-[10px]"
              >
                {String(value ?? "")}
              </text>
            )}
          />
        </Funnel>
      </FunnelChart>
    </ResponsiveContainer>
  );
}

/** Treemap: a category + value (area-proportional tiles). */
export function TreemapViz({ dataset, mapping }: VizComponentProps) {
  const table = asTable(dataset);
  const cols = table.columns;
  const cat = (typeof mapping.category === "string" && mapping.category) || categoryCols(cols)[0]?.id;
  const val = resolveValue(cols, mapping);
  if (!cat || !val || !table.rows.length) {
    return <VizEmpty message={dataset.meta?.note ?? "Map a category and a value."} />;
  }
  const data = table.rows
    .map((r, i) => ({ name: String(r[cat] ?? ""), size: Number(r[val]) || 0, fill: CHART_COLORS[i % CHART_COLORS.length] }))
    .filter((d) => d.size > 0)
    .slice(0, 30);
  if (!data.length) return <VizEmpty />;
  return (
    <ResponsiveContainer width="100%" height="100%">
      <Treemap data={data} dataKey="size" stroke="var(--background)" isAnimationActive={false}>
        <Tooltip formatter={(v) => numFmt(v)} contentStyle={{ fontSize: 12 }} />
      </Treemap>
    </ResponsiveContainer>
  );
}

/** Bubble: x/y numeric + a size measure. */
export function BubbleViz({ dataset, mapping }: VizComponentProps) {
  const table = asTable(dataset);
  const cols = table.columns;
  const nums = numericCols(cols);
  const x = (typeof mapping.x === "string" && mapping.x) || nums[0]?.id;
  const y = (typeof mapping.y === "string" && mapping.y) || nums[1]?.id || nums[0]?.id;
  const size = (typeof mapping.size === "string" && mapping.size) || nums[2]?.id || nums[0]?.id;
  if (!x || !y || !table.rows.length) {
    return <VizEmpty message={dataset.meta?.note ?? "Needs numeric columns."} />;
  }
  const fmtOf = (id: string) => (v: unknown) => formatValue(v, columnFormat(cols, id));
  const byName = new Map([x, y, size].map((id) => [labelOf(cols, id), fmtOf(id)]));
  return (
    <ResponsiveContainer width="100%" height="100%">
      <ScatterChart margin={{ top: 8, right: 16, bottom: 4, left: 4 }}>
        <CartesianGrid strokeDasharray="3 3" className="stroke-border/40" />
        <XAxis type="number" dataKey={x} name={labelOf(cols, x)} tickFormatter={fmtOf(x)} width={48} {...AXIS} />
        <YAxis type="number" dataKey={y} name={labelOf(cols, y)} tickFormatter={fmtOf(y)} width={56} {...AXIS} />
        <ZAxis type="number" dataKey={size} range={[30, 400]} name={labelOf(cols, size)} />
        <Tooltip
          cursor={{ strokeDasharray: "3 3" }}
          formatter={(v, name) => (byName.get(String(name)) ?? numFmt)(v)}
          contentStyle={{ fontSize: 12 }}
        />
        <Scatter data={table.rows} fill={CHART_COLORS[0]} fillOpacity={0.6}>
          {table.rows.map((_, i) => (
            <Cell key={i} fill={CHART_COLORS[i % CHART_COLORS.length]} />
          ))}
        </Scatter>
      </ScatterChart>
    </ResponsiveContainer>
  );
}
