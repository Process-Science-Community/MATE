# How to use Complexity V2

Complexity V2 scores your log with a suite of 28 complexity metrics covering entropy, size, variation and the distance between cases, and draws the matrix of transition probabilities between activities. Use it to see how complicated a process is and where the complexity comes from.

## When to use it

- You want the full complexity picture in one run, to benchmark processes against each other or versions of one process.
- You want to know whether complexity comes from many variants or from long, unpredictable traces: compare the variation and the distance metrics.
- You want to see which transitions make behaviour hard to predict: read the probability matrix.
- You want a quick, smaller set of measures: use Complexity. To see how the 28 metrics develop over time, use Complexity V2 Over Time.

## Before you start

- The module needs a case-centric event log with `case_id`, `activity` and `timestamp` columns, at least 50 events and at least 2 cases. Otherwise the module's card in the process's module list shows an **Unavailable** badge and cannot be opened; hover the card to read the reason.
- The first time you open the module for a process, MATE computes the metrics and the matrix. It shows "Computing Complexity V2…" with a progress bar and a **Cancel** button before the panel appears, and afterwards the page opens at once.
- A filter bar above the panel (**Add filter** and a time range you confirm with **Apply**) narrows the log, and the module recomputes on the filtered events. It is temporary: reloading the page clears it.
- Four enriched entropies need a complete set of XES attributes (see below). Without them they show "–".

## Settings

The panel has no controls. The module has one setting, on its module page: open **Module Settings** in the sidebar and choose this module. An administrator can lock the **Configuration** card, which makes it read-only. Save with **Save configuration**.

| Setting | Default | What it changes |
| --- | --- | --- |
| Distance-metric variant cap | 300 | How many distinct trace variants the two distance metrics, `avg-edit-distance` and `structural-var`, use (25 to 1000 in steps of 25). The most frequent variants are kept. Larger values are more faithful and cost memory and time that grow with the square of the number of variants. Saving does not recompute results that already exist; the new value applies the next time the metrics are computed, for example after the log's data has changed. |

## Reading the results

The panel shows one card per category, each with a table of **Metric**, **Value** and **Description**. A metric is named with its short label in code style, such as `var-e`, followed by its name and an info icon that explains the measure and names the author who introduced it. The source is also written under the category title when all its metrics share one, and under each metric otherwise. A "–" means the value is undefined for this log.

### Entropy

How unpredictable the recorded behaviour is, measured on the log's Extended Prefix Automaton (EPA), in which traces that share a beginning share states (Augusto et al., 2022).

- `var-e` **Variant Entropy**: how much the structure branches, counted in states, ignoring how often each path is taken.
- `seq-e` **Sequence Entropy**: the same entropy weighted by the number of events per state, so it also reflects how frequent each prefix is.
- `nvar-e` and `nseq-e`: the same two entropies divided by their largest possible value, from 0 to 1, so logs of different size can be compared.

### Enriched Entropy

`en-var-e`, `en-seq-e`, `en-nvar-e` and `en-nseq-e` are the four entropies computed on an automaton whose states also depend on the event and trace attributes, so identical activity sequences with different event data count as different behaviour (Vidgof and Mendling, 2023). They need the XES attributes `variant`, `concept:name`, `creator` and `variant-index` on traces and `time:timestamp`, `Resource`, `lifecycle:transition`, `concept:name`, `Activity` and `org:resource` on events. Without the complete set, the card shows "–" and "XES standard attributes missing".

### Size

- `#-e` **Number of Events**, `#-et` **Number of Event Types** (the distinct activities) and `#-seq` **Number of Sequences** (the cases).
- `min-seq-len`, `avg-seq-len` and `max-seq-len`: the number of events in the shortest, the average and the longest case.
- `avg-td-e` **Avg. Time Diff. between Consecutive Events**: the mean gap between consecutive events within a case, averaged over cases, shown in seconds, minutes, hours or days.

### Variation

- `#-acyclic-paths` **Number of Acyclic Paths**: an estimate of the routes through the process, `10^(0.08·(1 + e − v))` with e distinct directly-follows connections and v distinct activities (Hærem et al., 2015). From a million upwards it is shown in exponent notation, such as `1.23e+7`, and when it is too large to be represented at all, as `≈10^` followed by the exponent.
- `#-ties` **Number of Ties**: the sum of the lengths of the paths from the root of the prefix automaton to each of its end nodes. In effect it is the total length of the variants, leaving out a variant that is only the beginning of a longer one.
- `lempel-ziv` **Lempel-Ziv Complexity**: the number of phrases needed to describe the stream of all events in time order without repetition. Repetitive behaviour needs few, random-looking behaviour many.
- `#-unique-seq` **Number of Unique Sequences**: the number of distinct variants, that is of different activity sequences. `perc-unique-seq` divides it by the number of cases and shows the result in percent with two decimals. 100% means every case took its own path, and 25% means that each path is taken by four cases on average.
- `avg-distinct-e` **Avg. Distinct Events per Sequence**: the average number of distinct activities per case.
- `order_var` **Order Variation**: how often an event is followed by a different activity within its case, divided by the number of events (Lindberg et al., 2016).
- `activity-var` **Activity Variation**: the Shannon entropy of the shares of the activities among all events. It is largest when every activity occurs equally often, and low when a few dominate.

