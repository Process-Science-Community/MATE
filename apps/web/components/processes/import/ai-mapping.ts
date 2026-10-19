import type { ImportColumnMappingResponse } from "@/lib/ai-guidance";
import type { LogProbeResponse } from "@/lib/api-types";
import type { MappingState } from "@/components/processes/import/mapping-step";
import { isLowConfidence, REQUIRED_ROLES } from "@/components/processes/import/role-card";

/** What the AI column-mapping endpoint gets to see: the probe's headers and its
 *  sampled values, re-assembled into rows. */
export function aiMappingRequest(probe: LogProbeResponse): {
  headers: string[];
  sample_rows: string[][];
} {
  const headers = probe.columns.map((c) => c.name);
  const depth = Math.max(...probe.columns.map((c) => c.samples.length), 0);
  const sampleRows = Array.from({ length: depth }, (_, row) =>
    probe.columns.map((c) => c.samples[row] ?? ""),
  );
  return { headers, sample_rows: sampleRows };
}

/**
 * Folds AI suggestions into the mapping on screen. Only fills required roles
 * the resolver was unsure about in `base` (the probe's own mapping), and only
 * with a column that exists – never overwrites an exact match. Returns the
 * roles it changed.
 */
export function mergeAiSuggestions(
  current: MappingState,
  base: MappingState,
  headers: string[],
  suggestions: ImportColumnMappingResponse["suggestions"],
): { mapping: MappingState; filled: Set<string> } {
  const roles = { ...current.roles };
  const quality = { ...current.quality };
  const filled = new Set<string>();
  for (const role of REQUIRED_ROLES) {
    const suggestion = suggestions[role];
    if (!suggestion || !headers.includes(suggestion)) continue;
    if (!isLowConfidence(base.quality[role])) continue;
    if (roles[role] === suggestion) continue;
    roles[role] = suggestion;
    delete quality[role];
    filled.add(role);
  }
  return { mapping: filled.size > 0 ? { ...current, roles, quality } : current, filled };
}
