import type { DashboardItem } from "@/lib/dashboard-queries";

/**
 * JSON, but with exponent numbers PyYAML (YAML 1.1) still reads as floats: it
 * needs a dot in the mantissa, so `1e-7` / `1e+21` become `1.0e-7` / `1.0e+21`.
 * String tokens are matched first and left untouched.
 */
function yamlFlow(value: unknown): string {
  return JSON.stringify(value).replace(/"(?:[^"\\]|\\.)*"|-?\d+(?:\.\d+)?e[+-]\d+/g, (m) =>
    m.startsWith('"') || m.includes(".") ? m : m.replace("e", ".0e"),
  );
}

/**
 * Serialize a module board's current layout as the manifest snippet that seeds
 * it (`frontend.board.items`, see modules/README.md). Dev aid: arrange the board
 * in the UI, copy, paste into `manifest.yaml`.
 *
 * Only viz cards on one of the module's own datasets with a chosen visualization
 * are expressible in a manifest; everything else is counted in `skipped`.
 * Values are emitted as JSON, which is valid YAML flow syntax.
 */
export function boardManifestYaml(
  items: readonly DashboardItem[],
  moduleId: string,
): { yaml: string; skipped: number } {
  const lines = ["frontend:", "  board:", "    items:"];
  let skipped = 0;
  for (const it of items) {
    const datasetId = it.dataset_ref?.module_id === moduleId ? it.dataset_ref.dataset_id : null;
    if (it.kind !== "viz" || !datasetId || !it.viz_id) {
      skipped++;
      continue;
    }
    lines.push(`      - id: ${JSON.stringify(it.i)}`);
    lines.push(`        viz: ${JSON.stringify(it.viz_id)}`);
    lines.push(`        dataset: ${JSON.stringify(datasetId)}`);
    if (it.title) lines.push(`        title: ${JSON.stringify(it.title)}`);
    if (it.mapping && Object.keys(it.mapping).length > 0) {
      lines.push(`        mapping: ${yamlFlow(it.mapping)}`);
    }
    if (Object.keys(it.config).length > 0) {
      lines.push(`        options: ${yamlFlow(it.config)}`);
    }
    lines.push(`        x: ${it.x}`, `        y: ${it.y}`, `        w: ${it.w}`, `        h: ${it.h}`);
  }
  if (lines.length === 3) lines[2] = "    items: []";
  return { yaml: `${lines.join("\n")}\n`, skipped };
}
