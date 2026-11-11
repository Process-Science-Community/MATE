/** Catalog group "KPIs": registry entries for kpi-shape vizzes.
 * `group: "kpi"` is load-bearing: field-mapping-form shows the duration picker
 * for KPI-group specs. */

import type { WidgetPropSchema } from "@/lib/dashboard-queries";
import type { VizSpec } from "@/lib/visualizations/types";

import { GaugeViz, NumberTileViz, RadialBarViz } from "../charts/recharts-extra";
import { VALUE_FORMAT_PROP } from "../charts/chart-common";
import { KpiGridViz } from "./kpi-grid";

const KPI_EXAMPLE = {
  cases: 1250,
  events: 9840,
  variants: 87,
  fitness: 0.93,
  mean_cycle_time_s: 183600,
};

// Choices come from the bound dataset's measures (field-mapping-form resolves `enumFrom`).
const MEASURE: WidgetPropSchema = {
  type: "string",
  title: "Measure",
  description: "None = the first numeric measure.",
  ui: { enumFrom: "measures" },
};
const MEASURES: WidgetPropSchema = {
  type: "array",
  title: "Measures",
  description: "In display order; none picked = all.",
  ui: { enumFrom: "measures" },
};
const KPI_FORMAT: WidgetPropSchema = {
  ...VALUE_FORMAT_PROP,
  enum: ["auto", ...(VALUE_FORMAT_PROP.enum ?? [])],
  enumLabels: ["Per measure", ...(VALUE_FORMAT_PROP.enumLabels ?? [])],
  default: "auto",
};
const MIN: WidgetPropSchema = { type: "number", title: "Scale min", default: 0 };
const MAX: WidgetPropSchema = {
  type: "number",
  title: "Scale max",
  description: "Empty = 1 for percent measures, else the sum of all measures.",
};

const NEEDS_ONE = "A kpi dataset: an object of named numbers (one measure is shown).";

export const kpiSpecs: VizSpec[] = [
  {
    id: "kpi-grid",
    title: "KPI grid",
    icon: "LayoutGrid",
    group: "kpi",
    description: "Headline numbers as tiles, in a grid or a single strip.",
    needs: "A kpi dataset: an object of named numbers (nested objects are flattened).",
    example: KPI_EXAMPLE,
    accepts: ["kpi"],
    fields: [],
    options: {
      properties: {
        measures: MEASURES,
        layout: {
          type: "string",
          title: "Layout",
          enum: ["grid", "strip"],
          enumLabels: ["Grid", "Strip"],
          default: "grid",
          ui: { widget: "segmented" },
        },
        value_format: KPI_FORMAT,
      },
    },
    defaults: { w: 4, h: 8, minW: 2, minH: 5 },
    Component: KpiGridViz,
    presets: [
      {
        id: "kpi-strip",
        title: "KPI strip",
        description: "Headline numbers in one horizontal row.",
        icon: "GalleryHorizontal",
        options: { layout: "strip" },
      },
    ],
  },
  {
    id: "number-tile",
    title: "Number",
    icon: "Hash",
    group: "kpi",
    description: "One big number, optionally coloured by thresholds.",
    needs: NEEDS_ONE,
    example: KPI_EXAMPLE,
    accepts: ["kpi"],
    fields: [],
    options: {
      properties: {
        measure: MEASURE,
        value_format: KPI_FORMAT,
        decimals: { type: "integer", title: "Decimals", minimum: 0, maximum: 6 },
        prefix: { type: "string", title: "Prefix" },
        bands: {
          type: "string",
          title: "Threshold bands",
          description: "lt:tone:label, comma-separated; tone good|warning|danger|neutral; empty lt = else.",
        },
      },
    },
    defaults: { w: 2, h: 5, minW: 2, minH: 3 },
    Component: NumberTileViz,
    presets: [
      {
        id: "verdict-tile",
        title: "Threshold verdict",
        description: "A p-value style number with a significant / marginal / not significant verdict.",
        icon: "BadgeCheck",
        options: {
          decimals: 3,
          bands: "0.01:good:Significant,0.05:warning:Marginal,:neutral:Not significant",
        },
      },
    ],
  },
  {
    id: "gauge",
    title: "Gauge",
    icon: "Gauge",
    group: "kpi",
    description: "One measure as a ring filled between a min and a max.",
    needs: NEEDS_ONE,
    example: { fitness: 0.93 },
    accepts: ["kpi"],
    fields: [],
    options: { properties: { measure: MEASURE, min: MIN, max: MAX, value_format: KPI_FORMAT } },
    defaults: { w: 3, h: 8, minW: 2, minH: 5 },
    Component: GaugeViz,
  },
  {
    id: "radial-bar",
    title: "Radial bars",
    icon: "CircleDashed",
    group: "kpi",
    description: "Several measures as concentric rings on one scale.",
    needs: "A kpi dataset: an object of named numbers (up to 8 are shown).",
    example: { fitness: 0.93, precision: 0.81, generalization: 0.88, simplicity: 0.7 },
    accepts: ["kpi"],
    fields: [],
    options: {
      properties: {
        measures: MEASURES,
        min: MIN,
        max: { ...MAX, description: "Empty = the largest measure." },
        value_format: KPI_FORMAT,
      },
    },
    defaults: { w: 3, h: 8, minW: 2, minH: 5 },
    Component: RadialBarViz,
  },
];
