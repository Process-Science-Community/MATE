# How to use Performance

Performance shows how fast your process runs and where cases lose time. You get throughput, cycle-time and lead-time figures, a histogram of how long cases take, and a table of the activities where cases spend unusually long.

## When to use it

- You want headline speed figures for a log: how many cases the process handles per day, how long a typical case takes and how long the slowest 10% take.
- You want to know which steps hold cases up, for example to decide where speeding up pays off most.
- You want these figures over time. Use Performance Over Time for the trend, and Log Evolution to see how many cases arrive and finish per period.
- You want to know how long cases wait between two activities. Performance Metrics (Java) ranks those hand-offs, and Discovery can print durations on the arrows of its process map (set its **Edge label** to **Duration**).

## Before you start

- The module needs a case-centric event log with `case_id`, `activity` and `timestamp` columns, at least 100 events and at least 5 cases. For a smaller log, or an object-centric one, the module page says it is not available for this process and gives the reason.
- Only those three columns are read. Every event has a single timestamp, so all durations are measured between event timestamps; the optional `resource` and `end_timestamp` columns are not used.
- Nothing is precomputed and there is no run button. The page asks for its figures when you open it and keeps them for later visits; they are computed again when the log's data changes.
- Discovery is an optional companion. Performance works without it, but its module card shows a **Limited** badge while Discovery is not installed.

## Settings

This module has no settings.

## Reading the results

### The log summary

The top row has four tiles taken from the process itself, the same figures the process list shows: **Cases** (process instances), **Events** (recorded activities), **Variants** (distinct paths) and **Time span** (the dates of the first and last event, with the length of that span underneath).

### The KPI strip

Five tiles show the speed figures. Each has an info icon with its definition.

- **Throughput**: cases per day on average, over the full time span of the log. It is the number of cases divided by the days between the first and the last event, shown with one decimal as `cases/day`. Higher means more volume gets through.
- **Avg cycle time**: how long a case takes from its first to its last recorded event, averaged across all cases. A few very slow cases pull it up.
- **Median cycle**: half of all cases finish within this time. It is barely affected by rare, very slow cases, so it is often the more typical duration.
- **P90 cycle**: 90% of cases finish within this time; only the slowest 10% take longer. A realistic worst case for most of your cases.
- **Lead time**: the end-to-end time a case spends in the process, from its first to its last recorded event, averaged across cases, including waiting between steps. This module calculates it exactly like **Avg cycle time**, so the two tiles always show the same value.

Cycle time is wall-clock time, so it includes every wait. Every case counts: a case that is still running is measured up to its latest event, and a case with a single event has a cycle time of zero. Durations are rounded for display, and a `~` marks the rounding, for example `~3 d 4 h`.

### Cycle time distribution

A histogram of the cases' cycle times. Each bar counts the cases whose cycle time falls into one range, and hovering a bar shows that count. The range from the fastest to the slowest case is divided into 50 equal widths. Widths without a case are not drawn, so the bars are evenly spaced and the gap between two bars does not show how far apart their durations are. Read the labels on the horizontal axis instead: each is the time midway between the fastest and the slowest case in that bar. The top right of the card shows the shortest and the longest cycle time as **min** and **max**.

Dashed vertical lines mark the **median**, **p90** and **p95**: half, 90% and 95% of the cases are faster. A line is drawn on the first bar whose labelled time is at or above the value, and when two of them land on the same bar they share one line and one label, for example `median · p90`. Because the ranges are equally wide, one very slow case stretches the axis and squeezes most cases into the first few bars.

### Critical bottlenecks

The table lists the activities where cases spend unusually long, slowest first. The time measured is the time spent at an activity: the gap from one of its events to the next event of the same case. It covers the work and the wait before the next step. The last event of a case has no next event, so it is left out. An activity is listed when its average time spent is at least the median plus 1.5 times the interquartile range (IQR) of the average time spent across all activities, a standard outlier rule.

- **#**: the rank by average time spent.
- **Activity**: the activity name. Hover the row to reveal an arrow that opens the activity view.
- **Avg time spent**: the average time a case spends at this activity, waiting plus processing, from the activity's event until the case moves on to its next step.
- **Frequency**: how many times the activity occurred across all cases in the log.
- **P90 time spent**: 90% of visits to this activity take less time than this; only the slowest 10% take longer.
- **Share**: the activity's share of the total time spent across all activities in the process (average time spent times frequency). Big shares are where speeding up pays off most. Only the flagged activities are listed, so the shares in the table do not have to add up to 100%.
- **Distribution**: a small bar chart of the time-spent values at this activity. Each bar is a duration range on a logarithmic scale, and taller bars mean more occurrences in that range. It has no axes or tooltip, so it only shows the shape.

Click a row to highlight it, and click it again to remove the highlight; nothing else on the page changes. If you arrive from the **View performance metrics** link in Discovery, that activity's row is highlighted when it is in the table. If no activity stands out, the table is replaced by a message saying that no bottlenecks were detected.

### Dashboard cards

Cards follow the filters of their dashboard, so their figures can differ from this page, which has no filter bar. Each card has an info icon with a short explanation and an arrow (**Open in module**) that opens this page.

- **Performance KPIs**: up to eight tiles: Cases, Events, Variants, Throughput, Avg cycle, Median cycle, P90 cycle and Lead time, defined as above. A new card shows all eight, and **Figures** in the card's settings chooses which it shows. Clicking a tile opens this page.
- **Bottlenecks**: horizontal bars for the activities flagged as bottlenecks, longest average time spent first. Hover a bar for its **Avg time spent**. **Activities shown** (3 to 20, default 8) sets how many bars appear, and fewer appear when fewer activities are flagged. Clicking a bar opens that activity's view. An empty card reads "No bottlenecks detected."
- **Activity throughput**: vertical bars for the most frequent activities, counted in events, so an activity that occurs several times in a case counts each time. **Activities shown** (3 to 25, default 10) sets how many. Clicking a bar opens that activity's view.

## Tips and limits

- Cycle time and time spent are wall-clock times: they include nights, weekends and every wait, and the module does not separate work from waiting. Performance Metrics (Java) can split them when your log has an `end_timestamp`.
- The bottleneck rule compares activities with each other. It does not say whether the process is fast enough, it can flag steps that are quick in absolute terms, and it flags nothing when all activities take a similar time. When the interquartile range is zero, the median alone is the threshold, so every activity above zero at or above the median is listed.
- An activity that only ever ends cases has no time spent, so it cannot be flagged.
- A very slow case dominates the average and stretches the histogram. Compare **Avg cycle time** with **Median cycle** and **P90 cycle** before drawing conclusions.
- Median and percentiles are interpolated between neighbouring values, so a percentile can fall between two cases' actual durations.
- This page has no filter bar. Results are kept after the first computation and refreshed when the log's data changes.
- Next steps: Performance Over Time shows whether the figures move, Discovery shows where the slow steps sit in the process, and Actor Performance explains waits by actor behavior.

## Method

For each case the module takes the time between its first and its last event as the cycle time. The average, median, 90th and 95th percentile, minimum and maximum are calculated over all cases, with interpolated percentiles. Lead time is the average cycle time. Throughput divides the number of cases by the time between the earliest and the latest event in the log, and equals the number of cases when that time is zero.

For the bottleneck table the module sorts each case's events by time and takes the gap between an event and the next event of the same case as one time-spent sample for the first event's activity. It averages the samples per activity, takes their 90th percentile, and multiplies the average by the activity's frequency to get the total time behind the share. There is no formal citation for this module: the figures are standard descriptive statistics.
