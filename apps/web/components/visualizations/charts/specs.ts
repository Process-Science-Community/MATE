/** Catalog groups "Charts" + "Matrices": registry entries for chart-like vizzes. */

import type { WidgetConfigSchema, WidgetPropSchema } from "@/lib/dashboard-queries";
import type { ColumnType, VizSpec } from "@/lib/visualizations/types";

import { LEGEND_PROP, VALUE_FORMAT_PROP } from "./chart-common";
import { HeatmapViz } from "./heatmap-viz";
import { ProportionBarViz } from "./proportion-bar";
import { AreaViz, BarViz, HistogramViz, LineViz, PieViz, ScatterViz } from "./recharts-viz";
import { BubbleViz, FunnelViz, RadarViz, TreemapViz } from "./recharts-extra";

const NUM: ColumnType[] = ["number", "integer", "duration"];
const CAT: ColumnType[] = ["string", "boolean", "enum", "datetime"];

const TOP_N: WidgetPropSchema = {
  type: "integer",
  title: "Top N (0 = all)",
  minimum: 0,
  maximum: 50,
  step: 1,
  default: 0,
  ui: { widget: "slider" },
};

const STACK_MODE: WidgetPropSchema = {
  type: "string",
  title: "Stack mode",
  description: "Percent stacks every bar/area to 100%.",
  enum: ["normal", "percent"],
  enumLabels: ["Values", "100%"],
  default: "normal",
  ui: { widget: "segmented" },
};

const CURVE: WidgetPropSchema = {
  type: "string",
  title: "Line shape",
  enum: ["linear", "monotone", "step"],
  enumLabels: ["Straight", "Smooth", "Step"],
  default: "linear",
  ui: { widget: "segmented" },
};

/** Category + value examples, shared by pie/radar/funnel/treemap/proportion-bar. */
const CATEGORY_EXAMPLE = {
  activities: [
    { activity: "Create order", count: 120 },
    { activity: "Check stock", count: 95 },
    { activity: "Ship order", count: 80 },
    { activity: "Send invoice", count: 64 },
  ],
};

const TREND_EXAMPLE = {
  periods: [
    { period: "2024-01-01", cases: 40, mean_duration_s: 7200 },
    { period: "2024-02-01", cases: 52, mean_duration_s: 6600 },
    { period: "2024-03-01", cases: 47, mean_duration_s: 8100 },
    { period: "2024-04-01", cases: 61, mean_duration_s: 5900 },
  ],
};

const CATEGORY_FIELDS = [
  { key: "category", label: "Category", accepts: CAT, required: true },
  { key: "value", label: "Value", accepts: NUM, required: true },
];

const CATEGORY_NEEDS =
  "A table with one category column and one numeric column, or a kpi dataset (each numeric measure becomes a slice).";

const options = (properties: WidgetConfigSchema["properties"]): WidgetConfigSchema => ({ properties });

