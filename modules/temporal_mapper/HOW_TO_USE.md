# How to use Temporal Mapper

Temporal Mapper groups calendar periods (days, weeks, months or years) whose activity patterns look alike into states, then shows how the process moves between those states over time. Use it to see which patterns recur and how the process drifts.

## When to use it

- You suspect the process behaves differently on different days, weeks, months or years, for example because of seasonality, campaigns or month-end peaks.
- You want to know whether a pattern recurs, such as a busy week that looks like one from three months earlier.
- You want a high-level map of how the process changes over time rather than a case-by-case view. For drift with explicit change points, use CV4CDD; for the trend of a single metric, use Performance over time.

## Before you start

- The module needs a case-centric event log with `case_id`, `activity` and `timestamp` columns, at least 50 events and at least 2 cases.
- Nothing is precomputed. The map is built on demand when you press **Build map**, and the result is not cached, so changing a setting means building again.
- The module refuses oversized input instead of sampling it silently: at most 250,000 events, 800 occupied periods and 5,000 features. An error explains which limit you hit.
- To try it without your own data, import [recurring_process.csv](https://github.com/Process-Science-Community/MATE/blob/main/modules/temporal_mapper/examples/recurring_process.csv), map its columns to `case_id`, `activity` and `timestamp`, open the imported process and choose **Temporal Mapper**. The synthetic log has 54 cases over 18 weeks, with recurring approval, escalation and automated workflows.

## Settings

### Building the map

These controls sit above the map. Changing one hides the distance heatmap and clears the selected state, but the map on screen stays as it was until you press **Build map** again.

| Setting | Default | What it changes |
| --- | --- | --- |
| Time unit | Day | The size of a period: Day, Week, Month or Year. Dates use UTC and weeks start on Monday. |
| Similarity | 0.85 | How alike two periods must be (0 to 1, cosine similarity) to be connected. Higher values need closer matches and give more, smaller states. |
| Clustering | Average linkage | How periods are grouped. **Average linkage** clusters hierarchically; **Pairwise threshold** connects every pair that meets the similarity directly. |
| Temporal links across gaps | Bridge gaps | A gap is a period with no recorded patterns. **Bridge gaps** links consecutive observed periods even across a gap; **Adjacent only** links only periods that are next to each other on the calendar. |
| Loop size | 2 | How aggressively periods merge into one state (1 to 20). At 1 nothing merges; larger values merge more. |
| Enable PCA | Off | Projects the period vectors onto principal components before comparing them. |
| Retained variance (%) | 95 | Only shown when PCA is on (0 to 100). The share of variance the kept components must explain. |

### Display controls

These appear once a map exists and update it instantly, without rebuilding.

| Setting | Default | What it changes |
| --- | --- | --- |
| Layout | Spring (Fruchterman–Reingold) | Where states are placed: Circular, Grid, Timeline, Kamada–Kawai (KK) or Spring. Changing it resets any nodes you dragged. |
| Color palette | Blue–orange | The color scale for dates: Blue–orange, Viridis, Plasma or Blues. It also colors the heatmap. |
| Layout spacing | 3.0× | How far apart states are drawn (1 to 3). Changing it also resets dragged nodes. |
| Minimum radius | 7 | The smallest node radius (3 to 48). Used by the states with the fewest periods. |
| Maximum radius | 20 | The largest node radius (3 to 48). Used by the states with the most periods. |
| Arrow size | 9 | The size of the arrowheads (2 to 20). |
| Reset sizes | | Puts both radii back to their defaults. |
| Show state IDs | Off | Prints each state's number on its node. It lives in the settings gear on the map's toolbar. |

## Reading the results

### The summary line

Above the map you see the number of states, arrows, occupied periods, features and calendar gaps. Occupied periods are the periods that contain at least one pattern; empty ones are left out. Features are the distinct activities and transitions the periods are compared on. If PCA is on, a second line reports how many components were kept and how much variance they retain; if some events were invalid, a note says how many were excluded.

### The state map

Each circle is a state: one or more periods with similar patterns, not necessarily consecutive. Its size is the number of member periods and its color is their mean date, following the dated color bar below the map. Arrows are the directed connections that remain after periods were merged. They are not frequencies and not causal effects, and a state's links to itself are hidden.

Click a state (or focus it and press Enter or Space) to inspect it. Drag a state to move it, drag the background to pan, and use the toolbar to zoom, fit the map to the view, reset it or go fullscreen. Hovering a state lists its member dates.

With **Timeline**, horizontal position is the mean date and the vertical lanes only separate states. **Circular** and **Grid** order states by mean date without encoding distance. **KK** and **Spring** place states using their connections without direction; arrows stay directed. Distances on screen are never exact.

### State details

The panel beside the map shows the selected state's number, how many periods it contains, the exact dates of those periods (period starts, possibly nonconsecutive) and its **Most frequent patterns**. The counts there are the original activity and transition counts, before any normalization.

### The time-to-time distance heatmap

Press **Generate time-to-time distance heatmap** to show how far apart in the state graph any two periods are. Rows are source periods and columns are destination periods, both in chronological order and labeled by their start date.

Each cell is the shortest directed path, counted in arrows, between the states containing those two periods. The direction matters: from A to B can differ from B to A. Periods in the same state have distance 0, and a gray ∞ means the destination cannot be reached. Hover a cell, or use the **Source period** and **Destination period** selectors, to read an exact value. The heatmap expands the merged state graph onto time axes; it is not the graph before merging.

## Tips and limits

- Start with the defaults: Day, similarity 0.85, Average linkage, Bridge gaps, loop size 2 and PCA off. Then change one setting at a time.
- Too many small states? Lower **Similarity** or raise **Loop size**. Everything collapsed into one blob? Raise **Similarity** or lower **Loop size**.
- Use a coarser **Time unit** when daily periods are too sparse to look alike.
- PCA is optional. Centering the data changes the cosine similarities, so even 100% retained variance can give a different map than PCA off. Constant data has no variance to retain.
- The map shows which periods are alike and how the process moves between them. It is not a process model, a Petri net or a forecast.
- Large logs cost memory: the similarity and shortest-path computations grow quadratically with the number of periods.

## Method

For each period, the module counts every activity occurrence and every directed transition between two successive different activities within a case; nothing is linked across cases. All of a case's activity counts go to the period of its first event, and all of its transition counts go to the period containing the source of its first transition between different activities, which can be a different period. Within each period the activity counts and the transition counts are each scaled to unit length and then joined into one vector, with or without PCA.

Periods are compared by cosine similarity (on the component scores when PCA is on). **Average linkage** cuts a hierarchical clustering at one minus the similarity and connects the periods in each cluster, so not every pair in a cluster has to reach the threshold itself. **Pairwise threshold** connects pairs that reach it directly. Temporal links are then added between consecutive periods, and two periods merge into one state when each can reach the other in fewer arrows than **Loop size**.

Temporal Mapper was introduced by Mengsen Zhang, Samir Chowdhury and Manish Saggar (2023). This module follows GraphExplain's adaptation of it for process event logs, which Zhang, Wolf, Pentland and Pentland applied to routine dynamics in outpatient clinics (2025). The formal citations are in the info popover next to the module name.
