# How to use Discovery

Discovery mines your event log into process models. It shows how cases actually run as a directly-follows graph (DFG), a Petri net, a process tree, a prefix tree, a heuristics net and a BPMN diagram, so you can see the real flow of work, its most frequent paths and its rare detours.

## When to use it

- You want to see how the process really runs: which activities occur, in which order and how often. The DFG is the place to start.
- You need a formal model, such as a Petri net, a process tree or a BPMN file, for example as a reference model for Conformance Checking.
- You want timing. Discovery only shows the mean time between two activities on the DFG; for cycle times and bottlenecks use Performance, and to compare two groups of cases use Process Comparison.
- Your events refer to several object types at once (orders, items, invoices). On an object-centric log Discovery's card is greyed out as unavailable; use Object-Centric Discovery.

## Before you start

- The log must be case-centric, with `case_id`, `activity` and `timestamp` columns, at least 50 events and at least 2 cases. Otherwise the module's card in the process's module list shows an **Unavailable** badge and cannot be opened; hover the card to read the reason.
- Nothing else is needed: no model to upload, no API key and no external service. Every model is mined from your events inside MATE.
- MATE computes Discovery in the background right after you import a log (an administrator can change this), so it is usually ready when you open it. If it is not, you see "Computing Discovery…" with a progress bar and a **Cancel** button. The run happens once; next time it is instant. A failed or cancelled run is reported on the page with a **Retry** or **Run** button.
- Everything else is mined when you first choose it: the ILP Miner, IM Infrequent at a noise threshold other than 0.2, the Heuristics Net at thresholds other than its defaults, the BPMN **Frequency filter** above 0, and the Backbone and Layered DFG layouts. The ILP Miner is refused on logs with more than 30 distinct activities or more than 5,000 cases.

## Settings

Every view is a canvas with a toolbar in its top right corner: **Zoom out**, **Zoom in**, **Fit to view**, **Graph settings** (the sliders icon), **Reset layout** and **Fullscreen**. The tables below list what **Graph settings** holds for each view. The Prefix Tree has no settings. A mini-map appears at the bottom right while you pan or zoom.

Cards you drag stay where you drop them until you press **Reset layout** and confirm. On the DFG that clears the moved cards of every Discovery view for the log; on the other views only that view's. The BPMN diagram cannot be dragged, and its reset button is called **Reset view**.

### In the panel

#### DFG

| Setting | Default | What it changes |
| --- | --- | --- |
| **Activities** | Auto | A slider that keeps the most frequent activities, from the single most frequent (left) to all (right). Its read-out shows visible / total, for example 8 / 16. **Auto** keeps activities up to the knee of the cumulative frequency curve and never fewer than 5 (or all, when there are fewer). Dragging the slider turns Auto off; click **Auto** to go back. |
| **Connections** | Auto | The same for connections between the visible activities. Even at the far left, the most frequent connections needed to keep every visible activity reachable stay. With **Auto** the cut-off is the knee of the frequency curve of all connections. |
| **Layout** | Process flow (Celonis) | Where the cards go: **Process flow (Celonis)**, **Backbone (optimized)**, **Backbone (smooth routing)** or **Layered (Sugiyama)**. See the DFG section below. |
| **Edge label** | Count | What each connection is labelled with: **Count**, **Duration** (the mean time from one activity to the next) or **Off**. |

#### Petri Net

| Setting | Default | What it changes |
| --- | --- | --- |
| **Algorithm** | Inductive Miner | Which algorithm mines the net: **Inductive Miner**, **IM Infrequent**, **ILP Miner (heavy)**, **Alpha Miner** or **Alpha+ Miner**. |
| **Noise threshold** | 0.2 | Only shown for **IM Infrequent**. A slider from 0 to 1 in steps of 0.05; higher values drop more infrequent behaviour. The net is mined again when you release the slider. |
| **Layout** | LR | Reading direction: **LR** (left to right) or **TB** (top to bottom). Switching clears moved nodes. |
| **Merge arcs** | Off | Arcs leaving a place share a trunk and branch late, instead of each leaving on its own. |
| **Show invisible (τ)** | On | Shows or hides the silent transitions together with their arcs. Hiding them can leave parts of the net looking disconnected. |
| **Highlight markings** | On | Rings the initial and final places. |
| **Arc weights** | On | Labels arcs whose weight is above 1. |
| **Transition label** | Activity | **Activity** shows the activity name, **ID** the transition's internal name, **Both** shows both. |
| **Place mode** | Rings | **Rings** draws no token dots; **Count** puts a dot in every place that holds a token (the initial place). |

