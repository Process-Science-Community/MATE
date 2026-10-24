# How to use Complexity Over Time

Complexity Over Time shows whether your process is getting simpler or more tangled. It cuts the log into time slices, scores each slice with the measures of the Complexity module and plots the metrics you choose as a trend, with optional bands for concept drift found by CV4CDD.

## When to use it

- You want to know whether a redesign, an automation rollout or a standardisation effort actually made the process simpler, or whether workarounds and new variants are creeping in.
- You want several measures on one chart, such as variant entropy next to the number of distinct activities.
- You want one set of scores for the whole log: use Complexity. For the speed of the process over time use Performance Over Time, and to find when the process itself changed use CV4CDD, whose drift periods this chart can show.

## Before you start

- The module needs a case-centric event log with `case_id`, `activity` and `timestamp` columns, at least 50 events and at least 2 cases. Only those three columns are read. Otherwise the module's card in the process's module list shows an **Unavailable** badge and cannot be opened; hover the card to read the reason.
- The first time you open the module for a process, MATE computes the default view (Calendar mode, Auto granularity), slice by slice. It shows "Computing Complexity Over Time…" with a progress bar and a **Cancel** button before the panel appears, and afterwards the page opens at once. Every other combination of settings is computed when you choose it, and the chart shows a loading state meanwhile.
- Drift bands are optional. They appear only when CV4CDD has already been run on the same process; this module reads CV4CDD's stored results and never starts it.
- A filter bar above the panel (**Add filter** and a time range you confirm with **Apply**) narrows the log, and the module recomputes on the filtered events. It is temporary: reloading the page clears it.

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
| Y-axis metrics | Variant entropy | Which metrics are drawn, one line each. It opens a checklist of 23 metrics, listed below. The last selected metric cannot be unticked. |

The checklist offers the measures of Complexity, computed on each slice: Magnitude (events), Support (cases), Variety (distinct activities), Level of detail, Time granularity (s), Structure, Affinity, Trace length (min), Trace length (avg), Trace length (max), Distinct traces %, Deviation from random, Lempel-Ziv complexity, Pentland task complexity, Pentland process complexity, Variant entropy, Sequence entropy, and the sequence entropy with linear and with exponential forgetting. Each entropy is offered as the raw value and, as "(normalised)", as a value from 0 to 1.

### On the module page

Open **Module Settings** in the sidebar and choose this module. The **Configuration** card has three fields; save them with **Save configuration**. An administrator can lock the card, which makes it read-only. The values are part of what a result is stored under, so a changed value is computed the next time the chart is loaded.

| Setting | Default | What it changes |
| --- | --- | --- |
| Exponential forgetting k | 1.0 | The coefficient of the exponentially forgetting sequence entropy (0.1 to 10 in steps of 0.1). Larger values make older events count less. The original paper uses 1. |
| Default number of slices | 50 | The slice count (2 to 2000) used when Absolute mode is requested without one. The panel always sends its own **Number of slices**, which starts at 50, so changing this does not change what the panel shows. |
| Minimum cases per slice | 1 | A slice with fewer cases than this (1 to 1000) gets no value instead of a noisy one. The chart then draws its line straight across the missing slice, and the Complexity trend card leaves the slice out. |

## Reading the results

### The slices

Each point on the chart is a time slice. A case belongs to the slice in which it started, the time of its first event, even if it ends later, and all of its events count toward that slice.

- **Absolute**: the timeline between the earliest and the latest case start is cut into equal-duration slices, labelled with their start date. Every case lands in exactly one slice, and slices without cases are kept as empty slices. At most 2000 slices are built.
- **Calendar**: one slice per calendar period from the period of the earliest case start to the period of the latest, labelled by the period, for example `2023-01` for a month. Periods without cases are kept as empty slices.
- **Sliding window**: windows of **Window (days)** start at the earliest case start and move on by **Step (days)** until a window would start after the latest case start, up to 2000 windows. A window holds the cases that started inside it, labelled with its start date. When the step is shorter than the window, windows overlap and a case counts in several of them.

