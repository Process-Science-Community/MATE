# How to use Complexity V2 Over Time

Complexity V2 Over Time shows whether your process is getting simpler or more tangled, measured with the 28 metrics of Complexity V2. It cuts the log into time slices, scores each slice with the whole suite and plots the metrics you choose as a trend, with optional bands for concept drift found by CV4CDD.

## When to use it

- You want to know whether a redesign, an automation rollout or a standardisation effort actually made the process simpler, or whether new variants and growing differences between cases are creeping in.
- You want to follow measures that Complexity Over Time does not have, such as the average edit distance between cases, the structural process variety or the number of acyclic paths, next to the entropies.
- You want one set of scores for the whole log: use Complexity V2. For a smaller set of measures over time use Complexity Over Time, for the speed of the process use Performance Over Time, and to find when the process itself changed use CV4CDD, whose drift periods this chart can show.

## Before you start

- The module needs a case-centric event log with `case_id`, `activity` and `timestamp` columns, at least 50 events and at least 2 cases. Otherwise the module's card in the process's module list shows an **Unavailable** badge and cannot be opened; hover the card to read the reason.
- The first time you open the module for a process, MATE computes the default view (Calendar mode, Auto granularity), slice by slice. It shows "Computing Complexity V2 Over Time…" with a progress bar and a **Cancel** button before the panel appears, and afterwards the page opens at once. Every other combination of settings is computed when you choose it, and the chart shows a loading state meanwhile.
- Every slice is scored with the whole suite, including the two distance metrics, so a series takes longer to compute than in Complexity Over Time, most of all with many slices or many variants per slice.
- Drift bands are optional. They appear only when CV4CDD has already been run on the same process; this module reads CV4CDD's stored results and never starts it.
- A filter bar above the panel (**Add filter** and a time range you confirm with **Apply**) narrows the log, and the module recomputes on the filtered events. It is temporary: reloading the page clears it.
- The four enriched entropies need the complete set of XES attributes that Complexity V2 lists. Without it they have no values.

## Settings

### In the panel

These controls sit in the bar above the chart. Number fields apply when you leave the field or press Enter, and values outside the limits are corrected to the nearest limit. The controls for the slicing (**Mode**, **Number of slices**, **Granularity**, **Window (days)**, **Step (days)**) load a new series and replace the chart with a loading state until it arrives. **Y-axis metrics** and **Normalise 0–1** only redraw the series that is already loaded.

| Setting | Default | What it changes |
| --- | --- | --- |
| Mode | Calendar (relative) | How the timeline is cut into slices: **Absolute (fixed count)** uses a fixed number of equal-duration slices, **Calendar (relative)** uses calendar periods and **Sliding window** uses overlapping windows. The next controls depend on the mode. |
| Number of slices | 50 | Absolute mode only (2 to 2000). How many equal-duration slices the timeline is cut into. |
| Granularity | Auto | Calendar mode only: Auto, Daily, Weekly, Monthly, Quarterly or Yearly. The size of a slice. Auto chooses from the span between the first and the last case start: days up to 60 days, weeks up to 540 days, months up to 2,200 days and quarters beyond that. |
| Window (days) | 30 | Sliding window mode only (at least 1). The length of each window. |
| Step (days) | 7 | Sliding window mode only (at least 1). How far each window starts after the previous one. A step larger than the window leaves gaps in coverage. |
| Normalise 0–1 | On | Only shown while two or more metrics are selected. It rescales each metric to its own minimum and maximum so that metrics on different scales share one axis from 0 to 1. The tooltip keeps the raw values. |
| Y-axis metrics | Sequence entropy (normalized) | Which metrics are drawn, one line each. It opens a checklist of 28 metrics, listed below. The last selected metric cannot be unticked. |

The checklist offers the metrics of Complexity V2, computed on each slice, in the order of its panel: the four entropies (Variant entropy, Sequence entropy and their normalized forms), the four enriched entropies, the size measures (number of events, event types and sequences, the minimum, average and maximum sequence length, and the average time between events), the variation measures (number of acyclic paths, ties, Lempel-Ziv complexity, number and percentage of unique sequences, average distinct events per sequence, order variation and activity variation) and the distance measures (average affinity, structure, deviation from random, average edit distance and structural process variety). Complexity V2 explains each of them.

### On the module page

Open **Module Settings** in the sidebar and choose this module. The **Configuration** card has three fields; save them with **Save configuration**. An administrator can lock the card, which makes it read-only. The values are part of what a result is stored under, so a changed value is computed the next time the chart is loaded.

| Setting | Default | What it changes |
| --- | --- | --- |
| Distance-metric variant cap | 300 | How many distinct trace variants each slice uses for the two distance metrics, average edit distance and structural process variety (25 to 1000 in steps of 25). The most frequent variants of the slice are kept. Larger values are more faithful and cost memory and time that grow with the square of the number of variants in a slice. |
| Default number of slices | 50 | The slice count (2 to 2000) used when Absolute mode is requested without one. The panel always sends its own **Number of slices**, which starts at 50, so changing this does not change what the panel shows. |
| Minimum cases per slice | 1 | A slice with fewer cases than this (1 to 1000) gets no value instead of a noisy one. The chart then draws its line straight across the missing slice, and the Complexity V2 trend card leaves the slice out. |

## Reading the results

### The slices

Each point on the chart is a time slice. A case belongs to the slice in which it started, the time of its first event, even if it ends later, and all of its events count toward that slice.

