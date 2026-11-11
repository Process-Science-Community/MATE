"use client";

import {
  Area,
  AreaChart,
  Bar,
  BarChart,
  CartesianGrid,
  Cell,
  LabelList,
  Legend,
  Line,
  LineChart,
  Pie,
  PieChart,
  ReferenceLine,
  ResponsiveContainer,
  Scatter,
  ScatterChart,
  Tooltip,
  XAxis,
  YAxis,
  ZAxis,
} from "recharts";

import { formatNumber } from "@/lib/format";
import type { ColumnSpec, VizComponentProps } from "@/lib/visualizations/types";
import { VizEmpty } from "@/components/visualizations/viz-shell";
import {
  asTable,
  buildChartData,
  categoryCols,
  columnFormat,
  CHART_COLORS,
  formatValue,
  labelOf,
  legendPos,
  legendProps,
  listOption,
  numericCols,
  resolveSeries,
  resolveValue,
  resolveX,
  resolveYs,
} from "./chart-common";

const AXIS = {
  tick: { fontSize: 10 },
  stroke: "currentColor",
  className: "text-muted-foreground",
} as const;
const TOOLTIP = { contentStyle: { fontSize: 12 } } as const;
const MARGIN = { top: 8, right: 16, bottom: 4, left: 4 } as const;

const numFmt = (v: unknown) => (typeof v === "number" ? formatNumber(v) : String(v ?? ""));
const pctTick = (v: unknown) => `${Math.round(Number(v) * 100)}%`;
const OTHER = "Other";
const OTHER_COLOR = "var(--muted-foreground)";

const total = (row: Record<string, unknown>, keys: string[]) =>
  keys.reduce((s, k) => s + (Number(row[k]) || 0), 0);

/** Keep the `n` series keys with the largest totals; sum the rest into "Other". */
function foldSeries(data: Array<Record<string, unknown>>, keys: string[], n: number) {
  if (n <= 0 || keys.length <= n) return { data, keys };
  const sums = new Map(keys.map((k) => [k, data.reduce((s, r) => s + (Number(r[k]) || 0), 0)]));
  const keep = [...keys].sort((a, b) => sums.get(b)! - sums.get(a)!).slice(0, n);
  const rest = keys.filter((k) => !keep.includes(k));
  return {
    data: data.map((r) => ({ ...r, [OTHER]: total(r, rest) })),
    keys: [...keep, OTHER],
  };
}

/** Shared bar/line/area renderer: x category/time on the axis, one or more y
 * measures (or a pivoted `series`) as colored series. Bars: `top_n`/`sort` pick
 * categories; line/area: `top_n` keeps the largest series and folds the rest
 * into "Other". */
