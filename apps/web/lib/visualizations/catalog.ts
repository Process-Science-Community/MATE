/**
 * The Studio's visualization catalog: every registry viz plus its presets,
 * grouped for the palette, and the helpers that bind a catalog entry to a
 * module dataset or explain what data it still needs.
 *
 * Presets are palette-only starting points (viz_id + options/mapping). A placed
 * card stores nothing about the preset, and the inspector lists base vizzes.
 */

import type { DatasetCatalogEntry, LogModel } from "@/lib/dashboard-queries";
import { vizSpecs } from "./registry";
import type { DatasetShape, FieldMapping, VizGroup, VizSpec } from "./types";

/** Palette groups, in palette order. Labels are fixed UI strings (e2e relies on them). */
export const CATALOG_GROUPS: { id: string; label: string; icon: string; groups: VizGroup[] }[] = [
  { id: "charts", label: "Charts", icon: "BarChart3", groups: ["chart"] },
  { id: "graphs", label: "Process models & graphs", icon: "Workflow", groups: ["graph", "tree"] },
  { id: "matrices", label: "Matrices", icon: "Grid3x3", groups: ["matrix"] },
  { id: "kpis", label: "KPIs", icon: "Gauge", groups: ["kpi"] },
  { id: "tables", label: "Tables & lists", icon: "Table2", groups: ["table"] },
  { id: "text", label: "Text & status", icon: "Type", groups: ["text"] },
];

/** One palette row: a base viz, or one of its presets. */
export interface CatalogEntry {
  /** Unique across the catalog: the viz id, or the preset id. */
  key: string;
  spec: VizSpec;
  title: string;
  description: string;
  icon: string;
  options?: Record<string, unknown>;
  mapping?: FieldMapping;
}

/** Base vizzes in registry order, each followed by its presets. */
export function catalogEntries(): CatalogEntry[] {
  return vizSpecs.flatMap((spec) => [
    { key: spec.id, spec, title: spec.title, description: spec.description ?? "", icon: spec.icon },
    ...(spec.presets ?? []).map((p) => ({
      key: p.id,
      spec,
      title: p.title,
      description: p.description,
      icon: p.icon ?? spec.icon,
      options: p.options,
      mapping: p.mapping,
    })),
  ]);
}

/** This module's datasets a viz accepting `accepts` can render on a
 * `logModel` log, in manifest order, except that a dataset whose id names the
 * entry (`prefer`, e.g. "petri-net" → `petri_net`) comes first. The first one
 * is the auto-binding. */
export function compatibleDatasets(
  accepts: DatasetShape[],
  datasets: DatasetCatalogEntry[] | undefined,
  moduleId: string,
  logModel: LogModel,
  prefer?: string,
): DatasetCatalogEntry[] {
  const named = (d: DatasetCatalogEntry) =>
    !!prefer && d.dataset_id.replace(/_/g, "-").includes(prefer) ? 0 : 1;
  return (datasets ?? [])
    .filter(
      (d) =>
        d.module_id === moduleId && accepts.includes(d.shape) && d.log_models.includes(logModel),
    )
    .sort((a, b) => named(a) - named(b));
}

/** JSON value → Python literal (None/True/False, 4-space indent). */
export function toPython(v: unknown, indent = 0): string {
  const pad = " ".repeat(indent + 4);
  const end = " ".repeat(indent);
  if (v === null || v === undefined) return "None";
  if (v === true) return "True";
  if (v === false) return "False";
  if (typeof v === "number") return Number.isFinite(v) ? String(v) : "None";
  if (typeof v === "string") return JSON.stringify(v);
  if (Array.isArray(v)) {
    if (v.length === 0) return "[]";
    // Short rows of scalars stay on one line (matrix rows, label lists).
    const flat = v.every((x) => x === null || typeof x !== "object");
    const inline = `[${v.map((x) => toPython(x)).join(", ")}]`;
    if (flat && inline.length <= 72) return inline;
    return `[\n${v.map((x) => pad + toPython(x, indent + 4)).join(",\n")},\n${end}]`;
  }
  if (typeof v === "object") {
    const entries = Object.entries(v as Record<string, unknown>);
    if (entries.length === 0) return "{}";
    const inline = `{${entries.map(([k, x]) => `${JSON.stringify(k)}: ${toPython(x)}`).join(", ")}}`;
    if (inline.length <= 72 && !inline.includes("\n")) return inline;
    return `{\n${entries
      .map(([k, x]) => `${pad}${JSON.stringify(k)}: ${toPython(x, indent + 4)}`)
      .join(",\n")},\n${end}}`;
  }
  return "None";
}

/** The two files a module author edits to give an unbound viz card data: a
 * manifest `datasets:` item and a `@route.get` returning the viz's example. */
export function needsDataSnippet(spec: VizSpec): { manifest: string; route: string } {
  const shape = spec.accepts[0];
  const id = `${spec.id.replace(/-/g, "_")}_data`;
  const path = `/${id.replace(/_/g, "-")}`;
  const manifest = [
    `  - id: ${id}`,
    `    title: ${spec.title} data`,
    `    shape: ${shape}`,
    `    route: ${path}`,
  ].join("\n");
  const route = [
    `    @route.get("${path}")`,
    `    async def ${id}(self, ctx: ModuleContext) -> dict[str, Any]:`,
    `        # Replace with data computed from ctx (the event log).`,
    `        return ${toPython(spec.example ?? {}, 8)}`,
  ].join("\n");
  return { manifest, route };
}