### Distance

- `affinity` **Average Affinity**: the average similarity of the directly-follows patterns of two cases, over all pairs of cases. Homogeneous logs score high, and it shows "–" with fewer than two cases.
- `structure` **Structure**: 1 minus the distinct directly-follows connections divided by the square of the number of activities. Near 1 the behaviour is sparse and structured, near 0 almost any activity can follow any other.
- `dev-random` **Deviation from Random**: 1 minus the distance between the shares of transitions and an even spread over all pairs of activities. Close to 1 the transitions are spread almost evenly, like random behaviour.
- `avg-edit-distance` **Average Edit Distance**: the mean Levenshtein distance between two cases, the number of insertions, deletions and replacements needed to turn one activity sequence into the other (Pentland, 2003).
- `structural-var` **Structural Process Variety**: the sum of the merge heights when the variants are clustered by their Levenshtein distance (Schreiber and Abbad-Andaloussi, 2024).

The two distance metrics at the end use only the most frequent variants, up to the **Distance-metric variant cap**. When a log has more variants, or very long traces, they are computed on the most frequent ones only, and the page does not tell you when that happens.

### Probability of action pairs

The heatmap below the tables, labelled `prob-act-pairs`, is the directly-follows transition matrix, in which every row sums to 1 (Grisold et al., 2022). The cell in a row and a column is the probability that the activity of the column follows the activity of the row. Rows are numbered and named, the activity names of the columns run vertically in the same order, a darker cell is a higher probability, and hovering a cell shows the percentage. A row of an activity that is never followed by a shown activity stays empty. A note reads "Showing the 25 most frequent activities." when the log has more, and "No transitions to display." when there are none. Read it for qualitative questions, such as which activity is followed by many different ones, rather than as a score.

With more than 25 activities the matrix covers only the 25 most frequent ones, and each row is scaled to sum to 1 over those. A probability then means the chance of a shown activity coming next, not of any activity.

### Dashboard cards

Cards follow the filters of their dashboard. Each has an info icon with a short explanation and an arrow (**Open in module**) that opens this page.

- **Complexity suite (v2)**: up to eight tiles: `#-e`, `#-et`, `perc-unique-seq`, `nseq-e`, `activity-var`, `avg-edit-distance`, `structural-var` and `#-acyclic-paths`, each with an info icon. Until you choose, a card shows all eight. The **Figures** list in the card's settings has one checkbox per tile, named Events, Event types, Unique sequences, Normalized sequence entropy, Activity variation, Avg edit distance, Structural variation and Acyclic paths. It starts with the last three unticked, and once you change any box the card shows exactly the ticked tiles.
- **Complexity metrics by category**: every metric, grouped by category, as a list of label, name and value.

## Tips and limits

- The metrics capture different aspects and can disagree. Read them together instead of picking one, and compare two logs on the same metric.
- When logs differ in size, compare the normalised entropies and the ratio measures, such as `perc-unique-seq`, `structure` and `affinity`, instead of the raw sizes and raw entropies.
- On a log with many variants the cap decides how faithful the two distance metrics are. Raise it for a closer value on the whole log, and lower it when the page is slow to compute.
- On logs with more than 4,000 distinct directly-follows patterns, `affinity` uses the most frequent ones and is an approximation.
- `lempel-ziv` runs over all events in time order as one stream, so cases that overlap in time influence each other.
- One missing XES attribute is enough to turn the four enriched entropies to "–". The plain entropies still describe the activity sequences.
- The metrics describe the log; they do not say whether a process is good or bad. For the structure of the process use Discovery.

## Method

The suite implements the metrics of Langer's thesis on business process complexity, which compiles the measures of Günther (2009), van der Aalst (2016), Pentland (2003), Hærem et al. (2015), Lindberg et al. (2016), Grisold et al. (2022) and Schreiber and Abbad-Andaloussi (2024), and the entropies of Augusto et al. (2022). The formal citations are in the info popover next to the module name.

The entropies are graph entropies of the Extended Prefix Automaton, `|S|·log|S|` minus the sum of `|p|·log|p|` over its state partitions p, and the sequence entropy counts events instead of states. The distance metrics build one Levenshtein matrix over the kept variants and take the weighted mean of its entries and the merge heights of an average-linkage agglomerative clustering. The transition matrix divides the number of directly-follows transitions from one activity to another by all transitions out of the first.