function Cartesian({
  dataset,
  mapping,
  options,
  kind,
}: VizComponentProps & { kind: "bar" | "line" | "area" }) {
  const table = asTable(dataset);
  const cols = table.columns;
  const x = resolveX(cols, mapping);
  const series = resolveSeries(cols, mapping);
  const stacking = options.stacked === true || options.stack_mode === "percent";
  const first = resolveYs(cols, mapping);
  // A stack of one series says nothing: unmapped, stack every measure in the first one's unit.
  // Only for cards that set stack_mode (presets/inspector): legacy `stacked: true` cards keep one series.
  const ys =
    stacking && kind !== "line" && options.stack_mode != null && !series && mapping.y == null && first.length === 1
      ? numericCols(cols)
          .filter((c) => columnFormat(cols, c.id) === columnFormat(cols, first[0]))
          .map((c) => c.id)
      : first;

  if (!x || ys.length === 0 || table.rows.length === 0) {
    return <VizEmpty message={dataset.meta?.note ?? "Map an X and a measure to plot."} />;
  }

  const built = buildChartData(table, x, ys, series);
  const topN = Math.max(0, Math.floor(Number(options.top_n) || 0));
  let { data, keys } = built;
  if (kind === "bar") {
    if (options.sort === "asc" || options.sort === "desc") {
      const dir = options.sort === "asc" ? 1 : -1;
      data = [...data].sort((a, b) => dir * (total(a, keys) - total(b, keys)));
    }
    if (topN) data = data.slice(0, topN);
  } else {
    ({ data, keys } = foldSeries(data, keys, topN));
  }

  // A line never stacks, so a stack_mode left over from a bar/area preset must not turn ticks into %.
  const percent = kind !== "line" && options.stack_mode === "percent";
  const stackId = options.stacked || percent ? "stack" : undefined;
  const horizontal = kind === "bar" && options.orientation === "horizontal";
  const compact = kind === "line" && options.compact === true;
  const legend = compact || keys.length < 2 ? "off" : legendPos(options);
  const fmt = (v: unknown) => formatValue(v, columnFormat(cols, ys[0], options));
  // Tooltip rows: each y column in its own format (pivoted series use the first y).
  const tipFmt = (v: unknown, _n: unknown, item: { dataKey?: unknown }) =>
    formatValue(v, columnFormat(cols, series ? ys[0] : String(item?.dataKey ?? ys[0]), options));
  const nameFor = (k: string) => (series || k === OTHER ? k : labelOf(cols, k));
  const colorFor = (k: string, i: number) => (k === OTHER ? OTHER_COLOR : CHART_COLORS[i % CHART_COLORS.length]);
  const curve = options.curve === "monotone" || options.curve === "step" ? options.curve : "linear";

  const body = keys.map((k, i) => {
    const color = colorFor(k, i);
    if (kind === "bar") {
      return (
        <Bar
          key={k}
          dataKey={k}
          name={nameFor(k)}
          fill={color}
          stackId={stackId}
          radius={stackId ? 0 : horizontal ? [0, 2, 2, 0] : [2, 2, 0, 0]}
        >
          {options.show_values === true && (
            <LabelList
              dataKey={k}
              position={stackId ? "center" : horizontal ? "right" : "top"}
              formatter={fmt}
              className="fill-muted-foreground text-[10px]"
            />
          )}
        </Bar>
      );
    }
    if (kind === "line") {
      return (
        <Line
          key={k}
          type={curve}
          dataKey={k}
          name={nameFor(k)}
          stroke={color}
          dot={options.dots === true && !compact}
          strokeWidth={compact ? 1.5 : 2}
          isAnimationActive={!compact}
        />
      );
    }
    return (
      <Area
        key={k}
        type={curve}
        dataKey={k}
        name={nameFor(k)}
        stroke={color}
        fill={color}
        fillOpacity={stackId ? 0.5 : 0.2}
        stackId={stackId}
      />
    );
  });

  const valueTick = percent ? pctTick : fmt;
  const catAxis = horizontal ? (
    <YAxis type="category" dataKey={x} width={96} {...AXIS} />
  ) : (
    <XAxis dataKey={x} {...AXIS} />
  );
  const valAxis = horizontal ? (
    <XAxis type="number" tickFormatter={valueTick} {...AXIS} />
  ) : (
    <YAxis tickFormatter={valueTick} width={56} {...AXIS} />
  );

  const common = compact ? (
    <>
      <XAxis dataKey={x} hide />
      <YAxis hide domain={["auto", "auto"]} />
      <Tooltip {...TOOLTIP} formatter={tipFmt} />
      {body}
    </>
  ) : (
    <>
      <CartesianGrid strokeDasharray="3 3" className="stroke-border/40" />
      {catAxis}
      {valAxis}
      <Tooltip {...TOOLTIP} formatter={tipFmt} />
      {legend !== "off" && <Legend {...legendProps(legend)} />}
      {body}
    </>
  );

  const offset = percent ? "expand" : undefined;
  return (
    <ResponsiveContainer width="100%" height="100%">
      {kind === "bar" ? (
        <BarChart
          data={data}
          // Room for the value labels right of horizontal bars.
          margin={horizontal && options.show_values === true ? { ...MARGIN, right: 40 } : MARGIN}
          layout={horizontal ? "vertical" : "horizontal"} stackOffset={offset}>
          {common}
        </BarChart>
      ) : kind === "line" ? (
        <LineChart data={data} margin={compact ? { top: 4, right: 4, bottom: 4, left: 4 } : MARGIN}>{common}</LineChart>
      ) : (
        <AreaChart data={data} margin={MARGIN} stackOffset={offset}>{common}</AreaChart>
      )}
    </ResponsiveContainer>
  );
}

export function BarViz(props: VizComponentProps) {
  return <Cartesian {...props} kind="bar" />;
}
export function LineViz(props: VizComponentProps) {
  return <Cartesian {...props} kind="line" />;
}
export function AreaViz(props: VizComponentProps) {
  return <Cartesian {...props} kind="area" />;
}

const isNum = (cols: ColumnSpec[], id: string) => numericCols(cols).some((c) => c.id === id);
const timeTick = (v: unknown) => new Date(Number(v)).toLocaleDateString();

/** Scatter: x/y columns, optional color series. `x_time` plots x as a time
 * axis (dotted chart); a non-numeric y becomes a category axis. */
