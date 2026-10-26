# How to use Complexity

Complexity puts numbers on how tangled your process is. It scores the log with entropy, size, variation and Pentland measures and lists them in two tables, so you can compare processes or process versions and see how much variability a log carries.

## When to use it

- You want to compare processes or versions of a process objectively, for example before and after a standardisation effort, by scoring each log with the same measures.
- You want to know early whether a log will produce an unreadable spaghetti model, before you discover one.
- You want to see how the complexity develops over time. Use Complexity Over Time. For a larger suite of 28 measures including trace distances, use Complexity V2.

## Before you start

- The module needs a case-centric event log with `case_id`, `activity` and `timestamp` columns, at least 50 events and at least 2 cases. Otherwise the module's card in the process's module list shows an **Unavailable** badge and cannot be opened; hover the card to read the reason.
- The first time you open the module for a process, MATE computes the measures. It shows "Computing Complexity…" with a progress bar and a **Cancel** button before the panel appears, and afterwards the page opens at once.
- A filter bar above the panel (**Add filter** and a time range you confirm with **Apply**) narrows the log, and the module recomputes on the filtered events. It is temporary: reloading the page clears it.
- A second view, **Enriched EPA**, exists only for logs that carry a complete set of XES attributes (see Reading the results). Without them the panel shows the basic view only.

## Settings

The panel has no controls apart from the two tabs described below. The module has one setting, on its module page: open **Module Settings** in the sidebar and choose this module. An administrator can lock the **Configuration** card, which makes it read-only. Save with **Save configuration**.

| Setting | Default | What it changes |
| --- | --- | --- |
| Exponential forgetting k | 1.0 | The coefficient k of the exponentially forgetting sequence entropy (0.1 to 10 in steps of 0.1). Larger values make older events count less. The original paper uses 1. The panel shows the value in the row's name. Saving does not recompute results that already exist; the new value applies the next time the measures are computed, for example after the log's data has changed. |

## Reading the results

A badge above the tables tells which attribute set the module found: "Enriched attribute set detected", or "Basic only (XES standard attributes missing)". With the enriched set there are two tabs, **Basic** and **Enriched EPA**, each with the same two tables; otherwise there is one view. **Enriched EPA** needs these XES attributes, on traces `variant`, `concept:name`, `creator` and `variant-index`, and on events `time:timestamp`, `Resource`, `lifecycle:transition`, `concept:name`, `Activity` and `org:resource`. It builds the automaton behind the entropies with steps told apart by those attributes. The entropies and Pentland's task complexity are recomputed on that automaton, and all other measures are the same in both views.

Most measures have an info icon with a short explanation and the author who introduced the measure.

### Entropy measures

One row per measure, with its **Value** and its **Normalised (0–1)** form. All four are computed on the log's Extended Prefix Automaton (EPA), in which traces that share a beginning share states, so the entropy tells how much the recorded behaviour branches.

- **Variant entropy**: how much the structure branches, counted in states. It ignores how often each path is taken.
- **Sequence entropy**: the same entropy weighted by the number of events instead of states, so it also reflects how frequent each prefix is.
- **Sequence entropy (linear forgetting)** and **Sequence entropy (exponential forgetting, k=1)**: sequence entropy in which an event counts less the older it is. The linear weight falls from 1 for the latest event to 0 for the earliest, and the exponential weight is `e` to the power of minus k times the relative age. They have no info icon.

Higher entropy means less predictable behaviour. The normalised value divides by the largest possible entropy, so logs of different sizes can be compared on it; the raw value grows with the size of the log.

### Selected measures

One row per measure with its **Value** and a short **Description**.

