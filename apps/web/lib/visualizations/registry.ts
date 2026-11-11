/**
 * The platform-owned generic visualization registry.
 *
 * Each `VizSpec` declares which dataset shape(s) it accepts, its field-mapping
 * (Power-BI-style column binding), optional non-field options (card
 * `config_schema` dialect), default sizing, and the React component. Rendered
 * by dashboard generic-viz cards. Entries live next to their components in
 * `components/visualizations/<group>/specs.ts`; this file concatenates them.
 * Artifact viewers (`acceptsArtifactTypes`) are offered – and picked by
 * default – only for datasets that declare a matching `artifact_type`; untyped
 * datasets keep the shape-based vizzes.
 */

import { artifactTypeId } from "./adapters";
import type { DatasetShape, VizSpec } from "./types";

import { artifactSpecs } from "@/components/visualizations/artifacts/specs";
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
  ...artifactSpecs,
  ...textSpecs,
];

export const vizRegistry: Record<string, VizSpec> = Object.fromEntries(
  vizSpecs.map((v) => [v.id, v]),
);

/** Can `spec` render a dataset of `shape` (typed as `artifactType`)? */
export function vizAccepts(spec: VizSpec, shape: DatasetShape, artifactType?: string | null): boolean {
  if (spec.acceptsArtifactTypes) {
    const type = artifactTypeId(artifactType);
    return type !== null && spec.acceptsArtifactTypes.includes(type);
  }
  return spec.accepts.includes(shape);
}

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

/** Every untyped viz that can render a dataset of `shape`. */
export function vizzesForShape(shape: DatasetShape): VizSpec[] {
  return vizzesFor(shape);
}

/** The viz auto-selected for a dropped dataset: its artifact viewer when the
 * dataset is typed and one exists, else the shape default. */
export function defaultVizFor(shape: DatasetShape, artifactType?: string | null): string {
  const type = artifactTypeId(artifactType);
  const viewer = type && vizSpecs.find((v) => v.acceptsArtifactTypes?.includes(type));
  return viewer ? viewer.id : defaultVizForShape(shape);
}

/** Every viz that can render a dataset - powers the viz picker. */
export function vizzesFor(shape: DatasetShape, artifactType?: string | null): VizSpec[] {
  return vizSpecs.filter((v) => vizAccepts(v, shape, artifactType));
}
