/** Directed, unweighted shortest paths. Infinity denotes unreachable destinations. */
export function directedDistances(ids: string[], edges: { source: string; target: string }[]) {
  const indices = new Map(ids.map((id, i) => [id, i]));
  const neighbors: number[][] = ids.map(() => []);
  for (const edge of edges) {
    const source = indices.get(edge.source), target = indices.get(edge.target);
    if (source !== undefined && target !== undefined) neighbors[source].push(target);
  }
  return ids.map((_, source) => {
    const distances = Array<number>(ids.length).fill(Infinity);
    distances[source] = 0;
    const queue = [source];
    for (let head = 0; head < queue.length; head++) {
      const current = queue[head];
      for (const next of neighbors[current]) {
        if (distances[next] !== Infinity) continue;
        distances[next] = distances[current] + 1;
        queue.push(next);
      }
    }
    return distances;
  });
}

/** Expand state distances onto chronologically ordered occupied periods. */
export function periodDistances(periods: string[], nodes: { id: string; members: string[] }[], edges: { source: string; target: string }[]) {
  const distances = directedDistances(nodes.map(node => node.id), edges);
  const membership = new Map(nodes.flatMap((node, i) => node.members.map(period => [period, i] as const)));
  return periods.map(source => periods.map(target => {
    const i = membership.get(source), j = membership.get(target);
    if (i === undefined || j === undefined) throw new Error("A period is missing its state membership.");
    return distances[i][j];
  }));
}
