# How to use Actor Performance

Actor Performance explains why work waits between two steps of a case. For every step and its next step it classifies how the work moved between people: the same actor carried on, the same actor did something else in between, or another actor took over, who was idle, busy or doing other work first. It then shows how much waiting time each kind of behaviour contributes.

## When to use it

- A transition is slow and the averages do not say why. Here you see whether the wait comes from handovers that sit idle in a queue, from actors who interrupt their own work, or from actors who did other work first.
- You want to separate a handover problem from a capacity problem before you change staffing or rules.
- You want the waiting times of the steps of a process, the cycle time of cases or the bottlenecks without the actor view: use Performance.

## Before you start

- The module needs a case-centric event log with `case_id`, `activity`, `timestamp` and `resource` columns, at least 100 events and at least 5 cases. A `lifecycle` column is used when the log has one. Otherwise the module's card in the process's module list shows an **Unavailable** badge and cannot be opened; hover the card to read the reason.
- The analysis runs in a Neo4j graph engine. It is a separate server that is not part of MATE, and all users share it. An administrator switches it on with the `graph` profile of the Docker stack, or on a development machine with the `docker run` command that the panel shows. While the engine cannot be reached, the panel shows "Graph sidecar not running" with these instructions, checks again every few seconds, and keeps the run button off. A wrong password shows "Neo4j rejected the credentials".
- Nothing is precomputed. A run starts only when you press **Run analysis**, as a background job with progress messages, and the panel shows the result of your last run and when it ran. Only one analysis runs at a time, across all users, so yours can wait in line.
- Events without a resource are left out, because the analysis needs to know who did each step. The panel says how many were excluded.
- A filter bar above the panel (**Add filter** and a time range you confirm with **Apply**) narrows the events of the next run. Results are kept for each filter on its own and show only while the same filter is on. The filter is temporary: reloading the page clears it.
- Everything stays on your servers: the graph engine belongs to MATE, the graph is cleared before and after every run, and the results are kept only for your account. If an administrator points the module at a remote Neo4j server, the data goes there.

## Settings

### In the panel

Two fields sit under the title. Pressing the run button saves their values, so the next run starts with them. After a run, a badge "Re-run to apply" appears while the fields differ from the result on screen.

| Setting | Default | What it changes |
| --- | --- | --- |
| Min. transition frequency | 0 | Only transitions that occur at least this often are analysed. 0 keeps every transition. On a log with many rare transitions, a higher value shortens the run. |
| Max. transitions | 150 | The most transitions that are analysed (10 to 1000), the most frequent first. When the run reaches the cap, the tile **Transitions** shows a "capped" badge. |

The button reads **Run analysis** first, **Re-run** once a result exists and "Running…" during a run. While it runs, the panel shows the current stage, a percentage and a bar. The stages are: checking the graph engine, loading the log, building the graph, identifying task instances, classifying actor behaviour and decomposing the transitions. Cancel a run in the **Jobs** drawer of the sidebar.

### On the module page

Open **Module Settings** in the sidebar and choose this module. The **Configuration** card has six fields. Save them with **Save configuration**. An administrator can lock the card, which makes it read-only. The first two fields below repeat the panel's fields and start from the same values.

| Setting | Default | What it changes |
| --- | --- | --- |
| Minimum transition frequency | 0 | The same as **Min. transition frequency** in the panel, from 0 to 100000. The paper used 1000 on the full BPIC17 log. |
| Max transitions to extract | 150 | The same as **Max. transitions** in the panel. |
| Neo4j bolt URI | empty | The address of the graph engine. Empty means the platform's default, which is `bolt://neo4j:7687` on the Docker stack, and else `bolt://localhost:7687`. |
| Neo4j user | `neo4j` | The user name for the graph engine. |
| Neo4j password | empty | The password for the graph engine. Empty uses the platform's default. |
| Shared import directory | empty | The folder that MATE writes the prepared log to and that the Neo4j server reads it from. Both must see the same folder. Empty means the platform's default, `/app/data/neo4j-import` on the Docker stack, and else `./data/neo4j-import`. |

