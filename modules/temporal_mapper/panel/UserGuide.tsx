"use client";

import { useEffect, useRef, useState } from "react";

const sections = [
  { title: "Quick start", content: <ol className="list-decimal space-y-2 pl-5"><li>Choose a time unit (day is the default).</li><li>Start with similarity 0.85, average linkage, temporal links across gaps enabled, and loop size 2.</li><li>Optionally enable PCA and select a retained-variance target.</li><li>Select Build map, then click a state to inspect its periods and original pattern counts.</li></ol> },
  { title: "How patterns are built", content: <><p>Events are sorted by timestamp within each case. All activity occurrences count, including repeated actions. Directed transitions connect successive different activities within a case; no transitions are created between cases.</p><p>Activity counts go to the case’s first period. Transition counts go to the period containing the source of its first cross-activity transition. Dates use UTC and weeks start Monday. Periods with no patterns are omitted.</p><p>Within each period, activity and transition counts are separately L2-normalized to unit length, then concatenated. Zero-count groups stay zero. This preprocessing applies with and without PCA. State details retain the original counts.</p></> },
  { title: "Similarity and clustering", content: <><p>Cosine similarity compares the period vectors. With PCA enabled, it compares the retained component scores. Larger thresholds require closer similarity.</p><p>Average linkage performs hierarchical clustering on 1 − cosine similarity, cutting at 1 − the selected threshold. It connects periods in the same cluster; not every pair in a cluster must exceed the threshold individually.</p><p>Pairwise threshold connects pairs meeting the threshold directly, excluding recurrent links between neighboring periods before adding temporal links.</p></> },
  { title: "Temporal gaps and loop size", content: <><p>A gap is a missing calendar period. “Add temporal links across gaps?” → Yes connects consecutive observed periods even across gaps. No only adds these arrows between adjacent calendar periods.</p><p>Loop size controls merging. Two periods qualify when each can reach the other in fewer arrows than the selected value. Connected groups of qualifying pairs become one state. At 2, directly mutual connections qualify; at 1, no periods merge. A larger value can merge more periods.</p></> },
  { title: "Optional PCA", content: <><p>PCA is disabled by default. When enabled, it centers the separately normalized, concatenated feature columns without additional column scaling, then projects onto principal components.</p><p>The variance target starts at 95%. It keeps the fewest components reaching the target: at least one at 0%, and all numerically nonzero components at 100%. Constant data has no variance to retain. Results report the component count and actual retained variance.</p><p>Centering changes cosine similarities, so 100% PCA can still produce a different map from PCA disabled.</p></> },
  { title: "Reading the map", content: <><p>Each state contains one or more periods, which may be nonconsecutive. Node size reflects the number of member periods; color represents their mean date, using the dated color bar.</p><p>Arrows retain directed connections from the temporal and similarity graph after merging. They are not frequencies or causal effects. Internal self-links are hidden. Click a node for exact dates and its most frequent patterns.</p></> },
  { title: "Interacting and appearance", content: <><p>Drag a node to reposition it, or drag the background to pan. Use the map controls to zoom, fit, reset positions, or enter fullscreen. State IDs are hidden by default; enable them through the settings gear.</p><p>Spring is the default layout. KK and Spring use connectivity without direction to place nodes; arrows remain directed. Timeline positions nodes horizontally by mean date. Display distances are not exact geodesic distances.</p><p>Layout, palette, node-size limits, spacing, and arrow size update without rebuilding. Changing layout resets dragged positions. Changing spacing also clears dragged positions. The same palette applies to the heatmap, with its own distance legend.</p></> },
  { title: "Time-to-time distance heatmap", content: <><p>Generate the heatmap after building a map. Rows are source periods and columns destination periods, in chronological order. Dates are period starts; periods with no patterns are omitted.</p><p>Each cell is the shortest directed path between the states containing those periods, counted in arrows. A → B can differ from B → A. Periods in the same state have distance 0; gray ∞ means unreachable.</p><p>Hover a cell or use the source/destination selectors to inspect a value. This expands distances from the merged state graph onto time axes; it is not the graph before merging.</p></> },
  { title: "Method and citation", content: <><p>Temporal Mapper was introduced by Mengsen Zhang, Samir Chowdhury, and Manish Saggar. This module implements GraphExplain’s adaptation for process event logs.</p><p>Zhang, M., Chowdhury, S., &amp; Saggar, M. (2023). <a className="underline" href="https://doi.org/10.1162/netn_a_00301" target="_blank" rel="noopener noreferrer">Temporal Mapper: Transition networks in simulated and real neural dynamics.</a> <em>Network Neuroscience, 7</em>(2), 431–460.</p><p>Zhang, L., Wolf, J. R., Pentland, A. P., &amp; Pentland, B. T. (2025). <a className="underline" href="https://doi.org/10.1007/978-3-031-78666-2_26" target="_blank" rel="noopener noreferrer">Visualizing Routine Dynamics in Outpatient Medical Clinics with Topological Data Analysis.</a> In <em>Business Process Management Workshops</em> (BPM 2024), Lecture Notes in Business Information Processing, vol. 534, pp. 338–349. Springer.</p></> },
];

export default function UserGuide() {
  const [open, setOpen] = useState(false);
  const trigger = useRef<HTMLButtonElement>(null);
  const closeButton = useRef<HTMLButtonElement>(null);
  const close = () => { setOpen(false); trigger.current?.focus(); };
  useEffect(() => {
    if (!open) return;
    closeButton.current?.focus();
    const handleKey = (event: KeyboardEvent) => {
      if (event.key === "Escape") { setOpen(false); trigger.current?.focus(); }
    };
    document.addEventListener("keydown", handleKey);
    return () => document.removeEventListener("keydown", handleKey);
  }, [open]);
  return <>
    <button ref={trigger} type="button" aria-expanded={open} aria-controls="tm-user-guide" className="rounded-md border px-3 py-1.5 text-sm" onClick={() => setOpen(value => !value)}>How to use</button>
    {open && <aside id="tm-user-guide" aria-labelledby="tm-guide-title" className="fixed inset-y-0 right-0 z-[60] flex w-full max-w-md flex-col border-l bg-background shadow-xl">
      <div className="flex items-center justify-between gap-3 border-b p-4"><h2 id="tm-guide-title" className="font-semibold">Temporal Mapper guide</h2><button ref={closeButton} type="button" className="rounded-md border px-3 py-1 text-sm" onClick={close}>Close</button></div>
      <div className="overflow-y-auto p-4"><p className="mb-4 text-xs text-muted-foreground">Keep this guide open while exploring. Close it or press Escape to return to the full map.</p>
        {sections.map((section, i) => <details key={section.title} open={i === 0 ? true : undefined} className="border-b py-3"><summary className="cursor-pointer text-sm font-medium">{section.title}</summary><div className="mt-3 space-y-3 text-sm leading-relaxed text-muted-foreground">{section.content}</div></details>)}
      </div>
    </aside>}
  </>;
}
