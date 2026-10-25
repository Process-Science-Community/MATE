# How to use Performance Metrics (Java)

Performance Metrics (Java) shows where your process spends its time. You get the headline timing figures, a ranking of the activities by the time cases stay in them, and a ranking of the hand-offs between two activities by the time that passes between them.

## When to use it

- You want to see where time is lost, both in the step where cases linger and in the hand-off that delays them, so a bottleneck shows up on both rankings.
- Your log has an `end_timestamp` and you want to read waiting against work: the module then adds the average processing time per case and the share of waiting.
- You want a statistical bottleneck test or the distribution of cycle times. Use Performance for that. For the trend over time use Performance Over Time, and for waits that depend on who handled a case use Actor Performance.

## Before you start

- The module needs a case-centric event log with `case_id`, `activity` and `timestamp` columns and at least 10 events; there is no minimum number of cases. Rows with a missing case ID, activity or timestamp are dropped before anything is counted. Otherwise the module's card in the process's module list shows an **Unavailable** badge and cannot be opened; hover the card to read the reason.
- Two optional columns are read only when the log has them. `end_timestamp` gives every event its own duration and adds the processing and waiting figures. `resource` adds the number of distinct actors.
- The first time you open the module for a process, MATE computes all three results. It shows "Computing Performance Metrics (Java)…" with a progress bar, the current step ("Computing KPIs", "Ranking activities", "Ranking hand-offs") and a **Cancel** button before the panel appears. Afterwards the page opens at once, and it computes again when the log's data changes.
- Nothing to install or configure: the module is a self-contained Java program that runs inside MATE.
- A filter bar above the panel (**Add filter** and a time range you confirm with **Apply**) narrows the log, and the module recomputes on the filtered events. It is temporary: reloading the page clears it.

## Settings

The panel has one control and the module has no configuration on its module page.

| Setting | Default | What it changes |
| --- | --- | --- |
| Top (the slider reads "Top 10") | 10 | How many ranked rows the chart and the table show, from 5 to 25 in steps of 5. It applies to both tabs and does not recompute anything. |

The two tabs, **Activities** and **Hand-offs**, switch between the rankings. **Activities** is open first.

## Reading the results

### The KPI strip

Tiles across the top:

- **Cases**, with the number of events underneath.
- **Variants**: the distinct paths cases take, with the average number of events per case underneath. Many variants means little standardisation.
- **Avg cycle time**, **Median cycle time** and **P90 cycle time**: how long a case takes from its first to its last recorded event, as an average, as the middle value and as the value below which 90% of cases finish. Cycle time includes all waiting, so a few slow cases pull the average up.
- **Throughput**: cases per day, the number of cases divided by the calendar span between the first and the last event of the log. It shows the volume the process sustained, not its speed. A log whose events all share one instant has no span, and the tile shows "-".
- **Avg processing time**: the work per case, the sum of the events' own durations (from `timestamp` to `end_timestamp`), averaged over cases. Shown only when the log has an `end_timestamp`.
- **Waiting share**: the part of the average cycle time that is not processing time, limited to 0% to 100%. Shown only with an `end_timestamp`.
- **Resources**: the number of distinct actors. Shown only when the log has a `resource` column.

Durations are rounded for display, and a `~` marks the rounding, for example `~3 d 4 h`.

### Activities

A bar chart of the top activities by **Total dwell**, and a table below it with one row per activity. The dwell time of an event is the wall-clock gap from its timestamp to the next event of the same case: the wait plus the work. The info icon next to the slider explains it.

- **Activity**: the name. Click it to open the activity view.
- **Occurrences** and **Cases**: how many events the activity has and in how many cases it occurs.
- **Median dwell** and **P90 dwell**: the typical gap and the gap that only 10% of the events exceed.
- **Avg processing**: the average event duration. The column appears only when the log has an `end_timestamp`.
- **Total dwell**: the sum of all gaps. The rows are ranked by it, so the top rows are the bottlenecks.
- **Share**: the activity's part of the total dwell of all listed activities.

The last event of a case has no next event, so it counts in **Occurrences** but not in the dwell figures. An empty tab reads "No activity timings for this log."

### Hand-offs

The same layout for the directly-follows pairs: a bar chart of the top pairs by **Total wait**, written `A → B`, and a table.

- **Hand-off**: the two activity names; each one links to its activity view.
- **Occurrences** and **Cases**: how often the pair occurs and in how many cases.
- **Median wait** and **P90 wait**: the typical gap between the two events and the gap that only 10% of the hand-offs exceed.
- **Total wait** and **Share**: the sum of all gaps and its part of the total of all listed hand-offs. The rows are ranked by total wait.

The wait is measured from the timestamp of the first event to the timestamp of the next event of the same case. It is the dwell time of the first activity, split by the activity that follows, so it still contains the first activity's own work. An empty tab reads "No hand-offs for this log."

### Dashboard cards

The module has no cards of its own. It offers three datasets in the **Cards & data** palette of a dashboard, which show them with the platform's generic visualizations and follow the dashboard's filters:

- **Performance KPIs (Java)**: cases, variants, cycle-time percentiles and throughput.
- **Activity performance**: the activities ranked by the total time cases stay in them.
- **Hand-off performance**: the directly-follows transitions ranked by the wait between the steps.

## Tips and limits

- Both rankings are ordered by total time, so a frequent step with short visits can outrank a rare slow one. Read the median and P90 columns for what a single visit costs.
- The rankings keep at most 500 activities and 300 hand-offs. If more exist, a note reads "Ranking capped at N rows - filter the log to see the rest."
- A case that is still running is not detected as such: its last recorded event ends its cycle time.
- A share is relative to all rows the module kept, up to 500 activities or 300 hand-offs, not only to the rows on screen.
- Cycle times and gaps are wall-clock times that include nights and weekends. The module does not separate working time from waiting unless the log has an `end_timestamp`, and then only for the event itself.
- For statistically flagged bottlenecks and the cycle-time histogram use Performance, and for waits by actor behaviour use Actor Performance.

## Method

All figures come from aggregate queries that run on MATE's side against the filtered log; only the aggregates reach the module. A case's cycle time is the time between its first and its last event timestamp, and the average, the median and the 90th percentile are taken over all cases with interpolated percentiles. Variants count the distinct activity sequences in timestamp order. Throughput divides the number of cases by the calendar span of the log.

For the rankings the events of each case are put in timestamp order. The dwell time of an event is the gap to the next event of the same case, and the hand-off wait is the same gap grouped by the pair of activities. Processing time is the difference between an event's `end_timestamp` and its `timestamp`. There is no formal citation for this module: the figures are standard descriptive statistics.