export function ScatterViz({ dataset, mapping, options }: VizComponentProps) {
  const table = asTable(dataset);
  const cols = table.columns;
  const nums = numericCols(cols);
  const pick = (k: string) => (typeof mapping[k] === "string" && cols.some((c) => c.id === mapping[k]) ? (mapping[k] as string) : undefined);
  const x = pick("x") ?? (options.x_time === true ? cols.find((c) => c.type === "datetime")?.id : undefined) ?? nums[0]?.id;
  // A time axis needs a time column (or an explicit mapping), else numbers read as 1970 dates.
  const xTime = options.x_time === true && (!!pick("x") || cols.find((c) => c.id === x)?.type === "datetime");
  const y = pick("y") ?? nums.find((c) => c.id !== x)?.id ?? nums[0]?.id;
  const series = resolveSeries(cols, mapping);
  if (!x || !y || table.rows.length === 0) {
    return <VizEmpty message={dataset.meta?.note ?? "Needs two numeric columns."} />;
  }
  const rows = xTime
    ? table.rows.map((r) => ({ ...r, [x]: typeof r[x] === "number" ? r[x] : Date.parse(String(r[x])) }))
    : table.rows;
  const groups = new Map<string, Array<Record<string, unknown>>>();
  for (const r of rows) {
    const g = series ? String(r[series] ?? "") : "";
    groups.set(g, [...(groups.get(g) ?? []), r]);
  }
  const dot = Number(options.dot_size);
  const area = dot > 0 ? Math.PI * dot * dot : undefined;
  const yNum = isNum(cols, y);
  const xFmt = (v: unknown) => (xTime ? new Date(Number(v)).toLocaleString() : formatValue(v, columnFormat(cols, x)));
  const yFmt = (v: unknown) => (yNum ? formatValue(v, columnFormat(cols, y)) : String(v ?? ""));
  const legend = series && groups.size > 1 ? legendPos(options) : "off";
  return (
    <ResponsiveContainer width="100%" height="100%">
      <ScatterChart margin={MARGIN}>
        <CartesianGrid strokeDasharray="3 3" className="stroke-border/40" />
        <XAxis
          type="number"
          dataKey={x}
          name={labelOf(cols, x)}
          domain={xTime ? ["dataMin", "dataMax"] : undefined}
          tickFormatter={xTime ? timeTick : xFmt}
          {...AXIS}
        />
        <YAxis
          type={yNum ? "number" : "category"}
          dataKey={y}
          name={labelOf(cols, y)}
          tickFormatter={yNum ? yFmt : undefined}
          reversed={options.reverse_y === true}
          width={yNum ? 56 : 80}
          {...AXIS}
        />
        {area && <ZAxis type="number" range={[area, area]} />}
        <Tooltip
          cursor={{ strokeDasharray: "3 3" }}
          {...TOOLTIP}
          formatter={(v, name) => (name === labelOf(cols, x) ? xFmt(v) : yFmt(v))}
        />
        {legend !== "off" && <Legend {...legendProps(legend)} />}
        {[...groups.entries()].map(([g, data], i) => (
          <Scatter key={g || "all"} name={g || labelOf(cols, y)} data={data} fill={CHART_COLORS[i % CHART_COLORS.length]} />
        ))}
      </ScatterChart>
    </ResponsiveContainer>
  );
}

export function PieViz({ dataset, mapping, options }: VizComponentProps) {
  const table = asTable(dataset);
  const cols = table.columns;
  const cat = (typeof mapping.category === "string" && mapping.category) || categoryCols(cols)[0]?.id || cols[0]?.id;
  const val = resolveValue(cols, mapping);
  if (!cat || !val || table.rows.length === 0) {
    return <VizEmpty message={dataset.meta?.note ?? "Map a category and a value."} />;
  }
  // Default: data order, first 12 slices (stored cards). `top_n` sorts largest
  // first and folds the tail past n into "Other".
  const colFmt = columnFormat(cols, val, options);
  const topN = Math.max(0, Math.floor(Number(options.top_n) || 0));
  let data = table.rows
    .map((r) => ({ name: String(r[cat] ?? ""), value: Number(r[val]) || 0, format: colFmt ?? r.format }))
    .filter((d) => d.value > 0);
  if (!topN) data = data.slice(0, 12);
  else {
    data.sort((a, b) => b.value - a.value);
    if (data.length > topN) {
      const rest = data.splice(topN - 1);
      data.push({ name: OTHER, value: rest.reduce((s, d) => s + d.value, 0), format: colFmt });
    }
  }
  if (data.length === 0) return <VizEmpty />;
  return (
    <ResponsiveContainer width="100%" height="100%">
      <PieChart>
        <Tooltip {...TOOLTIP} formatter={(v, _n, item) => formatValue(v, item?.payload?.format)} />
        <Pie data={data} dataKey="value" nameKey="name" innerRadius="45%" outerRadius="80%" paddingAngle={1}>
          {data.map((d, i) => (
            <Cell key={i} fill={d.name === OTHER ? OTHER_COLOR : CHART_COLORS[i % CHART_COLORS.length]} />
          ))}
        </Pie>
        <Legend wrapperStyle={{ fontSize: 11 }} />
      </PieChart>
    </ResponsiveContainer>
  );
}

