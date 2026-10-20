# How to use Performance Over Time

Performance Over Time shows whether your process is getting faster or slower. It cuts the log into time slices, calculates the Performance module's KPIs on each slice and plots the metrics you choose as a trend, with optional bands for concept drift found by CV4CDD.

## When to use it

- You want to know whether cycle time or throughput changed over the life of the log, for example to check that an improvement initiative moved the numbers or to catch a slowdown early.
- You want several metrics on one chart, such as median and P90 cycle time, or cycle time against throughput.
- You want one set of figures for the whole log: use Performance. To see how many cases arrive and finish per period, or how much work is in progress, use Log Evolution. To find when the process itself changed, use CV4CDD, whose drift periods this chart can show.

## Before you start

- The module needs a case-centric event log with `case_id`, `activity` and `timestamp` columns, at least 50 events and at least 2 cases. Only those three columns are read.
- The first time you open the module for a process, MATE computes the default view (Calendar mode, Auto granularity). It shows "Computing Performance Over Time…" with a progress bar and a **Cancel** button before the panel appears, and afterwards the page opens at once. Every other combination of settings is computed when you choose it, and the chart shows a loading state meanwhile.
- Drift bands are optional. They appear only when CV4CDD has already been run on the same process; this module reads CV4CDD's stored results and never starts it.

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
| Y-axis metrics | Avg cycle time | Which metrics are drawn, one line each. It opens a checklist of Cases, Events, Variants, Avg cycle time, Median cycle time, P90 cycle time, P95 cycle time, Min cycle time, Max cycle time, Throughput (cases/day) and Lead time. The last selected metric cannot be unticked. |
| Normalise 0–1 | On | Only shown while two or more metrics are selected. It rescales each metric to its own minimum and maximum so that metrics with different units share one axis from 0 to 1. The tooltip keeps the raw values. |

### On the module page

Open **Module Settings** in the sidebar and choose this module. Its **Configuration** card has two fields; save them with **Save configuration**. An administrator can lock the card, which makes it read-only.

| Setting | Default | What it changes |
| --- | --- | --- |
| Default number of slices | 50 | The slice count (2 to 2000) used when Absolute mode is requested without one. The panel always sends its own **Number of slices**, which starts at 50, so changing this does not change what the panel shows. |
| Minimum cases per slice | 1 | A slice with fewer cases than this (1 to 1000) gets no value instead of a noisy one. The chart then draws its line straight across the missing slice, and the Performance trend card leaves the slice out. |

## Reading the results

### The slices

Each point on the chart is a time slice. A case belongs to the slice in which it started, the time of its first event, even if it ends later, and all of its events count toward that slice.

- **Absolute**: the timeline between the earliest and the latest case start is cut into equal-duration slices, labelled with their start date. Every case lands in exactly one slice, and slices without cases are kept as empty slices.
- **Calendar**: one slice per calendar period from the period of the earliest case start to the period of the latest, labelled by the period, for example `2023-01` for a month. Periods without cases are kept as empty slices.
- **Sliding window**: windows of **Window (days)** start at the earliest case start and move on by **Step (days)** until a window would start after the latest case start. A window holds the cases that started inside it, labelled with its start date. When the step is shorter than the window, windows overlap and a case counts in several of them.

### The trend chart

With one metric the heading is the metric's name followed by "over time", for example "Avg cycle time over time", and with several metrics it reads "Performance metrics over time". The horizontal axis lists the slices, and a legend names each line. When every drawn metric is a duration and the axis shows real values, the vertical axis uses durations such as `~3 d 4 h`. Hover the chart to see the slice label, the raw value of each drawn metric and the slice's number of cases and events.

The metrics are those of the Performance module, calculated on the cases of each slice:

- **Cases**, **Events**, **Variants**: the number of cases in the slice, the number of events of those cases and the number of distinct activity sequences among them.
- **Avg cycle time**, **Median cycle time**, **P90 cycle time**, **P95 cycle time**, **Min cycle time**, **Max cycle time**: the statistic over the cycle times of the slice's cases, where a case's cycle time runs from its first to its last event.
- **Throughput (cases/day)**: the slice's cases divided by the days between the first and the last event of those cases. This is not the length of the slice. It equals the number of cases when all of those events share one instant.
- **Lead time**: calculated exactly like **Avg cycle time**, so the two lines always match.

With two or more metrics and **Normalise 0–1** on, each metric is scaled to its own range, a caption below the chart says so, and a metric that never changes is drawn at 0.5. A single metric is always drawn with its real values. With normalisation off, all lines share one axis with real values, so a small metric such as throughput is flattened by a large one.

### Concept-drift bands

When CV4CDD has results for the process, each detected drift period is shaded behind the lines from the first slice it overlaps to the slice after the last one it overlaps. The colour shows the drift type: red for sudden, blue for gradual, magenta for incremental, aqua for recurring and yellow for any other type. The legend above the chart lists the types present, with a count such as `Sudden ×2`, and a caption under the chart explains the shading. Drift periods outside the charted range are not drawn. If CV4CDD has run and no band is drawn, the legend says "No concept drift detected". If CV4CDD has not run, nothing about drift is shown.

### Dashboard cards

- **Performance trend**: one metric as a line over equal-duration slices, as in Absolute mode. **Metric** chooses it from Avg cycle time (the default), Median cycle time, P90 cycle time, P95 cycle time, Min cycle time, Max cycle time, Throughput (cases/day) and Lead time. **Time slices** (4 to 50, default 12) sets the number of slices. Slices without a value are left out of the line rather than shown as gaps, and a card with nothing to plot reads "No time series for this log yet." The card has no drift bands, no normalisation and only one metric.

## Tips and limits

- Start with Calendar and Auto. A longer window in Sliding window mode gathers more cases per point, which makes the line less noisy, but neighbouring points then share cases.
- A slice with only a few cases gives a noisy value, and the tooltip shows how many cases it holds. Raise **Minimum cases per slice** to hide such slices, or use a coarser granularity, fewer slices or a wider window to give each slice more cases. If no slice has enough cases for the chosen metrics, the chart says so and suggests the coarser granularity, fewer slices or a wider window.
- Slices follow when cases started. If the log contains cases that are still running, they are measured up to their latest event, which can make the latest slices look faster than they will turn out to be.
- All three modes cover the span from the earliest to the latest case start. Absolute mode builds at most 2000 slices. Sliding window mode stops after 2000 windows counted from the earliest case start, so a very small step on a long log ends the timeline early, without a warning. Calendar mode has no upper limit, so Daily on a log that spans years creates a very large number of slices and takes longer; Auto keeps the number of slices moderate.
- Results for each combination of settings are kept after they are first computed.
- A filter bar above the panel (**Add filter** and a time range you confirm with **Apply**) narrows the log, and the module recomputes on the filtered events.
- The chart shows how the metrics move, not why. Use Performance for the figures of the whole log and CV4CDD to detect drift.

## Method

The module assigns every case to a time slice by the timestamp of its first event, keeping the case whole. In Absolute mode the timeline from the earliest to the latest case start is cut into equal-duration slices. In Calendar mode cases are grouped by calendar period, and Auto picks the period from the span between the first and the last case start. In Sliding window mode windows of a fixed length advance by a fixed step, and a case counts in every window that contains its start.

On the cases of each slice the module computes the same KPIs as the Performance module. A case's cycle time is the time between its first and its last event. The average, median, 90th and 95th percentile (interpolated), minimum and maximum are taken over the slice's cases, lead time equals the average cycle time, and throughput is the number of cases divided by the time between the earliest and the latest event of those cases. Drift bands are drawn from the detections that CV4CDD stored for the same process.
