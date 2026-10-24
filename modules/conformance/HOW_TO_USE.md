# How to use Conformance Checking

Conformance Checking replays your event log against a reference BPMN model that you upload. You get the fitness and precision of the model, the share of cases that follow it, and the places where cases deviate, painted onto the diagram.

## When to use it

- You have a model of how the process should run, such as a designed process, a policy or a standard, and you want to know how closely the recorded cases follow it.
- You want to find where reality departs from it: the activities and the variants with the most deviations.
- You have no model yet. Open the BPMN view of Discovery, save the diagram with **Download BPMN** and upload it here, or draw your own. A mined model mainly reflects the log it came from, so a compliance check is most useful with a model that states what should happen.
- You want to compare two groups of cases or see how the process runs without a model. Use Process Comparison or Discovery.

## Before you start

- The module needs a case-centric event log with `case_id`, `activity` and `timestamp` columns, at least 50 events and at least 2 cases. Otherwise the module's card in the process's module list shows an **Unavailable** badge and cannot be opened; hover the card to read the reason.
- You need a reference model: a BPMN 2.0 file ending in `.bpmn` or `.xml` with at least one labelled task. MATE refuses a file it cannot read ("Not a readable BPMN model") or one without labelled tasks. The model belongs to this log and your account; it is not shared with other logs.
- Task names in the model must match the activity names in the log. They are compared exactly after trimming spaces, collapsing repeated spaces and ignoring upper and lower case. There is no fuzzy matching, so one wrong letter makes a different activity.
- Nothing is precomputed. A check runs when you start it, as a background job with progress messages, and a freshly uploaded model starts one automatically.
- This page has no filter bar. A filter applied on the log's **Events** tab narrows the events that are replayed.

## Settings

### In the panel

The **Reference model** card holds the model controls and the technique.

- **Upload BPMN** adds a model. It becomes the active model and a run starts at once, and a message confirms it with the number of tasks, for example `Reference model "order.bpmn" ready (14 tasks)`. A file with the name of an existing model replaces it. File names are reduced to letters, digits, dots, hyphens and underscores.
- **Run conformance** appears once a model exists and starts a check with the chosen technique. It reads "Running…" while a run is in progress.
- The list shows every uploaded model with its size and an **active** badge. Choose one to make it active. The trash icon deletes a model from this log after you confirm.

| Setting | Default | What it changes |
| --- | --- | --- |
| **Technique** | Token-based replay | How cases are replayed: **Token-based replay** or **Alignments (precise)**. It is locked while a run is in progress. Results are kept per model and technique, so switching to a combination you have not run shows "Reference model ready" and a **Run conformance** button. |
| **Heatmap** | On | In the diagram's **Graph settings**: colours the tasks by their deviations. |
| **Deviation counts** | On | In the diagram's **Graph settings**: puts a count badge on the tasks. |

### On the module page

Open **Module Settings** in the sidebar and choose this module. The values in its **Configuration** card apply to the next run, and saving does not start one. An administrator can lock the card, which makes it read-only. Save with **Save configuration**.

| Setting | Default | What it changes |
| --- | --- | --- |
| Conformance technique | Token-based replay | The technique used when a request does not name one: the dashboard cards and the MATE AI summary. The panel always starts on Token-based replay and sends its own choice. |
| Conforming-trace threshold | 1.0 | A case counts as conforming when its fitness is at or above this value (0 to 1 in steps of 0.05). It sets the **Conforming traces** tile. |
| Precision – max events | 150000 | Above this many events the token-replay precision is skipped (0 to 5000000). Set 0 to always compute it. |
| Alignments – max cases | 2000 | Alignments are refused above this many cases (100 to 100000). |
| Alignments – max variants | 300 | Alignments are refused above this many distinct variants (50 to 20000). |

## Reading the results

### The label notice

An amber notice reading "Activity names don't fully line up" appears when the model and the log disagree about activity names. It lists the model tasks that never occur in the log, which always show as deviations, and the log activities that are not in the model. Check it first: a naming problem is the most common reason for a result that looks alarmingly bad. Rename the tasks in the model or the activities in the log to make them match.

### The four tiles

