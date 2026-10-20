# How to use Process Comparison

Process Comparison puts two views of event logs side by side and shows what differs: headline figures, similarity scores, an overlaid process map and diagram, the variants and the activities. Each side is a log plus its own filter, so you can compare two logs or two groups of cases of the same log.

## When to use it

- You want to know how two logs, periods or regions differ, such as before and after a change, region A against region B, or one quarter against another, and where exactly the behaviour diverges.
- You want to see differences in the process, not only in numbers: which activities and connections appear, vanish or grow.
- You want to know whether a difference is more than chance. Process Comparison describes the differences and runs no statistical test. Use Pcomp to test two logs. To look at a single log, use Discovery.

## Before you start

- The log you open the module on needs a case-centric event log with `case_id`, `activity` and `timestamp` columns, at least 50 events and at least 2 cases. Otherwise the module's card in the process's module list shows an **Unavailable** badge and cannot be opened; hover the card to read the reason.
- You compare ready case-centric logs of your account, including the one you opened the module on. Object-centric logs are not offered. If there is none, the panel reads "No comparable logs".
- Nothing is precomputed. Each tab computes when you open it, and a result is kept until the log's data or a side's filter changes. This page has no filter bar of its own, because every side carries its own filters.
- A side must keep at least one event. If its filter removes them all, the tab fails with "Side A matches no events - loosen or clear its filter."

## Settings

### In the panel

Two cards, **A · baseline** on the left and **B · comparison** on the right, choose what is compared. A is the log the module was opened on and B the first other log, or the same log when there is no other. A is the reference: the tabs rank differences by how far they are from A.

| Setting | Default | What it changes |
| --- | --- | --- |
| Log of each side (placeholder "Pick a log") | A: the open log, B: the first other log | Which log the side reads. Picking another log clears the side's filters. The number of cases of the picked log appears next to the picker. |
| Filters of each side | The log's applied filter | The side reads only the matching rows. Column filters are added with **Add filter**, and the time range is confirmed with **Apply**. When you pick a log, a filter applied on its **Events** tab is copied into the side once, so the filter you see is the filter that is used, and you can edit or clear it. |
| **Swap** | – | Exchanges A and B. |
| **Layout** (Process map) | Overlay | In the map's **Graph settings**: **Overlay** merges both graphs, **Side by side** draws each side's graph in its own pane. |
| **Colour** (Process map) | Δ change | In the map's **Graph settings**, for the overlay only: **Δ change** colours by direction of change, **Presence** by where an element exists. |
| **Layout** (BPMN) | Side by side | In each diagram's **Graph settings**: **Side by side** or **Stacked**, which gives each diagram the full width. |
| **Metric** (Similarity) | Earth Mover's Distance | Which score the matrix shows: **Earth Mover's Distance**, **Footprints similarity**, **Variant overlap**, **Edge overlap** or **Activity overlap**. |

The module has no configuration on its module page. If both sides are the same log with the same filter, the panel reads "Both sides are identical" and shows nothing; give one side another log or another filter. Until both sides are picked it reads "Pick two sides to compare".

## Reading the results

The tabs are **Summary**, **Similarity**, **Process map**, **BPMN**, **Variants** and **Activity deltas**, and **Summary** is open first. Sides are named with their letter, their log and the number of filters, for example `B · Orders 2024 (2 filters)`.

### Summary

Five cards, each reading `value in A → value in B`, with the difference and, when A is not zero, the difference in percent: **Cases**, **Events**, **Distinct activities**, **Variants** and **Mean throughput**. Mean throughput is the average time from a case's first to its last event, shown as a duration, and its card says "lower is better". The arrow and the colour only show direction: green means B is higher than A and red means B is lower, even where lower is better.

### Similarity

A matrix of the sides against each other for the chosen metric, with "–" on the diagonal. **Earth Mover's Distance** is a distance between the two logs' variant distributions: 0 means identical behaviour and higher means more different. The other four are overlaps: 100% means identical and lower means more different. **Activity overlap**, **Edge overlap** and **Variant overlap** divide what the sides share by what either has, counted in activities, directly-follows connections and variants. **Footprints similarity** does the same for a set of behavioural relations: directly-follows, parallel, start and end. Greener cells are more similar.

### Process map

The directly-follows graph of both sides, with a legend for the chosen colouring.

- **Overlay**: one graph with every activity and connection of either side, laid out top to bottom. A line above the graph counts the shared connections and those only in A and only in B. An activity reads `count in A → count in B`, followed by `(Δ+3)` or `(Δ-3)` in **Δ change** when it changed, or `N · baseline only` or `N · comparison only` when it exists on one side. A connection reads `count in A → count in B` too, or just its count when it exists on one side. Connections are drawn thicker the more often they occur, and dashed when they exist on one side only. With **Δ change**, green means more or added (only in B, or a shared element that grew), red means less or removed, and grey means unchanged. With **Presence**, blue marks elements only in A, amber only in B and grey shared ones.
- **Side by side**: two panes, each a plain graph of one side, with the counts of that side.

Each canvas has a toolbar with **Zoom out**, **Zoom in**, **Fit to view**, **Graph settings**, **Reset layout** and **Fullscreen**.

### BPMN

A BPMN diagram for each side, mined with the Inductive Miner. The tasks are coloured by how the activity changed from A to B: green for an activity that was added or grew, red for one that was removed or shrank, and no colour for an unchanged one. A badge reads "new" for an activity only in B, "gone" for one only in A, and `Δ+3` or `Δ-3` for a shared activity that changed. Hover a badge for both counts. The diagrams cannot be edited. Mining takes a moment on large logs, and the tab says so while it works.

### Variants

"N distinct variants across both sides · showing the M that diverge most from A (share of cases)". One row per variant, written as a chain of activities, with the share of each side's cases that follow it and, in brackets, the number of cases. A "–" means the side has none. The rows are ordered by how far the share differs from A, and the list stops at 60.

### Activity deltas

A bar chart of the activity frequency share per side for the 15 activities that diverge most from A, and a table of the top 40. Shares are percentages of all events of the side, which takes out the difference in size. Next to each share the table gives the average sojourn time of the activity, the time from one of its events to the next event of the same case, or "–" when there is none.

## Tips and limits

- Compare cohorts of one log by giving the two sides different filters, for example a region or a quarter. A log with a filter applied on its **Events** tab starts a side with that filter, so clear it if you want the whole log.
- Read shares, not counts, when the sides differ in size. The Variants and Activity deltas tabs use shares.
- The counts on the process map and the BPMN badges are derived from the directly-follows graph: an activity counts the times another activity follows it, raised to the number of cases that start or end with it when that is higher. For an activity that both ends some cases and is followed by others, this is lower than its number of events. The Variants and Activity deltas tabs count events exactly.
- The Earth Mover's Distance uses only the 100 most frequent variants of each side, which keeps it fast and changes it little.
- The Variants tab shows at most 60 variants, and the Activity deltas table 40 activities.
- A difference here is a difference in these two views, not proof of a lasting change. Run Pcomp on two logs to test whether it is more than chance.

## Method

Each side is read through its own filter. The process map is the directly-follows graph of each side, found with PM4Py, with every activity and connection marked as shared, only in A or only in B and carrying both counts. The BPMN diagrams come from PM4Py's Inductive Miner. A variant is the sequence of activities of a case. The Earth Mover's Distance compares the two variant distributions with PM4Py, and the overlaps are Jaccard indices over sets of activities, connections, variants and footprint relations. The sojourn time of an event is the gap to the next event of the same case. There is no formal citation for this module.