### The trend chart

With one metric the heading is the metric's name followed by "over time", for example "Variant entropy over time", with an info icon that explains the measure and names its source. With several metrics it reads "Complexity metrics over time". The horizontal axis lists the slices, and a legend names each line. Hover the chart to see the slice label, the raw value of each drawn metric and the slice's number of cases and events. Durations show as `~3 d 4 h` and percentages with a `%`.

With two or more metrics and **Normalise 0–1** on, each metric is scaled to its own range, the vertical axis is titled "normalized 0–1", a caption below the chart says so, and a metric that never changes is drawn at 0.5. A single metric is always drawn with its real values and, when it is a duration or a percentage, with the axis titled "duration" or "%". With normalisation off, all lines share one axis with real values, so a small metric such as a normalised entropy is flattened by a large one such as the number of events.

Normalised entropies suit comparisons across slices of different size. Magnitude, Support and the other size measures grow with the number of cases in a slice, so a rise in them mostly mirrors more activity.

### Concept-drift bands

When CV4CDD has results for the process, each detected drift period is shaded behind the lines from the first slice it overlaps to the slice after the last one it overlaps. The colour shows the drift type: white for sudden, blue for gradual, magenta for incremental, aqua for recurring and yellow for any other type. The legend above the chart lists the types present, with a count such as `Gradual ×2`, and a caption under the chart explains the shading. Drift periods outside the charted range are not drawn. If CV4CDD has run and no band is drawn, the legend says "No concept drift detected". If CV4CDD has not run, nothing about drift is shown.

### Dashboard cards

Cards follow the filters of their dashboard. Each has an info icon with a short explanation and an arrow (**Open in module**) that opens this page.

- **Complexity trend**: one metric as a line over equal-duration slices, as in Absolute mode. **Metric** chooses it from Sequence entropy (norm.), the default, Variant entropy (norm.), Sequence entropy - linear (norm.), Sequence entropy - exponential (norm.), Lempel–Ziv, Magnitude, Variety and Pentland process. **Time slices** (4 to 50, default 12) sets the number of slices. Slices without a value are left out of the line rather than shown as gaps, and a card with nothing to plot reads "No time series for this log yet." The card has no drift bands, no normalisation and only one metric.

## Tips and limits

- Start with Calendar and Auto. A longer window in Sliding window mode gathers more cases per point, which makes the line less noisy, but neighbouring points then share cases.
- Entropy and the other measures of a slice with only a few cases are unreliable, and the tooltip shows how many cases a slice holds. Raise **Minimum cases per slice** to hide such slices, or use a coarser granularity, fewer slices or a wider window to give each slice more cases. If no slice has enough cases for the chosen metrics, the chart says so and suggests the coarser granularity, fewer slices or a wider window.
- Slices follow when cases started. A case that continues into later periods is counted in its first slice only.
- All three modes cover the span from the earliest to the latest case start. Absolute mode builds at most 2000 slices. Sliding window mode stops after 2000 windows counted from the earliest case start, so a very small step on a long log ends the timeline early, without a warning. Calendar mode has no upper limit, so Daily on a log that spans years creates a very large number of slices and takes longer, and every slice is scored on its own; Auto keeps the number of slices moderate.
- Results for each combination of settings are kept after they are first computed.
- The chart shows how the measures move, not why. Use Complexity for the definitions and the values of the whole log, and CV4CDD to detect drift.

## Method

The module assigns every case to a time slice by the timestamp of its first event, keeping the case whole. In Absolute mode the timeline from the earliest to the latest case start is cut into equal-duration slices. In Calendar mode cases are grouped by calendar period, and Auto picks the period from the span between the first and the last case start. In Sliding window mode windows of a fixed length advance by a fixed step, and a case counts in every window that contains its start.

On the cases of each slice the module computes the same measures as the Complexity module, which follows Augusto, Mendling, Vidgof and Wurm (2022) for the entropies built on the Extended Prefix Automaton. Drift bands are drawn from the detections that CV4CDD stored for the same process. The formal citation is in the info popover next to the module name.