The connection fields rarely need a change. A wrong value makes the next run fail with a message that names the address or the folder.

## Reading the results

Before the first run, the panel explains the classes in a card "What the classes mean". After a run it shows six tiles, the behaviour mix and the transitions.

### The behaviour classes

Each transition, a step followed by the next step of the same case, falls into exactly one class.

- **Continuation**: the same actor continued directly with the next step, with nothing else in between in their own work.
- **Interruption**: the same actor did both steps but worked on something else in between.
- **Handover (idle)**: another actor took over, and they had finished their previous task before the work became available, or it is the first work of that actor in the log.
- **Handover (prioritized)**: another actor took over, and they were in the middle of another task when the work became available. They did this step as the next piece of their work.
- **Handover (deprioritized)**: another actor took over, but only after they had started other work that began after the work became available.
- **Unclassified**: a transition that no class matched. It should be rare.

The wait of a transition is the time between the timestamps of the two events.

### The tiles

**Events**, **Cases** and **Actors** count what the run used, after the events without a resource were left out. **Task instances** counts the stretches of work in the graph, where a stretch is a run of events that one actor performs on one case without doing anything else in between. **Transitions** counts the transitions that were analysed, and a "capped" badge means the run reached **Max. transitions**. **Runtime** is the duration of the run. A line above the mix says how many events were excluded for lack of a resource.

### Behavior mix across the log

A bar shows the share of each class among all analysed transitions. Under it, one card per class that occurs gives the number of transitions, their share in percent, a bar for the mean wait and the mean wait as a duration. Hover a card to read the class description. The mix covers the analysed transitions only, so a minimum frequency or the cap leaves the rare transitions out of it.

### Transitions

One row per analysed transition, written as the first activity, an arrow and the next activity. When the log has a lifecycle column, each activity carries its transition after a dot, such as `·complete`. A row shows its **Count**, its **Mean wait** and a small bar with the behaviour mix. Click a row to open one card per class with its count and share, and the mean, the median and the 90th percentile of the wait.

The box "Filter activities…" narrows the list to transitions whose activities contain your text. The menu sorts by "Most frequent", the default, or "Slowest (mean)". At most 150 rows show at a time, and a note says how many more there are.

## Tips and limits

- Read the share and the mean wait of a class together. A rare class with a huge wait means something other than a common class that is only a little slow.
- The wait is the difference between two timestamps. In a log with only completion times it includes the time the next step took. With start and complete events, look at the rows that go from a complete event to a start event for the pure waiting.
- Continuation and interruption involve one actor only, so their waits are not handover queues: the actor did the next step themselves, directly or after other work.
- Use **Min. transition frequency** and **Max. transitions** to keep a run short on a log with many different transitions. Large logs also need much memory for the graph engine: about 2 GB of heap for small and medium logs, and about 10 GB for a log of over a million events. An administrator sets it.
- Each run replaces the stored result for its filter. To compare two settings, note the numbers before you run again.
- A run that fails says why, for example that the graph engine cannot be reached, that the import folder cannot be written, or that no events were left after leaving out those without a resource.

## Method

The module follows Klijn, Tentina, Fahland and Mannhardt (2024), who decompose waiting times by actor behaviour. A run writes the log, with the events that have a resource, to a file for the graph engine, builds an Event Knowledge Graph of the cases and the actors in Neo4j with the PromG library, and groups the events into task instances. A task instance is a run of events that one actor performs on one case and that follow each other in the actor's own work.

It then labels every directly-follows step of a case with one class, by comparing the events and the task instances of the two actors. The labels are applied in this order, and a later label replaces an earlier one: continuation, interruption, handover idle, handover prioritized and handover deprioritized. Finally it reads the wait of every step, the difference of the two timestamps, and gives the count, the share, the mean, the median and the 90th percentile for each transition and class. The log-wide mix weights each transition by its number of steps. The graph is cleared before and after every run, so it holds no data between runs. The formal citation is in the info popover next to the module name.
