"use client";

import { useMemo, useRef, useState } from "react";
import { CanvasControlCluster, CanvasSettings, CanvasSettingsSwitch } from "@/components/visualizations/canvases/shared/canvas-toolbar";
import { networkLayout } from "./layouts";
import UserGuide from "./UserGuide";
import DistanceHeatmap from "./DistanceHeatmap";
import { api } from "@/lib/api";

type StateNode = { id: string; size: number; time: number; members: string[]; patterns: { name: string; count: number }[] };
type Result = { nodes: StateNode[]; edges: { source: string; target: string }[]; periods: string[]; n_features: number; n_gaps: number; dropped_events: number; pca: { components: number; retained_variance_percent: number; note: string | null } | null };

export default function TemporalMapperPanel({ logId }: { logId: string; moduleId: string }) {
  const [fullscreen, setFullscreen] = useState(false);
  const [spacing, setSpacing] = useState(3);
  const [showIds, setShowIds] = useState(false);
  const [zoom, setZoom] = useState(0.39);
  const [pan, setPan] = useState({ x: 0, y: 0 });
  const [manualPositions, setManualPositions] = useState<Record<string, { x: number; y: number }>>({});
  const gesture = useRef<{ id: string | null; x: number; y: number; originX: number; originY: number } | null>(null);
  function resetView() { setZoom(0.39); setPan({ x: 0, y: 0 }); setManualPositions({}); }
  const [showDistances, setShowDistances] = useState(false);
  const [minRadius, setMinRadius] = useState(7);
  const [arrowSize, setArrowSize] = useState(9);
  const [maxRadius, setMaxRadius] = useState(20);
  const [layout, setLayout] = useState("spring");
  const [palette, setPalette] = useState("original");
  const [unit, setUnit] = useState("day");
  const [similarity, setSimilarity] = useState("0.85");
  const [method, setMethod] = useState("hclust");
  const [mode, setMode] = useState("consecutive");
  const [pcaEnabled, setPcaEnabled] = useState(false);
  const [pcaVariance, setPcaVariance] = useState("95");
  const [loop, setLoop] = useState("2");
  const [result, setResult] = useState<Result | null>(null);
  const [selected, setSelected] = useState<StateNode | null>(null);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  const field = "rounded-md border bg-background px-3 py-2 text-sm";
  async function run() {
    resetView(); setShowDistances(false); setBusy(true); setError(""); setResult(null); setSelected(null);
    try {
      const params = new URLSearchParams({ log_id: logId, time_unit: unit, similarity_threshold: similarity, method, temporal_edges: mode, loop_size: loop, pca_enabled: String(pcaEnabled), pca_variance: pcaEnabled ? pcaVariance : "95" });
      const data = await api<Result>(`/api/v1/modules/temporal_mapper/graph?${params}`);
      setResult(data); setSelected(data.nodes[0] ?? null);
    } catch (e) { setError(e instanceof Error ? e.message : String(e)); }
    finally { setBusy(false); }
  }
  const firstTime = result?.periods.length ? Date.parse(result.periods[0]) : 0;
  const lastTime = result?.periods.length ? Date.parse(result.periods[result.periods.length - 1]) : 0;
  const meanDate = (node: StateNode) => node.members.reduce((sum, date) => sum + Date.parse(date), 0) / node.members.length;
  const orderedNodes = [...(result?.nodes ?? [])].sort((a, b) => meanDate(a) - meanDate(b) || Number(a.id) - Number(b.id));
  const networkPositions = useMemo(() => result && (layout === "kk" || layout === "spring") ? networkLayout(result.nodes.map(n => n.id), result.edges, layout) : null, [result, layout]);
  const basePositions = networkPositions ?? new Map(orderedNodes.map((node, i, nodes) => {
    if (nodes.length === 1) return [node.id, { x: 440, y: 270 }] as const;
    if (layout === "grid") {
      const columns = Math.ceil(Math.sqrt(nodes.length * 1.5));
      const rows = Math.ceil(nodes.length / columns);
      return [node.id, { x: 80 + (i % columns) * 720 / Math.max(1, columns - 1), y: rows === 1 ? 270 : 70 + Math.floor(i / columns) * 400 / (rows - 1) }] as const;
    }
    if (layout === "timeline") {
      return [node.id, { x: lastTime === firstTime ? 440 : 80 + 720 * (meanDate(node) - firstTime) / (lastTime - firstTime), y: 100 + (i % 5) * 80 }] as const;
    }
    const angle = (2 * Math.PI * i) / nodes.length - Math.PI / 2;
    return [node.id, { x: 440 + 310 * Math.cos(angle), y: 270 + 205 * Math.sin(angle) }] as const;
  }));
  const positions = new Map([...basePositions].map(([id, position]) => [id, manualPositions[id] ?? { x: 440 + (position.x - 440) * spacing, y: 270 + (position.y - 270) * spacing }]));
  function fitView() {
    const points = [...positions.values()];
    if (!points.length) return;
    const left = Math.min(...points.map(p => p.x)) - maxRadius - 15;
    const right = Math.max(...points.map(p => p.x)) + maxRadius + 15;
    const top = Math.min(...points.map(p => p.y)) - maxRadius - 15;
    const bottom = Math.max(...points.map(p => p.y)) + maxRadius + 15;
    const scale = Math.min(1, 880 / (right - left), 540 / (bottom - top));
    setZoom(scale);
    setPan({ x: (440 - (left + right) / 2) * scale, y: (270 - (top + bottom) / 2) * scale });
  }
  const sizes = result?.nodes.map(node => node.size) ?? [1];
  const smallest = Math.min(...sizes), largest = Math.max(...sizes);
  const nodeRadius = (node: StateNode) => {
    const fraction = largest === smallest ? 0 : (node.size - smallest) / (largest - smallest);
    return Math.sqrt(minRadius ** 2 + fraction * (maxRadius ** 2 - minRadius ** 2));
  };
  const palettes: Record<string, string[]> = {
    viridis: ["#440154", "#3b528b", "#21918c", "#5ec962", "#fde725"],
    plasma: ["#0d0887", "#7e03a8", "#cc4778", "#f89540", "#f0f921"],
    blues: ["#deebf7", "#9ecae1", "#4292c6", "#08519c", "#08306b"],
  };
  const timeColor = (fraction: number) => {
    const t = Math.max(0, Math.min(1, fraction));
    if (palette === "original") return `hsl(${225 - 190 * t} 65% 45%)`;
    const stops = palettes[palette];
    const index = Math.min(stops.length - 2, Math.floor(t * (stops.length - 1)));
    const weight = t * (stops.length - 1) - index;
    const channels = [1, 3, 5].map(offset => {
      const start = parseInt(stops[index].slice(offset, offset + 2), 16);
      const end = parseInt(stops[index + 1].slice(offset, offset + 2), 16);
      return Math.round(start + weight * (end - start));
    });
    return `rgb(${channels.join(", ")})`;
  };
  const nodeColor = (node: StateNode) => {
    const meanTime = node.members.reduce((sum, date) => sum + Date.parse(date), 0) / node.members.length;
    return timeColor(lastTime === firstTime ? 0 : (meanTime - firstTime) / (lastTime - firstTime));
  };
  const colorStops = Array.from({ length: 21 }, (_, i) => `${timeColor(lastTime === firstTime ? 0 : i / 20)} ${i * 5}%`).join(", ");
  const dateLabel = (fraction: number) => new Date(firstTime + fraction * (lastTime - firstTime)).toISOString().slice(0, 10);
  function clear() { setShowDistances(false); setResult(null); setSelected(null); }
  return <div className="space-y-5">
    <div><div className="flex items-center justify-between gap-3"><h2 className="text-xl font-semibold">Temporal Mapper</h2><UserGuide/></div><p className="mt-2 text-sm text-muted-foreground">Find recurring activity patterns across calendar periods. Each state contains one or more periods; arrows retain connections from the temporal and similarity graph.</p></div>
    <form className="flex flex-wrap items-start gap-4 rounded-xl border p-4" onSubmit={e => { e.preventDefault(); void run(); }}>
      <label className="grid gap-2 text-sm">Time unit<select className={field} value={unit} disabled={busy} onChange={e => { setUnit(e.target.value); clear(); }}>{["day", "week", "month", "year"].map(v => <option key={v}>{v}</option>)}</select></label>
      <label className="grid gap-2 text-sm">Similarity<input className={field + " w-28"} type="number" min="0" max="1" step="0.01" required value={similarity} disabled={busy} onChange={e => { setSimilarity(e.target.value); clear(); }}/></label>
      <label className="grid gap-2 text-sm">Clustering<select className={field} value={method} disabled={busy} onChange={e => { setMethod(e.target.value); clear(); }}><option value="hclust">Average linkage</option><option value="heuristic">Pairwise threshold</option></select></label>
      <div className="grid gap-2"><label className="grid gap-2 text-sm">Add temporal links across gaps?<select className={field} value={mode} disabled={busy} aria-describedby="tm-gap-help" onChange={e => { setMode(e.target.value); clear(); }}><option value="consecutive">Yes</option><option value="adjacent">No</option></select></label><p id="tm-gap-help" className="text-xs text-muted-foreground">A gap is a {unit} with no recorded patterns.</p></div>
      <label className="grid gap-2 text-sm">Loop size<input className={field + " w-24"} type="number" min="1" max="20" required value={loop} disabled={busy} onChange={e => { setLoop(e.target.value); clear(); }}/></label>
      <label className="mt-7 flex items-center gap-2 py-2 text-sm"><input type="checkbox" checked={pcaEnabled} disabled={busy} onChange={e => { setPcaEnabled(e.target.checked); clear(); }}/>Enable PCA</label>
      {pcaEnabled && <label className="grid gap-2 text-sm">Retained variance (%)<input className={field + " w-28"} type="number" min="0" max="100" step="0.1" required value={pcaVariance} disabled={busy} onChange={e => { setPcaVariance(e.target.value); clear(); }}/></label>}
      <button type="submit" disabled={busy} className="mt-7 rounded-md bg-primary px-5 py-2 text-primary-foreground disabled:opacity-50">{busy ? "Building…" : "Build map"}</button>
    </form>
    <p className="text-xs text-muted-foreground">Activity and transition counts are normalized separately before analysis. <a href="https://doi.org/10.1162/netn_a_00301" target="_blank" rel="noopener noreferrer" className="underline">Temporal Mapper: Zhang, Chowdhury &amp; Saggar (2023)</a>. See “How to use” for details.</p>
    {error && <div role="alert" className="rounded-lg border border-red-400 p-4 text-red-600">{error}</div>}
    {!result && !busy && !error && <div className="rounded-xl border border-dashed p-12 text-center text-muted-foreground">Choose your parameters and build a map from this event log.</div>}
    {result && <>
      <div className="flex flex-wrap gap-6 text-sm"><strong>{result.nodes.length} states</strong><span>{result.edges.length} arrows</span><span>{result.periods.length} occupied periods</span><span>{result.n_features} features</span><span>{result.n_gaps} calendar gaps</span></div>
      {result.pca && <p role="status" className="text-sm">PCA: {result.pca.components} components · {result.pca.retained_variance_percent.toFixed(2)}% variance retained{result.pca.note ? ` · ${result.pca.note}` : ""}</p>}
      {result.dropped_events > 0 && <p role="status">Excluded {result.dropped_events} invalid events.</p>}
      <div className="flex flex-wrap items-end gap-4">
        <label className="grid gap-2 text-sm">Layout<select className={field} value={layout} onChange={e => { setLayout(e.target.value); resetView(); }}><option value="circular">Circular</option><option value="grid">Grid</option><option value="timeline">Timeline</option><option value="kk">Kamada–Kawai (KK)</option><option value="spring">Spring (Fruchterman–Reingold)</option></select></label>
        <label className="grid gap-2 text-sm">Color palette<select className={field} value={palette} onChange={e => setPalette(e.target.value)}><option value="original">Blue–orange (original)</option><option value="viridis">Viridis</option><option value="plasma">Plasma</option><option value="blues">Blues</option></select></label>
      </div>
      <fieldset className="flex flex-wrap items-center gap-5 rounded-lg border p-3">
        <legend className="px-1 text-sm font-medium">Node size, spacing, and arrows</legend>
        <label className="grid gap-1 text-sm">Layout spacing: {spacing.toFixed(1)}×<input type="range" min="1" max="3" step="0.1" value={spacing} onChange={e => { setSpacing(Number(e.target.value)); setManualPositions({}); }}/></label>
        <label className="grid gap-1 text-sm">Minimum radius: {minRadius}<input type="range" min="3" max="48" step="1" value={minRadius} onChange={e => { const value = Number(e.target.value); setMinRadius(value); setMaxRadius(current => Math.max(current, value)); }}/></label>
        <label className="grid gap-1 text-sm">Maximum radius: {maxRadius}<input type="range" min="3" max="48" step="1" value={maxRadius} onChange={e => { const value = Number(e.target.value); setMaxRadius(value); setMinRadius(current => Math.min(current, value)); }}/></label>
        <label className="grid gap-1 text-sm">Arrow size: {arrowSize}<input type="range" min="2" max="20" step="1" value={arrowSize} onChange={e => setArrowSize(Number(e.target.value))}/></label>
        <button type="button" className={field} onClick={() => { setMinRadius(7); setMaxRadius(20); }}>Reset sizes</button>
        <p className="w-full text-xs text-muted-foreground">Sizes span {smallest}–{largest} member periods. Circle area scales between the selected limits. If all states have the same count, all use the minimum size.</p>
      </fieldset>
      <div className="grid gap-4 lg:grid-cols-[2fr_1fr]">
        <div className={fullscreen ? "fixed inset-0 z-50 overflow-auto bg-background p-4" : "relative overflow-auto rounded-xl border bg-background"}>
          <CanvasControlCluster
            onZoomIn={() => setZoom(z => Math.min(4, z * 1.25))}
            onZoomOut={() => setZoom(z => Math.max(.25, z / 1.25))}
            onFit={fitView}
            onReset={resetView}
            isFullscreen={fullscreen}
            onToggleFullscreen={() => setFullscreen(value => !value)}
            settings={<CanvasSettings><CanvasSettingsSwitch label="Show state IDs" checked={showIds} onChange={setShowIds}/></CanvasSettings>}
          />
          <p className="px-4 pt-14 text-xs text-muted-foreground">Drag nodes to move them; drag the background to pan. Zoom: {Math.round(zoom * 100)}%.</p>
          <svg style={{ touchAction: "none", cursor: "grab" }}
            onPointerDown={e => {
              if (e.button !== 0) return;
              const svg = e.currentTarget;
              const point = new DOMPoint(e.clientX, e.clientY).matrixTransform(svg.getScreenCTM()!.inverse());
              const nodeId = (e.target as Element).closest("[data-node-id]")?.getAttribute("data-node-id") ?? null;
              const origin = nodeId ? positions.get(nodeId)! : pan;
              gesture.current = { id: nodeId, x: point.x, y: point.y, originX: origin.x, originY: origin.y };
              svg.setPointerCapture(e.pointerId);
            }}
            onPointerMove={e => {
              const current = gesture.current;
              if (!current) return;
              const point = new DOMPoint(e.clientX, e.clientY).matrixTransform(e.currentTarget.getScreenCTM()!.inverse());
              const dx = point.x - current.x, dy = point.y - current.y;
              if (current.id) setManualPositions(previous => ({ ...previous, [current.id!]: { x: current.originX + dx / zoom, y: current.originY + dy / zoom } }));
              else setPan({ x: current.originX + dx, y: current.originY + dy });
            }}
            onPointerUp={e => {
              const current = gesture.current;
              const point = new DOMPoint(e.clientX, e.clientY).matrixTransform(e.currentTarget.getScreenCTM()!.inverse());
              if (current?.id && Math.hypot(point.x - current.x, point.y - current.y) < 3) setSelected(result.nodes.find(node => node.id === current.id) ?? null);
              gesture.current = null; e.currentTarget.releasePointerCapture(e.pointerId);
            }}
            onPointerCancel={() => { gesture.current = null; }}
            viewBox="0 0 880 540" className="min-w-[560px] w-full" role="img" aria-label="Temporal Mapper state graph. Select a state to inspect its periods.">
            <defs><marker id="tm-arrow" viewBox="0 0 10 10" refX="9" refY="5" markerWidth={arrowSize} markerHeight={arrowSize} orient="auto-start-reverse"><path d="M 0 0 L 10 5 L 0 10 z" fill="#94a3b8"/></marker></defs>
            <g transform={`translate(${pan.x} ${pan.y}) translate(440 270) scale(${zoom}) translate(-440 -270)`}>
            {layout === "timeline" && <g><line x1="80" y1="490" x2="800" y2="490" stroke="#94a3b8"/><text x="80" y="515" fontSize="12" fill="currentColor">{dateLabel(0)}</text><text x="800" y="515" textAnchor="end" fontSize="12" fill="currentColor">{dateLabel(1)}</text></g>}
            {result.edges.map(edge => {
              const a = positions.get(edge.source)!; const b = positions.get(edge.target)!;
              const dx = b.x - a.x, dy = b.y - a.y, len = Math.hypot(dx, dy) || 1;
              const radius = nodeRadius(result.nodes.find(n => n.id === edge.target)!);
              const end = { x: b.x - dx / len * (radius + 5), y: b.y - dy / len * (radius + 5) };
              return <path key={`${edge.source}-${edge.target}`} d={`M ${a.x} ${a.y} Q ${(a.x + b.x) / 2 - dy * .13} ${(a.y + b.y) / 2 + dx * .13} ${end.x} ${end.y}`} fill="none" stroke="#94a3b8" strokeWidth="1.5" markerEnd="url(#tm-arrow)"/>;
            })}
            {result.nodes.map(node => { const p = positions.get(node.id)!; return <g key={node.id} data-node-id={node.id} role="button" tabIndex={0} aria-label={`State ${node.id}, ${node.size} periods`} onClick={() => setSelected(node)} onKeyDown={e => { if (e.key === "Enter" || e.key === " ") { e.preventDefault(); setSelected(node); } }} style={{ cursor: "pointer" }}>
              <circle cx={p.x} cy={p.y} r={nodeRadius(node)} fill={nodeColor(node)} stroke={selected?.id === node.id ? "#f59e0b" : "white"} strokeWidth={selected?.id === node.id ? 4 : 2}/>{showIds && <text x={p.x} y={p.y + 4} textAnchor="middle" fill="white" stroke="#111827" strokeWidth="3" paintOrder="stroke" fontSize="12">{node.id}</text>}<title>{node.members.join(", ")}</title>
            </g>; })}
            </g>
          </svg>
          <div className="px-6 pb-5" aria-label="Time color scale">
            <p className="mb-2 text-xs font-medium">Mean date of member periods (UTC)</p>
            <div role="img" aria-label={`Time colors from ${dateLabel(0)} to ${dateLabel(1)}`} className="h-3 w-full rounded" style={{ background: `linear-gradient(to right, ${colorStops})` }} />
            <div className="mt-2 flex justify-between gap-2 text-xs tabular-nums text-muted-foreground">
              <span>{dateLabel(0)}</span>
              {lastTime !== firstTime && <><span>{dateLabel(0.5)}</span><span>{dateLabel(1)}</span></>}
            </div>
          </div>
          <p className="px-4 pb-4 text-xs text-muted-foreground">Size = number of periods · Color = mean calendar date, following the scale from earlier to later. {layout === "timeline" ? "Horizontal position = mean calendar date; vertical lanes separate states." : layout === "kk" || layout === "spring" ? "Network layout uses connections without direction for placement; arrows and geodesic distances remain directed. Display distances are not exact graph distances." : "States are ordered by mean date; distance has no analytical meaning."} Internal self-links are hidden.</p>
        </div>
        <aside className="space-y-4 rounded-xl border p-5">{selected && <><h3 className="font-semibold">State {selected.id}</h3><p className="text-sm">{selected.size} member periods</p><div className="max-h-36 overflow-auto text-sm">{selected.members.join(", ")}</div><h4 className="text-sm font-semibold">Most frequent patterns</h4><ul className="space-y-2 text-xs">{selected.patterns.map(p => <li key={p.name} className="flex justify-between gap-3"><span className="break-all">{p.name}</span><strong>{p.count}</strong></li>)}</ul></>}</aside>
      </div>
      <button type="button" className={field} aria-expanded={showDistances} onClick={() => setShowDistances(!showDistances)}>{showDistances ? "Hide distance heatmap" : "Generate time-to-time distance heatmap"}</button>
      {showDistances && <DistanceHeatmap ids={result.periods} nodes={result.nodes} edges={result.edges} timeColor={timeColor}/>}
    </>}
  </div>;
}