- **Absolute**: the timeline between the earliest and the latest case start is cut into equal-duration slices, labelled with their start date. Every case lands in exactly one slice, and slices without cases are kept as empty slices. At most 2000 slices are built.
- **Calendar**: one slice per calendar period from the period of the earliest case start to the period of the latest, labelled by the period, for example `2023-01` for a month. Periods without cases are kept as empty slices.
- **Sliding window**: windows of **Window (days)** start at the earliest case start and move on by **Step (days)** until a window would start after the latest case start, up to 2000 windows. A window holds the cases that started inside it, labelled with its start date. When the step is shorter than the window, windows overlap and a case counts in several of them.

### The trend chart

With one metric the heading is the metric's name followed by "over time", for example "Sequence entropy (normalized) over time", with an info icon that explains the measure and names its source. With several metrics it reads "Complexity V2 metrics over time". The horizontal axis lists the slices, and a legend names each line. Hover the chart to see the slice label, the raw value of each drawn metric and the slice's number of cases and events. Whole numbers show as they are, durations with a unit such as `3.4 d`, percentages such as `12.50 %`, and every other value with three decimals.

With two or more metrics and **Normalise 0–1** on, each metric is scaled to its own range, the vertical axis is titled "normalized 0–1", a caption below the chart says so, and a metric that never changes is drawn at 0.5. A single metric is always drawn with its real values and, when it is a duration or a percentage, with the axis titled "duration" or "%". With normalisation off, all lines share one axis with real values, so a small metric such as a normalised entropy is flattened by a large one such as the number of events.

Normalised entropies suit comparisons across slices of different size. The size measures grow with the number of cases in a slice, so a rise in them mostly mirrors more activity.

A slice can lack the value of a single metric even when it has enough cases: the average affinity and the average edit distance need at least two cases in the slice, and the structural process variety at least two variants. The tooltip shows "–" there, and the line is drawn straight across the slice.

If no slice has a value for the chosen metrics, the chart shows "No slice has enough cases to compute the selected metric" and suggests a coarser granularity, fewer slices or a wider window. That is also what you see when you plot only enriched entropies for a log without the complete XES attribute set, where the advice does not help.

### Concept-drift bands

When CV4CDD has results for the process, each detected drift period is shaded behind the lines from the first slice it overlaps to the slice after the last one it overlaps. The colour shows the drift type: white for sudden, blue for gradual, magenta for incremental, aqua for recurring and yellow for any other type. The legend above the chart lists the types present, with a count such as `Gradual ×2`, and a caption under the chart explains the shading. Drift periods outside the charted range are not drawn. If CV4CDD has run and no band is drawn, the legend says "No concept drift detected". If CV4CDD has not run, nothing about drift is shown.

### Dashboard cards

Cards follow the filters of their dashboard. Each has an info icon with a short explanation and an arrow (**Open in module**) that opens this page.

- **Complexity V2 trend**: one metric as a line over equal-duration slices, as in Absolute mode. **Metric** chooses it from Normalized sequence entropy, the default, Normalized variant entropy, Sequence entropy, Variant entropy, Activity variation, Order variation, Lempel–Ziv, Average edit distance, Structural process variety, Average affinity and Structure. **Time slices** (4 to 50, default 12) sets the number of slices. A slice that was not scored, because it has fewer cases than the minimum, is left out of the line, and a card with nothing to plot reads "No time series for this log yet." The card has no drift bands, no normalisation and only one metric.

## Tips and limits

- Start with Calendar and Auto. A longer window in Sliding window mode gathers more cases per point, which makes the line less noisy, but neighbouring points then share cases.
- The metrics of a slice with only a few cases are unreliable, and the tooltip shows how many cases a slice holds. Raise **Minimum cases per slice** to hide such slices, or use a coarser granularity, fewer slices or a wider window to give each slice more cases.
- The distance metrics use at most the **Distance-metric variant cap** most frequent variants of each slice. On a slice with more variants, or with very long cases, they describe the dominant behaviour only, and the page does not tell you when that happens.
- Slices follow when cases started. A case that continues into later periods is counted in its first slice only.
- All three modes cover the span from the earliest to the latest case start. Absolute mode builds at most 2000 slices. Sliding window mode stops after 2000 windows counted from the earliest case start, so a very small step on a long log ends the timeline early, without a warning. Calendar mode has no upper limit, so Daily on a log that spans years creates a very large number of slices and takes longer, and every slice is scored on its own; Auto keeps the number of slices moderate.
- Results for each combination of settings are kept after they are first computed.
- The chart shows how the measures move, not why. Use Complexity V2 for the definitions and the values of the whole log, and CV4CDD to detect drift.

## Method

The module assigns every case to a time slice by the timestamp of its first event, keeping the case whole. In Absolute mode the timeline from the earliest to the latest case start is cut into equal-duration slices. In Calendar mode cases are grouped by calendar period, and Auto picks the period from the span between the first and the last case start. In Sliding window mode windows of a fixed length advance by a fixed step, and a case counts in every window that contains its start.

On the cases of each slice the module computes the 28 metrics of Complexity V2, with the same code, which follows Langer's thesis on business process complexity and Augusto, Mendling, Vidgof and Wurm (2022) for the entropies built on the Extended Prefix Automaton. Drift bands are drawn from the detections that CV4CDD stored for the same process. The formal citations are in the info popover next to the module name.
