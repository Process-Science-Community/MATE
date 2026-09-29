import { directedDistances } from "./geodesic";

type Edge = { source: string; target: string };
/** Deterministic force layouts. Directions are retained in rendering, not placement. */
export function networkLayout(ids: string[], edges: Edge[], method: "kk" | "spring") {
  const n = ids.length;
  if (n < 2) return new Map(ids.map(id => [id, { x: 440, y: 270 }]));
  const index = new Map(ids.map((id, i) => [id, i]));
  const links = edges.map(e => [index.get(e.source)!, index.get(e.target)!]);
  const distances = method === "kk" ? directedDistances(ids, edges.flatMap(e => [e, { source: e.target, target: e.source }])) : [];
  const diameter = distances.reduce((max, row) => row.reduce((m, d) => Number.isFinite(d) ? Math.max(m, d) : m, max), 1);
  const points = ids.map((_, i) => ({ x: Math.cos(i * 2 * Math.PI / n), y: Math.sin(i * 2 * Math.PI / n) }));
  const k = Math.sqrt(4 / n);
  // Bounded iterations keep interaction practical up to the module's 800-state limit.
  for (let iteration = 0; iteration < 160; iteration++) {
    const force = points.map(() => ({ x: 0, y: 0 }));
    for (let i = 0; i < n; i++) for (let j = i + 1; j < n; j++) {
      const dx = points[i].x - points[j].x, dy = points[i].y - points[j].y;
      const length = Math.max(1e-9, Math.hypot(dx, dy));
      // KK stress: spring rest lengths proportional to shortest paths, stiffness 1/d².
      const d = method === "kk" ? (Number.isFinite(distances[i][j]) ? distances[i][j] : diameter * 2) : 1;
      const magnitude = method === "kk" ? (2 * d / diameter - length) / (d * d) : k * k / length;
      const fx = dx / length * magnitude, fy = dy / length * magnitude;
      force[i].x += fx; force[i].y += fy; force[j].x -= fx; force[j].y -= fy;
    }
    if (method === "spring") for (const [i, j] of links) {
      const dx = points[i].x - points[j].x, dy = points[i].y - points[j].y;
      const length = Math.hypot(dx, dy);
      const fx = dx * length / k, fy = dy * length / k;
      force[i].x -= fx; force[i].y -= fy; force[j].x += fx; force[j].y += fy;
    }
    const temperature = .12 * (1 - iteration / 160);
    points.forEach((p, i) => {
      const length = Math.max(1e-9, Math.hypot(force[i].x, force[i].y));
      const step = Math.min(temperature, length * .1);
      p.x += force[i].x / length * step; p.y += force[i].y / length * step;
    });
  }
  const xs = points.map(p => p.x), ys = points.map(p => p.y);
  const minX = Math.min(...xs), maxX = Math.max(...xs), minY = Math.min(...ys), maxY = Math.max(...ys);
  const scale = Math.min(720 / Math.max(maxX - minX, 1e-9), 400 / Math.max(maxY - minY, 1e-9));
  return new Map(ids.map((id, i) => [id, { x: 440 + (points[i].x - (minX + maxX) / 2) * scale, y: 270 + (points[i].y - (minY + maxY) / 2) * scale }]));
}
