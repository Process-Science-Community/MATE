import type { GraphData } from "@/lib/visualizations/types";

/**
 * Celonis-style rank filter for a process map, generalized from
 * modules/discovery/panel/dfg-filter.ts:
 *   1. keep the top `nodeShare` (0..1) of nodes by value;
 *   2. drop edges touching a hidden node (and self-loops if asked);
 *   3. keep the top `edgeShare` of the remaining edges by value, plus a
 *      spanning forest of the most frequent edges so no kept node floats.
 */
export function filterGraph(
  g: GraphData,
  nodeShare: number,
  edgeShare: number,
  hideSelfLoops: boolean,
): GraphData {
  const byValue = [...g.nodes].sort((a, b) => (b.value ?? 0) - (a.value ?? 0));
  const nodeCount = byValue.length ? Math.max(1, Math.ceil(byValue.length * clamp01(nodeShare))) : 0;
  const nodes = byValue.slice(0, nodeCount);
  const keep = new Set(nodes.map((n) => n.id));

  const candidates = g.edges
    .filter((e) => keep.has(e.source) && keep.has(e.target))
    .filter((e) => !(hideSelfLoops && e.source === e.target))
    .sort((a, b) => (b.value ?? 0) - (a.value ?? 0));

  // Kruskal over frequency-sorted edges: the most frequent edges that connect
  // otherwise separate components always stay.
  const parent = new Map<string, string>();
  const find = (x: string): string => {
    const p = parent.get(x) ?? x;
    if (p === x) return x;
    const r = find(p);
    parent.set(x, r);
    return r;
  };
  const spanning = new Set<string>();
  for (const e of candidates) {
    if (e.source === e.target) continue;
    const a = find(e.source);
    const b = find(e.target);
    if (a !== b) {
      parent.set(a, b);
      spanning.add(e.id);
    }
  }
  const top = Math.ceil(candidates.length * clamp01(edgeShare));
  const edges = candidates.filter((e, i) => i < top || spanning.has(e.id));

  // Keep the original node order so the layout stays stable across filters.
  return { ...g, nodes: g.nodes.filter((n) => keep.has(n.id)), edges };
}

function clamp01(v: number): number {
  return Number.isFinite(v) ? Math.min(1, Math.max(0, v)) : 1;
}