- **Magnitude**: the number of events. **Support**: the number of cases. **Variety**: the number of distinct activities. These are size measures.
- **Level of detail**: the average number of distinct activities per case.
- **Time granularity**: the mean over cases of the shortest gap between two consecutive events, shown as a duration. It tells how fine-grained the timestamps are.
- **Structure**: 1 minus the distinct directly-follows connections divided by the square of the number of activities. Near 1 the behaviour is sparse and structured, near 0 almost any activity can follow any other.
- **Affinity**: the average similarity of the directly-follows patterns of two cases, over all pairs. Homogeneous logs score high.
- **Trace length**: the shortest, the average and the longest case, as `min / avg / max`.
- **Distinct traces**: the number of distinct variants, that is of different activity sequences, as a share of the number of cases, in percent. 100% means every case took its own path, and 25% means that each path is taken by four cases on average.
- **Deviation from random**: 1 minus the distance between the shares of transitions and an even spread over all pairs of activities. Close to 1 the transitions are spread almost evenly, like random behaviour; lower, a few transitions dominate.
- **Lempel-Ziv complexity**: the number of phrases needed to describe the stream of all events in time order without repetition. Repetitive behaviour needs few, random-looking behaviour many.
- **Pentland's task complexity**: the sum of the lengths of the paths from the root of the prefix automaton to each of its end nodes, in effect the total length of the distinct variants, leaving out a variant that is only the beginning of a longer one. It has no info icon.
- **Pentland's process complexity**: `10^(0.08·(1 + e − v))` with e distinct directly-follows connections and v distinct activities, an estimate of the number of routes through the process. A log with very many connections shows "–", because the value would exceed what can be represented.

A "–" in any row means the measure is undefined for this log, for example **Affinity** with fewer than two cases.

### Dashboard cards

Cards follow the filters of their dashboard. Each has an info icon with a short explanation and an arrow (**Open in module**) that opens this page. Both cards use the basic view.

- **Complexity metrics**: up to eight tiles: Magnitude, Variety, Distinct traces, Avg trace len, Variant entropy, Sequence entropy, Lempel–Ziv and Pentland, which is the process complexity. The two entropies are the normalised values. Until you choose, a card shows all eight. The **Figures** list in the card's settings has one checkbox per tile, where Avg trace len reads Avg trace length and Pentland reads Pentland process complexity. It starts with Sequence entropy, Lempel-Ziv and Pentland process complexity unticked, and once you change any box the card shows exactly the ticked tiles. Each tile has an info icon with the explanation and source of the measure.
- **Entropy comparison**: a bar chart from 0 to 1 of the normalised variant entropy, sequence entropy and the linear and exponential forgetting variants.

The module also offers the dataset **Complexity metrics** in the **Cards & data** palette of a dashboard.

## Tips and limits

- The measures disagree by design, because they capture different kinds of complexity. Compare two logs on the same measure rather than looking for one overall score, and read a change over versions rather than a single value.
- When logs differ in size, compare the normalised entropies and the ratio measures, such as **Distinct traces**, **Structure** and **Affinity**, instead of the raw sizes and the raw entropies.
- The Lempel-Ziv measure runs over all events in time order as one stream, so cases that overlap in time influence each other.
- On logs with more than 4,000 distinct directly-follows patterns, **Affinity** uses the most frequent ones and is an approximation.
- Everything is computed from the activity names and the timestamps. The **Enriched EPA** view is the only place where other attributes enter.
- The measures describe the log; they do not say whether a process is good or bad. For the structure of the process use Discovery.

## Method

The module follows Augusto, Mendling, Vidgof and Wurm (2022), whose entropy measures are computed on the Extended Prefix Automaton of the log, and the size, variation and distance measures collected from Günther (2009), van der Aalst (2016), Pentland (2003) and Hærem et al. (2015). The implementation ports the reference code of Rüschel and Langer. The formal citation is in the info popover next to the module name.

The variant entropy is the graph entropy `|S|·log|S|` minus the sum of `|p|·log|p|` over the partitions p of the automaton's states S. The sequence entropy uses the number of events in each partition instead of the number of states, and the forgetting variants weight each event by its recency before summing. Normalised entropies divide by the largest possible value. Lempel-Ziv counts the phrases of the LZ76 parse of the time-ordered stream of activities.