export const chartSpecs: VizSpec[] = [
  {
    id: "bar",
    title: "Bar chart",
    icon: "BarChart3",
    group: "chart",
    description: "Compare a measure across categories, vertical or horizontal.",
    needs: "A table with a category column (X) and at least one numeric column (Y); an optional series column splits the bars.",
    example: CATEGORY_EXAMPLE,
    accepts: ["table"],
    fields: [
      { key: "x", label: "Category (X)", accepts: CAT, required: true },
      { key: "y", label: "Measure (Y)", accepts: NUM, multiple: true, required: true },
      { key: "series", label: "Series (color)", accepts: CAT },
    ],
    options: options({
      orientation: {
        type: "string",
        title: "Orientation",
        enum: ["vertical", "horizontal"],
        enumLabels: ["Vertical", "Horizontal"],
        default: "vertical",
        ui: { widget: "segmented" },
      },
      sort: {
        type: "string",
        title: "Sort",
        enum: ["none", "desc", "asc"],
        enumLabels: ["Data order", "Largest first", "Smallest first"],
        default: "none",
      },
      top_n: TOP_N,
      stacked: { type: "boolean", title: "Stacked" },
      stack_mode: STACK_MODE,
      value_format: VALUE_FORMAT_PROP,
      show_values: { type: "boolean", title: "Show values" },
      legend: LEGEND_PROP,
    }),
    defaults: { w: 4, h: 8, minW: 2, minH: 5 },
    Component: BarViz,
    presets: [
      {
        id: "bar-ranked",
        title: "Ranked bars",
        description: "Top 10 categories as horizontal bars, largest first.",
        icon: "BarChartHorizontal",
        options: { orientation: "horizontal", sort: "desc", top_n: 10, show_values: true },
      },
      {
        id: "bar-stacked",
        title: "Stacked bars",
        description: "Series stacked on top of each other per category.",
        icon: "ChartColumnStacked",
        options: { stacked: true, stack_mode: "normal" },
      },
      {
        id: "bar-100",
        title: "100% stacked bars",
        description: "Each category's series as shares of 100%.",
        icon: "Percent",
        options: { stacked: true, stack_mode: "percent" },
      },
    ],
  },
  {
    id: "line",
    title: "Line chart",
    icon: "LineChart",
    group: "chart",
    description: "Show how one or more measures change over time or order.",
    needs: "A table with an X column (usually a date) and at least one numeric column; an optional series column draws one line per value.",
    example: TREND_EXAMPLE,
    accepts: ["table"],
    fields: [
      { key: "x", label: "X axis", accepts: CAT, required: true },
      { key: "y", label: "Measure (Y)", accepts: NUM, multiple: true, required: true },
      { key: "series", label: "Series (color)", accepts: CAT },
    ],
    options: options({
      curve: CURVE,
      dots: { type: "boolean", title: "Show points" },
      compact: { type: "boolean", title: "Compact (sparkline)", description: "Hide axes, grid and legend." },
      value_format: VALUE_FORMAT_PROP,
      legend: LEGEND_PROP,
    }),
    defaults: { w: 4, h: 8, minW: 2, minH: 5 },
    Component: LineViz,
    presets: [
      {
        id: "line-multi",
        title: "Metric trends",
        description: "Several measures over time, one smooth line each.",
        icon: "TrendingUp",
        options: { curve: "monotone", legend: "bottom" },
      },
      {
        id: "sparkline",
        title: "Sparkline",
        description: "A tiny trend line without axes, for a quick glance.",
        icon: "Activity",
        options: { compact: true, curve: "monotone" },
      },
    ],
  },
  {
    id: "area",
    title: "Area chart",
    icon: "AreaChart",
    group: "chart",
    description: "Volume over time; stack series to show composition.",
    needs: "A table with an X column (usually a date) and at least one numeric column; an optional series column stacks one area per value.",
    example: TREND_EXAMPLE,
    accepts: ["table"],
    fields: [
      { key: "x", label: "X axis", accepts: CAT, required: true },
      { key: "y", label: "Measure (Y)", accepts: NUM, multiple: true, required: true },
      { key: "series", label: "Series (color)", accepts: CAT },
    ],
    options: options({
      stacked: { type: "boolean", title: "Stacked" },
      stack_mode: STACK_MODE,
      top_n: { ...TOP_N, title: "Top N series (rest = Other)" },
      curve: CURVE,
      value_format: VALUE_FORMAT_PROP,
      legend: LEGEND_PROP,
    }),
    defaults: { w: 4, h: 8, minW: 2, minH: 5 },
    Component: AreaViz,
    presets: [
      {
        id: "area-stacked",
        title: "Composition over time",
        description: "Stacked areas of the top series as shares of 100%, the rest as Other.",
        icon: "Layers",
        options: { stacked: true, stack_mode: "percent", top_n: 6, curve: "monotone" },
      },
    ],
  },
  {
    id: "scatter",
    title: "Scatter plot",
    icon: "ScatterChart",
    group: "chart",
    description: "Plot two measures against each other to spot correlation.",
    needs: "A table with two numeric columns (or a date and a category column for a dotted chart); an optional color column groups the dots.",
    example: {
      cases: [
        { case_id: "c1", events: 5, duration_s: 3600 },
        { case_id: "c2", events: 8, duration_s: 9100 },
        { case_id: "c3", events: 3, duration_s: 1500 },
        { case_id: "c4", events: 11, duration_s: 15200 },
      ],
    },
    accepts: ["table"],
    fields: [
      { key: "x", label: "X", accepts: [...NUM, "datetime"], required: true },
      { key: "y", label: "Y", accepts: [...NUM, "string", "enum"], required: true },
      { key: "series", label: "Color", accepts: CAT },
    ],
    options: options({
      x_time: { type: "boolean", title: "X is time", description: "Read X as dates (dotted chart)." },
      reverse_y: { type: "boolean", title: "Reverse Y" },
      dot_size: {
        type: "integer",
        title: "Dot size",
        minimum: 1,
        maximum: 12,
        step: 1,
        default: 4,
        ui: { widget: "slider" },
      },
      legend: LEGEND_PROP,
    }),
    defaults: { w: 3, h: 8, minW: 2, minH: 5 },
    Component: ScatterViz,
    presets: [
      {
        id: "dotted-chart",
        title: "Dotted chart",
        description: "Events as dots: time on X, case on Y, colored by activity.",
        options: { x_time: true, reverse_y: true, dot_size: 3 },
      },
    ],
  },
  {
    id: "pie",
    title: "Pie / donut",
    icon: "PieChart",
    group: "chart",
    description: "Shares of a whole for a handful of categories.",
    needs: CATEGORY_NEEDS,
    example: CATEGORY_EXAMPLE,
    accepts: ["table", "kpi"],
    fields: CATEGORY_FIELDS,
    options: options({
      top_n: { ...TOP_N, title: "Top N slices (rest = Other)", description: "0 keeps data order (first 12)." },
    }),
    defaults: { w: 3, h: 8, minW: 2, minH: 5 },
    Component: PieViz,
  },
  {
    id: "proportion-bar",
    title: "Proportion bar",
    icon: "SquareSplitHorizontal",
    group: "chart",
    description: "One 100% bar split into category shares, with a legend.",
    needs: CATEGORY_NEEDS,
    example: CATEGORY_EXAMPLE,
    accepts: ["table", "kpi"],
    fields: CATEGORY_FIELDS,
    options: options({
      show_labels: { type: "boolean", title: "Show legend", default: true },
      value_format: VALUE_FORMAT_PROP,
    }),
    defaults: { w: 4, h: 6, minW: 2, minH: 3 },
    Component: ProportionBarViz,
  },
  {
    id: "histogram",
    title: "Histogram",
    icon: "BarChartBig",
    group: "chart",
    description: "Distribution of a numeric column, binned or pre-binned.",
    needs: "A table with one numeric column to bin, or pre-binned rows with bin_start, bin_end and count columns.",
    example: {
      bins: [
        { bin_start: 0, bin_end: 3600, count: 12 },
        { bin_start: 3600, bin_end: 7200, count: 30 },
        { bin_start: 7200, bin_end: 10800, count: 18 },
        { bin_start: 10800, bin_end: 14400, count: 6 },
      ],
    },
    accepts: ["table"],
    fields: [
      { key: "value", label: "Value", accepts: NUM, required: true },
      { key: "bin_start", label: "Bin start (pre-binned)", accepts: NUM },
      { key: "bin_end", label: "Bin end (pre-binned)", accepts: NUM },
      { key: "count", label: "Count (pre-binned)", accepts: NUM },
    ],
    options: options({
      mode: {
        type: "string",
        title: "Mode",
        description: "Auto uses pre-binned rows when a count column is mapped or named count.",
        enum: ["auto", "raw", "prebinned"],
        enumLabels: ["Auto", "Bin values", "Pre-binned"],
        default: "auto",
        ui: { widget: "segmented" },
      },
      bins: {
        type: "integer",
        title: "Bins",
        minimum: 3,
        maximum: 30,
        step: 1,
        default: 10,
        ui: { widget: "slider" },
      },
      value_format: { ...VALUE_FORMAT_PROP, title: "Bin label format" },
      reference_keys: {
        type: "string",
        title: "Reference lines",
        description: "Comma-separated keys (meta.scalars or first-row columns) drawn as markers.",
      },
    }),
    defaults: { w: 3, h: 8, minW: 2, minH: 5 },
    Component: HistogramViz,
  },
  {
    id: "heatmap",
    title: "Heatmap",
    icon: "Grid3x3",
    group: "matrix",
    description: "A colour-shaded matrix of two categories and a value.",
    needs: "A long table with two category columns (x, y) and a numeric value; a dense matrix is expanded into row/col/value rows.",
    example: {
      cells: [
        { row: "Create order", col: "Check stock", value: 95 },
        { row: "Check stock", col: "Ship order", value: 80 },
        { row: "Check stock", col: "Create order", value: 4 },
        { row: "Ship order", col: "Send invoice", value: 64 },
      ],
    },
    accepts: ["table"],
    fields: [
      { key: "x", label: "X (category)", accepts: CAT, required: true },
      { key: "y", label: "Y (category)", accepts: CAT, required: true },
      { key: "value", label: "Value", accepts: NUM, required: true },
    ],
    options: options({
      show_values: { type: "boolean", title: "Show values" },
      value_format: VALUE_FORMAT_PROP,
      scale: {
        type: "string",
        title: "Colour scale",
        enum: ["linear", "sqrt", "log"],
        enumLabels: ["Linear", "Square root", "Log"],
        default: "linear",
        ui: { widget: "segmented" },
      },
      palette: {
        type: "string",
        title: "Palette",
        description: "Diverging centres on zero (for signed differences).",
        enum: ["sequential", "diverging"],
        enumLabels: ["Sequential", "Diverging"],
        default: "sequential",
        ui: { widget: "segmented" },
      },
      hide_diagonal: { type: "boolean", title: "Hide diagonal" },
      max_cells: {
        type: "integer",
        title: "Max rows/columns",
        minimum: 5,
        maximum: 120,
        step: 5,
        default: 60,
        ui: { widget: "slider" },
      },
      rotate_labels: { type: "boolean", title: "Rotate column labels" },
      legend: LEGEND_PROP,
    }),
    defaults: { w: 4, h: 8, minW: 2, minH: 5 },
    Component: HeatmapViz,
    presets: [
      {
        id: "matrix-similarity",
        title: "Similarity matrix",
        description: "Pairwise similarity with the trivial diagonal hidden.",
        options: { hide_diagonal: true, show_values: true, value_format: "number" },
      },
      {
        id: "matrix-transition",
        title: "Transition matrix",
        description: "From → to probabilities on a square-root colour scale.",
        icon: "ArrowRightLeft",
        options: { scale: "sqrt", value_format: "percent", rotate_labels: true },
      },
    ],
  },
  {
    id: "radar",
    title: "Radar",
    icon: "Radar",
    group: "chart",
    description: "A profile of several measures around a circle.",
    needs: CATEGORY_NEEDS,
    example: CATEGORY_EXAMPLE,
    accepts: ["table", "kpi"],
    fields: CATEGORY_FIELDS,
    defaults: { w: 3, h: 8, minW: 2, minH: 5 },
    Component: RadarViz,
  },
  {
    id: "funnel",
    title: "Funnel",
    icon: "Filter",
    group: "chart",
    description: "Stages sorted by size, to show drop-off.",
    needs: "A table with a stage column and a numeric value, or a kpi dataset (each numeric measure becomes a stage).",
    example: CATEGORY_EXAMPLE,
    accepts: ["table", "kpi"],
    fields: [
      { key: "category", label: "Stage", accepts: CAT, required: true },
      { key: "value", label: "Value", accepts: NUM, required: true },
    ],
    defaults: { w: 3, h: 8, minW: 2, minH: 5 },
    Component: FunnelViz,
  },
  {
    id: "treemap",
    title: "Treemap",
    icon: "LayoutDashboard",
    group: "chart",
    description: "Area-proportional tiles for many categories.",
    needs: CATEGORY_NEEDS,
    example: CATEGORY_EXAMPLE,
    accepts: ["table", "kpi"],
    fields: CATEGORY_FIELDS,
    defaults: { w: 4, h: 8, minW: 2, minH: 5 },
    Component: TreemapViz,
  },
  {
    id: "bubble",
    title: "Bubble",
    icon: "Circle",
    group: "chart",
    description: "Scatter with a third measure as bubble size.",
    needs: "A table with two numeric columns for X and Y and an optional numeric size column.",
    example: {
      activities: [
        { activity: "Create order", count: 120, mean_wait_s: 300, cases: 120 },
        { activity: "Check stock", count: 95, mean_wait_s: 5400, cases: 90 },
        { activity: "Ship order", count: 80, mean_wait_s: 86400, cases: 78 },
      ],
    },
    accepts: ["table"],
    fields: [
      { key: "x", label: "X (numeric)", accepts: NUM, required: true },
      { key: "y", label: "Y (numeric)", accepts: NUM, required: true },
      { key: "size", label: "Size (numeric)", accepts: NUM },
    ],
    defaults: { w: 3, h: 8, minW: 2, minH: 5 },
    Component: BubbleViz,
  },
];
