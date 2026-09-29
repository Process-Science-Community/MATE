# Temporal Mapper

Minimal Python contribution ported from GraphExplain's active R pipeline:
`TemporalMapperInteractive/R/TMapper_R/{similarity.r,cluster.r,tmapper_pipeline.R,visual.r}`
and the calendar-pattern portion of `R/series_builder.R`.
The older `R/temporal_mapper.R` PCA-cover implementation is not the algorithm
called by GraphExplain's current UI and is not used here.

## Citation and attribution

The original Temporal Mapper method was introduced by **Mengsen Zhang, Samir
Chowdhury, and Manish Saggar**. Please cite their paper when using this method:

Zhang, M., Chowdhury, S., & Saggar, M. (2023).
[Temporal Mapper: Transition networks in simulated and real neural dynamics](https://doi.org/10.1162/netn_a_00301).
*Network Neuroscience, 7*(2), 431–460.

This MATE module is a Python port of GraphExplain’s adaptation for process event
logs, with calendar aggregation and cosine similarity as described below.
The original method and the process-mining adaptation have distinct provenance.

## Try it

Import `examples/recurring_process.csv` through MATE's normal event-log import.
Map its columns to `case_id`, `activity`, and `timestamp`, open the imported
process, choose **Temporal Mapper**, then **Build map**. The synthetic example
has 54 cases over 18 weeks, with recurring approval, escalation, and automated
workflows. Click a state to inspect its member periods and pattern counts.

Defaults: daily periods, average-linkage clustering, cosine threshold 0.85,
consecutive temporal edges, loop size 2, PCA disabled. A loop size of 1 prevents collapsing.
Calendar gaps are counted even when consecutive mode bridges them.

## Method and scope

1. Sort events by UTC timestamp, retaining input order for ties. Build directed
   cross-activity transitions within each case; repeated identical adjacent
   activities produce no transition. Activity frequencies still include repeats.
2. Preserve GraphExplain's binning: all activity counts of a case go to its
   first event period; all transition counts go to the period of the source
   event of its first cross-activity transition. These can be different periods.
   Weeks start Monday. Empty periods are omitted, not filled with zero rows.
3. Within each period, separately divide the activity block and transition block
   by their L2 norms, then concatenate them. Zero blocks stay zero. Both nonzero
   blocks therefore have equal vector length. Apply this preprocessing whether
   PCA is enabled or disabled. Optionally center the resulting feature columns
   and apply PCA using NumPy SVD, without additional column scaling.
   The retained-variance target defaults to 95% and accepts 0–100%. Keep the
   fewest components reaching the target, with at least one at 0% and all
   numerically nonzero components at 100%. Constant data produces zero scores
   and a visible explanation. Report the actual retained variance and component
   count. Centering changes cosine similarities even at 100%; original counts
   remain available in node details. Compute cosine similarity of the resulting
   vectors (separately normalized counts when PCA is disabled). Two zero rows have similarity
   1; exactly one zero row gives similarity 0.
4. Average linkage cuts cosine distance at `1 - threshold` and connects members
   of each cluster. Pairwise mode applies the threshold directly, then removes
   recurrent links between neighboring rows, matching the R implementation.
5. Add forward temporal links between consecutive occupied periods, or only
   adjacent calendar periods. Spatial recurrence can still connect across gaps.
6. Compute directed shortest-path distances, join pairs whose distance is
   strictly less than `d` in both directions, and collapse connected components.
   The quotient adjacency uses maximum/binary connectivity, as in R. These
   arrows are not transition frequencies, causal effects, or a Petri-net model.

The graph defaults to Spring (Fruchterman–Reingold), with circular, grid,
timeline, and Kamada–Kawai layouts also available. Timeline horizontal
positions represent mean member dates; vertical lanes only separate states.
Circular and grid layouts order states by mean date without encoding distance.
Display controls update without rebuilding the map. Color choices are the original
blue–orange scale, Viridis, Plasma, and Blues; the dated legend follows the palette. Node size indicates period count;
color uses the mean UTC calendar date of member periods, with a dated color bar. Node details show exact dates,
including nonconsecutive dates. Internal self-links are retained in the core's
quotient adjacency but hidden in the UI. Feature names use JSON-encoded pairs
to avoid the original hyphen-concatenation ambiguity.

This first version includes only activity and transition counts, average
linkage/pairwise thresholding, calendar gaps, loop collapsing, and inspection.
It omits semantic embeddings, GitHub-specific terminal filtering,
entity-level maps, other linkage methods, recurrence plots, AI explanations,
batch comparisons, and research notes. The graph stages retain the R pipeline logic, but separate block normalization
can change results relative to the raw-feature R pipeline;
they will differ from GraphExplain runs with its optional embedding enabled.

Invalid events are excluded with a visible count. Limits: 250,000 input events,
800 occupied periods, and 5,000 features. Exceeding a limit raises an actionable
error rather than silently sampling. Dense similarities and shortest paths are
quadratic in memory; CPU work runs off the API event loop. Results are computed
on demand against MATE's filtered event-log access and are not cached.

## Development and validation

No R runtime or separate service is needed. The module inherits NumPy, pandas,
SciPy, and FastAPI from MATE's environment and changes no platform source files.

```bash
uv run --all-packages --extra dev python -m pytest modules/temporal_mapper/tests -q
uv run --all-packages --extra dev ruff check modules/temporal_mapper
node apps/web/scripts/bundle-modules.mjs temporal_mapper
pnpm --dir apps/web typecheck
```

`tests/r_reference.json` was produced by the original GraphExplain R functions
on 2026-09-29, using a seven-row fixture containing recurrence, a missing week,
and zero vectors. Twelve method/gap/loop combinations compare cosine matrices,
original adjacency, memberships, and quotient adjacency. Additional tests cover
case boundaries, tied timestamps, period assignment, isolated nodes, and limits.
`tests/generate_reference.R` regenerates the fixture given the GraphExplain
`TemporalMapperInteractive/R` directory as its argument. R is only needed to
regenerate references, not to run the module or Python tests.

License: MIT, as declared in GraphExplain's TemporalMapperInteractive README.
The source paths above record the implementation provenance of this port.

## Directed distance heatmap

After building a map, select **Generate time-to-time distance heatmap**. It computes
unweighted shortest paths on the displayed, collapsed state graph using outgoing
arrows only. Rows are source periods and columns destination periods, ordered chronologically
and labeled by UTC period-start date. Each period maps to its collapsed state;
the corresponding directed state distance fills the cell. Different periods in
the same state have distance zero. Empty periods are omitted.
Distances count arrows; the diagonal is zero and unreachable pairs are infinity
(gray), never replaced with zero or symmetrized. The selected palette applies to both the graph and heatmap, with separate
time and distance scales; unreachable pairs remain gray. Hover or use the source/destination selectors to
inspect exact values. This expands the collapsed graph’s distances to time-by-time axes; it is not
the distance matrix used internally before collapsing.

Additional network layouts: **Kamada–Kawai (KK)** minimizes shortest-path spring
stress using bounded gradient iterations; **Spring (Fruchterman–Reingold)** uses
node repulsion and edge attraction with cooling. Both are deterministic and use
undirected connectivity only for placement. KK gives disconnected pairs a finite
layout-only separation target of twice the largest finite path distance. Neither
changes directed arrows or the directed distance heatmap. Coordinates are display
approximations, not exact distances. Layout calculations are memoized and capped
at 160 iterations for the module's 800-state limit.

Node size controls set minimum and maximum radii (3–48 SVG units; defaults
7–20). Circle area interpolates linearly between these limits over the
observed member-count range. Equal counts use the minimum radius. Arrow endpoints
follow the selected sizes. Controls update the display without rebuilding.

Default layout spacing is 3× and initial/reset zoom is 39%.

Arrowhead size is adjustable from 2 to 20 (default 9), independently of node
size and line width, without rebuilding the map.
