# How to use Log Evolution

Log Evolution shows how your log grows and shifts over time. You get case arrivals against completions, work in progress, the changing mix of activities and a dotted chart of every event, so you can spot backlogs, seasonality and changes in behaviour.

## When to use it

- You want to know whether work is piling up: arrivals that stay above completions mean the number of open cases is growing.
- You want to spot seasonality, batch processing or a shift in which activities dominate when, or to check whether a process redesign changed how work arrives and gets done.
- You want durations rather than volumes. Use Performance Over Time for cycle time and throughput per period. For periods whose activity patterns look alike, use Temporal Mapper, and to detect drift explicitly, use CV4CDD.

## Before you start

- The module needs a case-centric event log with `case_id`, `activity` and `timestamp` columns, at least 50 events and at least 2 cases. Only those three columns are read; the optional `end_timestamp` column is not used, and events whose timestamp cannot be read are ignored.
- The first time you open the module for a process, MATE computes the default series (Auto granularity). It shows "Computing Log Evolution…" with a progress bar and a **Cancel** button before the panel appears, and afterwards the page opens at once. Other granularities and the dotted chart are computed when they are first shown.

## Settings

Three controls sit in the bar above the charts. Each loads or redraws only the charts it belongs to.

| Setting | Default | What it changes |
| --- | --- | --- |
| Granularity | Auto | The calendar period each point stands for: Auto, Daily, Weekly, Monthly, Quarterly or Yearly. It applies to Arrivals & completions, Active cases and Activity mix over time, which show a loading state until the new series arrives. Auto chooses from the span between the first and the last event: days up to 60 days, weeks up to 540 days, months up to 2,200 days and quarters beyond that. |
| Activities shown | 8 | 4, 6, 8, 10 or 12. How many of the most frequent activities get their own band in Activity mix over time; the rest are summed into one band called Other. Only that chart changes, and nothing is loaded again. |
| Dotted-chart points | 8,000 | 2,000, 4,000, 8,000, 12,000 or 20,000. The most events the Dotted chart draws. When the log has more events, a sample of that size is drawn. Only the Dotted chart is loaded again. |

## Reading the results

### Arrivals & completions

Two lines, one point per calendar period, from the period of the log's first event to the period of its last. Periods without events are kept and show zero. **Arrivals** counts the cases whose first event falls in the period, and **Completions** counts the cases whose last event falls in it. Every case appears once in each line, so each line adds up to the number of cases. Where arrivals stay above completions, cases are accumulating, and where completions rise above arrivals, the backlog is draining. Hover the chart to read both values for a period. An empty chart reads "No time series for this log yet."

### Active cases (work in progress)

An area chart of the cases that are open at the end of each period: the cases that started in or before the period, minus the cases whose last event is in or before it. A case that starts and ends within the same period is never counted. A rising curve means work arrives faster than it clears, and a flat one means arrivals and completions are balanced. The curve never drops below zero and always ends at zero, because every case has a last event.

### Activity mix over time

A stacked area chart of the number of events per activity in each period. These are event counts, not shares: the height of the whole stack is the number of events in the period. The most frequent activities of the whole log get their own band, up to **Activities shown**, and all other activities are summed into a grey band called Other, which appears only when the log has more activities than bands. The legend names the bands, and hovering shows each band's count. A band that grows or vanishes marks a change in how the process is run, and sometimes a change in how events are recorded. An empty chart reads "No activity mix for this log yet."

### Dotted chart

The classic dotted chart: one dot per event. The horizontal position is the time of the event, and the vertical position is the case, ordered by the time of its first event, with the earliest case at the top. The vertical axis shows each case's rank, starting at 0, not its case ID. The colour is the activity: the 12 most frequent activities of the log keep their own colour and all others share a grey called Other. All events of one case sit in one row, so a steady flow of cases typically forms a diagonal band whose width reflects how long cases take, and a vertical stripe means events of many cases at the same moments, which points to batch processing. Hovering a dot shows its date and its case rank.

The **Activities** button in the legend row opens a checklist that hides activities from the chart. Once something is hidden, the button shows how many activities are visible, for example `7/13`. At the top of the list, **Hide all** hides every activity, and **Show all** appears while something is hidden. Hiding only changes what is drawn: the other colours stay the same and nothing is loaded again. The choice is not kept when you leave the page. When the log has more events than **Dotted-chart points**, a caption reads "Showing X of Y events (sampled)." and the sample is fixed, so the same picture appears each time. An empty chart reads "No events to chart for this log yet."

### Dashboard cards

Cards follow the filters of their dashboard. Each has an info icon with a short explanation and an arrow (**Open in module**) that opens this page.

- **Arrivals & completions**: the arrivals and completions lines. **Granularity** (default Auto) sets the period.
- **Active cases (WIP)**: the open cases at the end of each period. **Granularity** (default Auto) sets the period.
- **Activity mix over time**: the stacked event counts per activity. **Granularity** (default Auto) sets the period, and **Activities shown** (2 to 12, default 8) sets how many activities keep their own band.
- **Dotted chart**: one dot per event, with the **Activities** filter. **Max points** (500 to 20,000 in steps of 500, default 8,000) sets how many events are drawn.

## Tips and limits

- A case counts as completed in the period of its last recorded event. The module does not know about end activities, so a case that is still running when the log ends counts as completed in the period of its latest event.
- The charts count cases and events and say nothing about duration. For cycle time or throughput per period, use Performance Over Time.
- **Granularity** applies to the three time charts, **Activities shown** only to Activity mix over time, and **Dotted-chart points** only to the Dotted chart. A coarser period smooths out day-to-day noise, and a finer one shows exact peaks.
- The dotted chart never draws more than 20,000 events, whatever the log size or the setting. Its vertical axis has one row per case, so on logs with many cases the rows overlap.
- A filter bar above the panel (**Add filter** and a time range you confirm with **Apply**) narrows the log, and the module recomputes on the filtered events.
- The module shows volumes over time, not a process model. Use Discovery for the structure of the process.

## Method

The module places every case in the calendar period of its first event as an arrival and in the period of its last event as a completion, and it counts each event in the period of its timestamp. Periods run without gaps from the first event's period to the last one's, so empty periods count as zero. Work in progress is the running sum of arrivals minus the running sum of completions. The activity mix counts events per activity and period: the twelve most frequent activities are kept as their own series and all others are summed as Other, and the panel then keeps as many as **Activities shown**.

For the dotted chart, each case gets a rank by the time of its first event, and that rank is the vertical position of all its events. When the log has more events than the limit, the module draws a random sample of that size with a fixed seed. There is no formal citation for this module.