#### Process Tree

| Setting | Default | What it changes |
| --- | --- | --- |
| **Algorithm** | Inductive Miner | **Inductive Miner** or **IM Infrequent**. |
| **Noise threshold** | 0.2 | Only shown for **IM Infrequent**. A slider from 0 to 1 in steps of 0.05, applied when you release it. |
| **Orientation** | Vertical | **Vertical** or **Horizontal** layout of the tree. |
| **Fold τ leaves** | Off | Hides the silent τ leaves. |
| **Max depth** | All | Shows the root and this many levels below it: **All**, 2, 3, 4, 6 or 8. |

#### Heuristics Net

| Setting | Default | What it changes |
| --- | --- | --- |
| **Dependency** | 0.5 | A slider from 0 to 1 in steps of 0.05: the dependency threshold handed to the Heuristics miner. The net is requested again when you release the slider. |
| **AND** | 0.65 | The same for the AND threshold. |
| **Loop-2** | 0.5 | The same for the threshold for loops of length two. |
| **Edge label** | Both | What each arc is labelled with: **Dependency** (the dependency score), **Count** or **Both** (count, then score). |
| **Hide rare arcs** | Off | Hides arcs whose count is below 10% of the busiest arc's count. Activities stay on screen. |

> [!WARNING] In this version the three thresholds do not change what is drawn. The Heuristics Net always shows every directly-follows arc of the log, with its dependency score, whatever **Dependency**, **AND** and **Loop-2** are set to. Use **Hide rare arcs** to thin the map.

#### BPMN

| Setting | Default | What it changes |
| --- | --- | --- |
| **Frequency filter** | 0 | A slider from 0 to 0.5 in steps of 0.05. At 0 the diagram comes from the Inductive Miner and keeps every activity; above 0 it is mined again with IM Infrequent at that noise threshold, which drops the least-used behaviour. Applied when you release the slider. |
| **Heatmap** | On | Shades tasks, and connections that join two tasks directly, by how often they occur. A legend appears at the top left. |
| **Frequency labels** | On | Puts a count badge on every task. |
| **Find** | Empty | Type part of an activity name and press Enter or the search button. MATE centres the first task whose name contains the text, ignoring case, and outlines it for about two seconds. If nothing matches, a notice says so. |
| **Download BPMN** | – | Saves the diagram as `process.bpmn`. The file always holds the Inductive Miner model, even when the **Frequency filter** is above 0, and has no diagram coordinates, so the layout you see is not part of it. |

### On the module page

Open **Module Settings** in the sidebar and choose Discovery, change the values in its **Configuration** card and press **Save configuration**. An administrator can lock the card, which makes it read-only. Changes apply to what is computed afterwards; results that already exist are not recomputed.

| Setting | Default | What it changes |
| --- | --- | --- |
| **Dependency threshold** | 0.5 | Starting value of the Heuristics miner's dependency threshold (a slider from 0 to 1 in steps of 0.05). Used by the background run and by requests that carry no value. The Heuristics Net tab sends its own **Dependency** value, so it does not read this one. |
| **AND threshold** | 0.65 | The same for the threshold above which two outgoing arcs of an activity count as concurrent. |
| **Loop-two threshold** | 0.5 | The same for detecting loops of length two (a, b, a). |
| **Layout solver time limit** | 10 s | How long the optimizer of the Backbone layouts may search, from 1 to 60 seconds in steps of 1. When time runs out the best layout found so far is used. |
| **Horizontal edges** | On | Lets two activities that follow each other in both directions share a row in the Backbone layouts. Off forces a strict layering. |
| **Edge-length weight** | 1 | A slider from 0.1 to 10 in steps of 0.1. Higher values fight long and upward connections harder in the Backbone layouts. |
| **Compactness weight** | 1 | A slider from 0.1 to 10 in steps of 0.1. The weight of map height in the Backbone layouts; higher values favour a shorter map. |