- **Fitness**: how much of the recorded behaviour the model can replay, up to 100%. With token replay it combines the tokens the replay had to add and the tokens it left over; with alignments it is the alignment-based fitness.
- **Precision**: how little behaviour the model allows beyond what happened. A high fitness with a low precision means a permissive model, not a clean process. When precision is skipped, the tile shows "–" and says why.
- **Conforming traces**: the share of cases with a fitness at or above the threshold.
- **Deviations**: the number of steps where the recorded process differs from the reference model, either because an activity happened that the model does not allow or because a required activity was skipped. Hover the tile for the same definition.

### Deviations on the model

The reference diagram with the result painted onto its tasks. Green marks a task the log replays without deviations, and a red scale in five steps marks deviating tasks, darker for more deviations. An amber dashed task is in the model but never in the log, which usually means a name mismatch. With **Deviation counts** on, a badge shows the number of deviations at a task, and "not in log" on such an amber task. With alignments it reads `+2 −1`: 2 steps happened in the log but are not allowed by the model, and 1 step is required by the model but skipped in the log. A legend under the diagram explains the colours and shows only what is switched on.

The diagram has a toolbar in its top right corner with **Zoom out**, **Zoom in**, **Fit to view**, **Graph settings** and **Fullscreen**.

### Most deviating activities

A bar chart of the ten activities with the most deviations. It is left out when there are none.

### Deviation breakdown

Two tabs under the explanation of a deviation.

- **By activity**: one row per activity with **Deviations**, **Cases** (the cases with at least one deviation there) and a severity bar relative to the worst activity. An activity that occurs in the log links to its activity view, and a model task that never occurs shows the "not in log" badge. With alignments two more columns split the deviations into **In log, not in model** and **In model, skipped in log**. Token replay does not make that split.
- **By variant**: one row per sequence of activities in the log, the most frequent first, with its number of steps, **Cases**, **Avg fitness** with three decimals and **Deviations**. Click a row to expand the sequence, and use the arrow that appears on hover to open the variant view.

### Dashboard cards

Cards follow the filters of their dashboard. Each has an info icon with a short explanation and an arrow (**Open in module**) that opens this page. They show the latest result for the active model and the technique set in the module's configuration, and ask you to run a check when there is none.

- **Conformance score**: the tiles Fitness, Precision, Conforming and Deviations.
- **Top deviations**: a bar chart of the activities with the most deviations. **Activities shown** (3 to 25, default 10) sets how many bars it draws.

The module also offers the datasets **Conformance KPIs** and **Per-activity deviations** in the **Cards & data** palette of a dashboard.

## Tips and limits

- Start with token replay. It is fast. Alignments give more precise diagnostics but can take very long on large or varied logs, so MATE refuses them above the limits in the configuration. Then the run fails with "Alignments refused: log has N cases and M variants", and you can switch the technique or raise the limits.
- On a large log the precision is skipped above the event limit, and fitness and deviations are still computed. Raise **Precision – max events** to compute it anyway, at the cost of memory.
- A token-replay deviation is counted at the model task where the replay had trouble, and an alignment deviation at the activity that was added or skipped, so the two techniques do not give identical counts.
- Only labelled tasks are matched. Gateways and events of the model are not coloured.
- A check says how the log relates to the model, not which of them is right. Look at the deviating variants before you change the model or the process.
- To see how the process actually runs before you check it, use Discovery.

## Method

The module reads the BPMN model, converts it to a Petri net with PM4Py and replays the log against it. Token-based replay follows Rozinat and van der Aalst (2008): every case is replayed on the net, and missing and remaining tokens measure its deviations. The log fitness combines them as 0.5 times one minus the missing tokens over the consumed tokens, plus 0.5 times one minus the remaining tokens over the produced tokens. Alignments, as surveyed by Carmona, van Dongen and Weidlich (2022), instead find the cheapest way to match each case to a run of the model, where a step in the log only or in the model only is a deviation.

Precision measures how much of the model's behaviour the log actually uses. Activity names are unified with the model's spelling before the replay, and the diagram colours come from the per-activity deviation counts. The formal citations are in the info popover next to the module name.
