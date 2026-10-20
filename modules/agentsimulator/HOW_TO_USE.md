# How to use AgentSimulator

AgentSimulator learns from your log how the people and systems in your process work: who does which activity, when they work, how long it takes and who hands work to whom. It then generates synthetic logs of the process and scores how closely they match a held-out part of your real log. Use it to see how well the behaviour of the process can be reproduced, and to get simulated logs.

## When to use it

- You want to know whether a model of agents with working calendars and handovers can reproduce your process, and in which respect it falls short: the order of activities, the timing or the congestion.
- You want to compare the two ways of handing over work, autonomous or centrally orchestrated, by how well each reproduces the real log.
- You want synthetic logs that follow the behaviour of the real process, for tools outside MATE.
- You want to understand the real waiting times or who hands over to whom: use Performance, or Actor Performance for the actor view. AgentSimulator reproduces the process as it was learned and has no setting that changes staffing, calendars or rules.

## Before you start

- The module needs a case-centric event log with `case_id`, `activity`, `timestamp` and `resource` columns, at least 200 events and at least 20 cases. An `end_timestamp` column is used when the log has one. Otherwise the module's card in the process's module list shows an **Unavailable** badge and cannot be opened; hover the card to read the reason.
- Most logs have one timestamp per event. Then MATE takes the gap to the next event of the case as the duration of an activity, so the working time and the waiting time cannot be told apart, while the duration of whole cases stays exact. With real end timestamps the durations are used as they are.
- Events without a resource count as one agent called `undefined`. Events without a case, an activity or a start time are left out.
- Nothing is precomputed. A run starts when you press **Run simulation**, here or on a dashboard card, as a background job with progress messages. It can take several minutes, and the panel shows the result of the last run.
- This page has no filter bar, and the filters of a dashboard do not apply to it. A filter applied on the log's **Events** tab narrows the events that the simulation reads.
- Everything runs on your MATE server, and nothing is sent to an outside service.

## Settings

### In the panel

Four options sit under the title. Pressing the run button saves them, and a dashboard run uses the saved values. After a run, a badge "Settings changed" appears while the options differ from the result on screen. The same four fields are on the module page.

| Setting | Default | What it changes |
| --- | --- | --- |
| Simulated logs | 5 | How many synthetic logs are generated (1 to 10). They are repetitions of the same model, and the simulation is random, so more runs give a steadier fidelity score and take longer. |
| Determine configuration automatically | Off | The module simulates a validation slice of its learning data, the last fifth of the cases by case ID, with all four combinations of handover type and extraneous delays and keeps the one whose case durations are closest to the real ones. This is the most accurate and much slower. While it is on, the next two switches are greyed out and ignored. |
| Central orchestration | Off | Off means autonomous: every agent decides on its own to whom it hands the work over, following the handovers seen in the log. On means orchestrated: a central orchestrator assigns each next activity to a suitable agent. |
| Discover extraneous delays | Off | Also models waiting time that the availability of the resources does not explain. This is slower. |

The button reads **Run simulation** first, **Re-run** once a result exists and "Running…" during a run, with the current stage and a bar. Cancel a run in the **Jobs** drawer of the sidebar.

### On the module page

Open **Module Settings** in the sidebar and choose this module. The **Configuration** card has the four fields above, and **Save configuration** saves them. An administrator can lock the card, which makes it read-only.

## Reading the results

Before the first run, the page reads "No simulation yet" with a **Run simulation** button. After a run, it shows three cards.

### Simulation fidelity

The badges name the mode, `autonomous`, `orchestrated` or `main_results` for the automatic choice, the number of logs and the duration of the run. Five tiles score the simulated logs against the test log. Each is a distance, so lower is better, and each shows the mean over the simulated logs, the standard deviation and an info icon.

- **NGD**, the N-Gram Distribution: how different the frequencies of every three consecutive activities are, from 0 for the same order of work to 1.
- **AEDD**, the Absolute Event Distribution: how different the times of the events on the calendar are, in hours.
- **CEDD**, the Circadian Event Distribution: how different the times of day of the events are, in hours. It checks the working hours.
- **REDD**, the Relative Event Distribution: how different the times of the events measured from the start of their case are, in hours.
- **CTDD**, the Cycle Time Distribution: how different the durations of whole cases are, in hours. It reflects the processing times together with the congestion.