/** Histogram. Raw mode bins one numeric column (`bins`); prebinned mode plots
 * `bin_start`/`bin_end`/`count` rows as they are. `reference_keys` draws a
 * marker per key: its value comes from `meta.scalars` or the first row. */
export function HistogramViz({ dataset, mapping, options }: VizComponentProps) {
  const table = asTable(dataset);
  const cols = table.columns;
  const has = (id: string | undefined) => !!id && cols.some((c) => c.id === id);
  const pick = (k: string) => (typeof mapping[k] === "string" && has(mapping[k] as string) ? (mapping[k] as string) : undefined);
  const countCol = pick("count") ?? (has("count") ? "count" : undefined);
  const startCol = pick("bin_start") ?? (has("bin_start") ? "bin_start" : numericCols(cols)[0]?.id);
  const endCol = pick("bin_end") ?? (has("bin_end") ? "bin_end" : undefined);
  const prebinned =
    options.mode === "prebinned" || (options.mode !== "raw" && !!countCol && (!!pick("bin_start") || has("bin_start")));
  const fmt = (v: unknown) =>
    formatValue(v, columnFormat(cols, prebinned ? startCol : resolveValue(cols, mapping), options));

  let buckets: Array<{ label: string; lo: number; hi: number; count: number }>;
  if (prebinned) {
    if (!countCol || !startCol || table.rows.length === 0) {
      return <VizEmpty message={dataset.meta?.note ?? "Map bin start and count columns."} />;
    }
    buckets = table.rows.map((r) => {
      const lo = Number(r[startCol]);
      const hi = endCol ? Number(r[endCol]) : lo;
      return { label: endCol ? `${fmt(lo)}–${fmt(hi)}` : fmt(lo), lo, hi, count: Number(r[countCol]) || 0 };
    });
  } else {
    const val = resolveValue(cols, mapping);
    if (!val || table.rows.length === 0) {
      return <VizEmpty message={dataset.meta?.note ?? "Map a numeric column to bin."} />;
    }
    const values = table.rows.map((r) => Number(r[val])).filter((v) => Number.isFinite(v));
    if (values.length === 0) return <VizEmpty />;
    const bins = Math.max(3, Math.min(30, Number(options.bins) || 10));
    const min = Math.min(...values);
    const max = Math.max(...values);
    const width = (max - min) / bins || 1;
    buckets = Array.from({ length: bins }, (_, i) => ({
      label: fmt(min + i * width),
      lo: min + i * width,
      hi: min + (i + 1) * width,
      count: 0,
    }));
    for (const v of values) {
      let idx = Math.floor((v - min) / width);
      if (idx >= bins) idx = bins - 1;
      if (idx < 0) idx = 0;
      buckets[idx].count += 1;
    }
  }

  // Reference markers: one line per bucket; markers in the same bucket share it.
  const scalars = (dataset.meta as { scalars?: Record<string, unknown> } | undefined)?.scalars ?? {};
  const markers = new Map<string, string[]>();
  for (const key of listOption(options, "reference_keys")) {
    const v = Number(key in scalars ? scalars[key] : table.rows[0]?.[key]);
    if (!Number.isFinite(v) || buckets.length === 0) continue;
    const b =
      buckets.find((b) => v >= b.lo && v < b.hi) ??
      (v < buckets[0].lo ? buckets[0] : buckets[buckets.length - 1]);
    markers.set(b.label, [...(markers.get(b.label) ?? []), `${labelOf(cols, key)} ${fmt(v)}`]);
  }

  return (
    <ResponsiveContainer width="100%" height="100%">
      <BarChart data={buckets} margin={MARGIN}>
        <CartesianGrid strokeDasharray="3 3" className="stroke-border/40" />
        <XAxis dataKey="label" {...AXIS} />
        <YAxis allowDecimals={false} tickFormatter={numFmt} width={40} {...AXIS} />
        <Tooltip {...TOOLTIP} formatter={(v) => [numFmt(v), "Count"]} />
        <Bar dataKey="count" fill={CHART_COLORS[0]} radius={[2, 2, 0, 0]} />
        {[...markers.entries()].map(([label, texts]) => (
          <ReferenceLine
            key={label}
            x={label}
            stroke="var(--foreground)"
            strokeDasharray="4 3"
            label={{ value: texts.join(" · "), position: "insideTopRight", fontSize: 10, fill: "currentColor" }}
          />
        ))}
      </BarChart>
    </ResponsiveContainer>
  );
}
