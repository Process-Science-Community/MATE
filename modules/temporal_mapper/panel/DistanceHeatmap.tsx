"use client";

import { useEffect, useMemo, useRef, useState } from "react";
import { Select, SelectContent, SelectItem, SelectTrigger, SelectValue } from "@/components/ui/select";
import { periodDistances } from "./geodesic";

export default function DistanceHeatmap({ ids, nodes, edges, timeColor }: { ids: string[]; nodes: { id: string; members: string[] }[]; edges: { source: string; target: string }[]; timeColor: (fraction: number) => string }) {
  const canvas = useRef<HTMLCanvasElement>(null);
  const distances = useMemo(() => periodDistances(ids, nodes, edges), [ids, nodes, edges]);
  const [source, setSource] = useState(0);
  const [target, setTarget] = useState(0);
  const max = distances.reduce((m, row) => row.reduce((n, d) => Number.isFinite(d) ? Math.max(n, d) : n, m), 0);
  const cell = Math.max(1, Math.min(36, Math.floor(640 / ids.length)));
  const margin = 100, size = ids.length * cell;
  const color = (d: number) => Number.isFinite(d) ? timeColor(d / Math.max(1, max)) : "#9ca3af";
  useEffect(() => {
    const ctx = canvas.current?.getContext("2d");
    if (!ctx) return;
    ctx.clearRect(0, 0, size + margin + 10, size + margin + 10);
    ctx.fillStyle = "#ffffff";
    ctx.fillRect(0, 0, size + margin + 10, size + margin + 10);
    distances.forEach((row, i) => row.forEach((d, j) => {
      ctx.fillStyle = Number.isFinite(d) ? timeColor(d / Math.max(1, max)) : "#9ca3af";
      ctx.fillRect(margin + j * cell, margin + i * cell, cell, cell);
      if (cell >= 24) {
        ctx.fillStyle = "white";
        ctx.strokeStyle = "#111827"; ctx.lineWidth = 3; ctx.lineJoin = "round";
        ctx.font = "11px sans-serif"; ctx.textAlign = "center";
        ctx.strokeText(Number.isFinite(d) ? String(d) : "∞", margin + (j + .5) * cell, margin + (i + .5) * cell + 4);
        ctx.fillText(Number.isFinite(d) ? String(d) : "∞", margin + (j + .5) * cell, margin + (i + .5) * cell + 4);
      }
    }));
    ctx.fillStyle = "#111827"; ctx.font = "11px sans-serif";
    const stride = Math.max(1, Math.ceil(24 / cell));
    ids.forEach((id, i) => {
      if (i % stride !== 0 && i !== ids.length - 1) return;
      ctx.textAlign = "right"; ctx.fillText(id, margin - 8, margin + (i + .5) * cell + 4);
      ctx.save(); ctx.translate(margin + (i + .5) * cell, margin - 8); ctx.rotate(-Math.PI / 2); ctx.textAlign = "left"; ctx.fillText(id, 0, 0); ctx.restore();
    });
  }, [distances, ids, cell, size, max, timeColor]);
  return <section className="space-y-5 rounded-xl border bg-card p-5 shadow-sm">
    <div className="space-y-1"><h3 className="font-semibold tracking-tight">Directed time-to-time geodesic distances</h3>
    <p className="text-sm leading-relaxed text-muted-foreground">Each cell shows the shortest directed path between the states containing two time periods, measured in arrows. Periods in the same state have distance 0; gray (∞) means no directed path exists.</p>
    <p className="text-xs text-muted-foreground">Destination period → · Source period ↓ · Dates are period starts (UTC), ordered chronologically. Periods with no recorded patterns are omitted.</p></div>
    <div className="overflow-auto rounded-lg border bg-background p-2"><canvas ref={canvas} width={size + margin + 10} height={size + margin + 10} role="img" aria-label="Directed time-to-time distance heatmap. Use the source and destination selectors below to inspect any value." onMouseMove={e => {
      const rect = e.currentTarget.getBoundingClientRect();
      const col = Math.floor(((e.clientX - rect.left) * e.currentTarget.width / rect.width - margin) / cell);
      const row = Math.floor(((e.clientY - rect.top) * e.currentTarget.height / rect.height - margin) / cell);
      if (row >= 0 && col >= 0 && row < ids.length && col < ids.length) { setSource(row); setTarget(col); }
    }}/></div>
    <div className="flex flex-wrap items-center gap-3 text-xs text-muted-foreground"><span>0</span><div className="h-3 min-w-40 flex-1 rounded-full" style={{ background: `linear-gradient(to right, ${Array.from({ length: 21 }, (_, i) => `${color(max * i / 20)} ${i * 5}%`).join(", ")})` }}/><span>{max} arrows</span><span className="inline-block h-3 w-5 rounded-sm bg-gray-400"/><span>∞ Unreachable</span></div>
    <div className="grid gap-4 rounded-lg border bg-muted/20 p-4 sm:grid-cols-2">
      <label className="grid gap-2 text-sm"><span className="font-medium">Source period</span><Select value={String(source)} onValueChange={value => setSource(Number(value))}><SelectTrigger><SelectValue /></SelectTrigger><SelectContent>{ids.map((id, i) => <SelectItem key={id} value={String(i)}>{id}</SelectItem>)}</SelectContent></Select></label>
      <label className="grid gap-2 text-sm"><span className="font-medium">Destination period</span><Select value={String(target)} onValueChange={value => setTarget(Number(value))}><SelectTrigger><SelectValue /></SelectTrigger><SelectContent>{ids.map((id, i) => <SelectItem key={id} value={String(i)}>{id}</SelectItem>)}</SelectContent></Select></label>
      <output className="sm:col-span-2 rounded-md bg-background px-3 py-2 text-sm font-medium tabular-nums">{ids[source]} → {ids[target]}: {Number.isFinite(distances[source][target]) ? `${distances[source][target]} arrows` : "∞ (unreachable)"}</output>
    </div>
  </section>;
}
