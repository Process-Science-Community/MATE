/**
 * The platform-owned generic visualization registry.
 *
 * Each `VizSpec` declares which dataset shape(s) it accepts, its field-mapping
 * (Power-BI-style column binding), optional non-field options (card
 * `config_schema` dialect), default sizing, and the React component. Rendered
 * by dashboard generic-viz cards. Entries live next to their components in
 * `components/visualizations/<group>/specs.ts`; this file concatenates them.
 */

import type { DatasetShape, VizSpec } from "./types";

import { chartSpecs } from "@/components/visualizations/charts/specs";
import { graphSpecs } from "@/components/visualizations/graphs/specs";
import { kpiSpecs } from "@/components/visualizations/kpi/specs";
import { tableSpecs } from "@/components/visualizations/tables/specs";
import { textSpecs } from "@/components/visualizations/text/specs";

/** Every registered viz, in picker order (within one shape the order matches
 * the pre-split registry). */
export const vizSpecs: VizSpec[] = [
  ...tableSpecs,
  ...kpiSpecs,
  ...chartSpecs,
  ...graphSpecs,
  ...textSpecs,
];

export const vizRegistry: Record<string, VizSpec> = Object.fromEntries(
  vizSpecs.map((v) => [v.id, v]),
);

/** The viz auto-selected when a dataset of `shape` is first dropped (needs no
 * field-mapping, so it renders immediately). */
export function defaultVizForShape(shape: DatasetShape): string {
  switch (shape) {
    case "graph":
      return "process-map";
    case "tree":
      return "tree";
    case "kpi":
      return "kpi-grid";
    default:
      return "table";
  }
}

/** Every viz that can render a dataset of `shape` - powers the viz picker. */
export function vizzesForShape(shape: DatasetShape): VizSpec[] {
  return vizSpecs.filter((v) => v.accepts.includes(shape));
}