The four layout options only affect **Backbone (optimized)** and **Backbone (smooth routing)**, and only for maps of up to 250 nodes; larger maps are ranked with a faster heuristic. **Process flow (Celonis)** and **Layered (Sugiyama)** ignore them.

## Reading the results

### DFG

The directly-follows graph shows which activity directly follows which. Each card is an activity with its name, a dot that grows with its share of the busiest activity, and its count (shown as 1K, 36.7K or 1.2M for large numbers). Each arrow is a directly-follows connection, labelled with its count (for example # 1.2K) and drawn thicker the more often it occurs. A **Start** marker at the top and an **End** marker at the bottom show how many cases begin and end at the activities that are currently visible.

On first open the map is simplified: **Activities** and **Connections** are on **Auto**, so rare activities and connections are hidden. The read-outs next to the sliders tell you how much of the whole you see; move the sliders right to see more.

The layouts differ as follows:

- **Process flow (Celonis)** is computed in your browser. The most frequent path from start to end runs down the centre column, and loop-backs run in lanes at the sides.
- **Backbone (optimized)** is computed on the server. The most frequent variant forms a straight backbone down the middle, and an optimizer assigns every activity to a row.
- **Backbone (smooth routing)** places the cards the same way and routes the connections around the cards with rounded corners.
- **Layered (Sugiyama)** is a classic layered layout, used as a baseline.

In the three server layouts, connections that run upward (loop-backs) are drawn in red. The map keeps its previous layout until the new one arrives.

Click an activity or a connection to open its details on the right; click the empty background or the X to close them. The Start and End markers have no details. Right-click an activity for a menu with the same three links as the details panel. The details always describe the complete map, including activities and connections the sliders have hidden. For an activity they show:

- **Frequency**: **Events** (with its share of all events) and **Self-loop** (how often the activity directly repeats), when it does.
- **Role**: **Cases starting here** and **Cases ending here** with their share of all cases, or "Intermediate activity."
- **Connections**: the number of distinct **Incoming** and **Outgoing** connections and the events on them, without self-loops.
- **Top transitions**: the incoming and outgoing connections ranked by count, five each, with **Show all** to expand the list.
- **Explore**: **Open activity view**, **View performance metrics** and **Show variants with this activity** open the activity's page, the Performance module and the process's Variants tab for this activity.

For a connection the details show **Path** (From and To), **Frequency** (**Transitions** with their share of all transitions, **Of source events** and **Of target events**) and, when the log allows it, **Duration** (**Mean transition time**).

### Petri Net

A Petri net is a formal model. Circles are places, boxes are transitions (activities) and arrows are arcs between them. A case starts with a token in the initial place and ends in the final place; both carry a heavier ring. A dark box is a silent τ transition, a routing step that is not an activity. A place with several outgoing arcs is a choice; a box with several outgoing arcs starts parallel branches.

The net shows structure only: it carries no counts, and a number appears only on arcs with a weight above 1. Use the DFG for frequencies. Drag nodes to move them; changing a setting lays the net out again.

### Process Tree

A process tree describes the process as a hierarchy. Leaves are activities, and a dark filled leaf is a silent τ step. Inner nodes are operators that say how their children combine:

- **Sequence**: the children happen in the order they are drawn.
- **XOR**: exactly one of the children happens.
- **Parallel**: all children happen, in any order.
- **Loop**: a part of the process can repeat.
- **OR**: one or more of the children happen.

Read the tree from the root down. Use **Max depth** to look at the top levels first.

### Prefix Tree

The prefix tree shows how cases begin. The root, labelled **Start**, carries the number of cases. Every other node is an activity, and its number is how many cases begin with the sequence of activities on the path from the root to that node. Branches show where cases diverge. Every distinct beginning gets its own node, so the tree widens quickly when the log has many variants.

### Heuristics Net

The heuristics net is a dependency graph. Each card is an activity with its name and count; start and end activities carry a play or a square badge. Each arc is a directly-follows relation, labelled according to **Edge label** and drawn thicker for higher counts and more opaque for higher dependency scores.

The dependency score says how clearly one direction dominates. Close to 1 means the reverse hardly ever happens, around 0 means both directions are about equally common, and negative values appear on the weaker direction of a pair. You cannot click cards or arcs in this view.

### BPMN

The BPMN view is the process as a BPMN 2.0 diagram with tasks, gateways and start and end events. It is mined from your events and is view-only: there is no model to upload here. With **Heatmap** on, tasks and the connections that join two tasks directly are shaded in six steps, relative to the most frequent activity and the most frequent connection; connections that pass through a gateway stay neutral. A legend shows the scale and the highest activity count. The counts and shading come from the DFG of the complete log, not from the model left after the **Frequency filter**.

Below the diagram a note points to Conformance Checking for your own reference model.

### Dashboard cards

- **Process overview**: four figures about the discovered map, **Activities**, **Connections**, **Start activities** and **End activities**, each a count of distinct items. Choose which of them the card shows under **Figures** in the card inspector.
- **Activity frequency**: a bar chart of the most frequent activities. **Activities shown** sets how many bars it draws, from 3 to 25 (default 10).
- **Process map**: the DFG of the dashboard's filtered log. The card has its own display settings; changing them does not affect the module page.

## Tips and limits

- Start with the DFG on **Auto**. If the map is too busy, lower **Activities** and **Connections**; if a rare path is missing, raise them.
- The Backbone and Layered layouts refuse more than 400 nodes or 4,000 connections in view (the Start and End markers count too). MATE then shows "Server layout failed — showing Process flow (Celonis) instead." Lower the sliders to get under the limit.
- Changing the **Noise threshold** or the **Frequency filter** mines the model again. The previous model stays on screen with a short "Working…" or "Re-mining…" notice.
- If the ILP Miner is refused, the error replaces the net, and the settings button disappears with it. Switch to another view and back to return to the Inductive Miner.
- Algorithm choices, noise thresholds and the BPMN controls are forgotten when you leave a view. Everything else under **Graph settings**, and the nodes you moved, is remembered per log for your account.
- An activity's count on the DFG, its **Events** figure in the details and the bars of the Activity frequency card come from the graph: they count the times another activity follows it, raised to the number of cases that start or end with it when that is higher. For an activity that both ends some cases and is followed by others, they are lower than its number of events. Performance counts events exactly.
- Long activity names are cut at 32 characters on Petri net, process tree and heuristics net nodes. The DFG wraps them over two lines and shows the full name on hover.
- A filter bar above the panel limits every view to the matching events (column filters and a time range). It is temporary: reloading the page clears it.
- The first BPMN run on a large log can take a minute; the result is cached afterwards.
- Discovery describes the process; it does not check it against a model. For that, upload a reference model in Conformance Checking.

## Method

Discovery mines each model from the events of your log with PM4Py. The DFG counts, within every case, how often one activity is directly followed by another, and how many cases start and end with each activity; a connection's duration is the mean time between an event and the next event of the same case. The Petri net, process tree and BPMN diagram come from the Inductive Miner, or from its Infrequent variant, which removes behaviour below a noise threshold; the Petri net can also use the ILP, Alpha or Alpha+ miner. The heuristics net comes from the Heuristics miner, and the prefix tree is a trie of every case's activity sequence. The module draws on the workflow-mining work of van der Aalst, Weijters and Maruster (2004).

The **Auto** setting looks for the knee of the cumulative frequency curve, in the spirit of the Kneedle method (Satopaa et al., 2011). The two Backbone layouts follow Lee, Song and van der Aalst (2026): the most frequent variant forms a straight backbone, and an integer program assigns rows by weighing upward and long connections against the height of the map. **Layered (Sugiyama)** is the baseline layered pipeline used in that work, and **Process flow (Celonis)** is a custom layered layout. The formal citations are in the info popover next to the module name.