NGD is a total-variation distance, and the other four are Earth Mover's distances. Below the tiles, four figures give the number of **Test cases** and their events, the average number of cases per simulated log (**Sim cases / run**) and the median cycle time of the real and of the simulated logs in hours.

The real log in all comparisons is the test log, roughly the newest fifth of the events of your log. The model learns from the earlier part, see Method.

### Real vs simulated distributions

Five tabs compare the test log with the average of the simulated logs.

- **Cycle time**: how many cases fall into each of 20 duration classes, the last of which takes all the longest cases. A caption gives the median and the 90th percentile of both.
- **Arrivals**: new cases started per day, or per week when the span exceeds 90 days. Each log counts from its own first case, so a different start date does not matter.
- **Activity mix**: how often each of the 12 most frequent activities of the test log occurs, per log.
- **Time of day**: events by hour of the day, in UTC. It shows whether the simulation respects working hours.
- **Handovers**: two tables of the probability that work passes from one resource to another, for the real log and for the simulation. They cover the eight busiest resources of the test log, and a darker cell is a higher probability. Each cell shows a percentage, hovering gives the exact value, and long names are cut. The rows are scaled to sum to 1 among these eight.

### Simulated logs

One button per run, such as "Run 1 · 120 cases", opens a dialog where you choose **CSV** or **XES** for that run. A download holds at most the first 20,000 events of the run. The CSV has the columns `case_id`, `activity`, `resource`, `start` and `end`, and the XES file carries the end time as the event time and the start time as an extra attribute. Below the buttons, a table previews the first 50 events of run 1.

### Dashboard cards

Each card has an info icon with a short explanation and an arrow (**Open in module**) that opens this page. The cards ignore the filters of their dashboard, because the result belongs to the whole log.

- **Simulation fidelity**: the five tiles of the panel.
- **Cycle time – real vs simulated**: the duration classes of the cycle time tab.
- **Case arrivals – real vs simulated**: the arrivals chart, with its days or weeks numbered from the start.

A card without a result reads "No simulation yet for this log". Its **Run simulation** button starts a run with the saved options, and **Configure** opens this page. While a run is going, whoever started it, such a card shows the progress, and it also says when the last run failed or was cancelled.

## Tips and limits

- Use the scores to compare configurations on one log. The time measures are in hours, so their size depends on how long cases and periods are in your process, and values from different logs do not compare.
- Raise **Simulated logs** for a steadier score, and expect a longer run. The automatic choice is the slowest, so try autonomous and orchestrated first if time is short.
- The test log is the newest fifth of the events. If the process changed in that period, the model learned the old behaviour, and the distances grow.
- With a single timestamp per event, the simulated cases have realistic lengths, but the split of working and waiting time is not reliable.
- The simulated logs contain no real cases, but they carry the real activity names and the behaviour learned from the log. Check them before you share them.
- A run that fails reports the reason, and the end of the simulator's output is part of the message when it produced no logs.
- MATE computes the five distances itself with numpy and scipy, in the way the paper defines them. The values can differ slightly from the paper's reference code.

## Method

The module follows Kirchdorfer, Blümel, Kampik, van der Aa and Stuckenschmidt (2024). It first brings the log into one table of case, activity, resource, start and end. The events are ordered by their end time, the first 80% are the learning data and the last 20% are the test log. A case that straddles the cut is left out of both parts. The last fifth of the learning cases, in the order of their case IDs, is kept as a validation log.

From the learning data it discovers one agent per resource, with roles and working calendars, the distribution of the duration of each activity per agent, the probabilities of the next activity and of the handover between agents, the activities that must come first, the largest number of repetitions of an activity in a case and the arrival process of cases. It then simulates the process with the agents, as a model with agents, over the period of the test log. A simulated log has as many cases as the test log, and the simulation is repeated for the chosen number of logs.

With the automatic choice, the module simulates the validation log in all four combinations and keeps the one with the smallest cycle-time distance. The five scores compare each simulated log with the test log, and the panel shows their mean and standard deviation. The formal citation is in the info popover next to the module name.
