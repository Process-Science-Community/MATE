# How to use Object-Centric Discovery

Object-Centric Discovery shows how a process runs when several kinds of objects, such as orders, items and invoices, take part in the same events. You get the object types, their directly-follows graphs, an object-centric Petri net, the relations between object types and a lifecycle summary, all without forcing the data into one case notion.

## When to use it

- Your events refer to several objects at once, for example one order with many items. Look at each object type's own flow and at where the types meet.
- You want to know which activities touch which object types, how many objects of each type exist and how long they live.
- You work with a single case notion. Use Discovery on a case-centric log. This module only works on object-centric logs, and the case-centric modules are unavailable for them.

## Before you start

- The module needs an object-centric (OCEL) log. MATE reads OCEL files in JSON (`.jsonocel`), XML (`.xmlocel`) and SQLite (`.sqlite`, OCEL 2.0), and recognises plain `.json` and `.xml` files by their content. There are no required columns, and a log needs at least 1 event. On a case-centric log the module's card in the process's module list shows an **Unavailable** badge and cannot be opened; hover the card to read the reason.
- Nothing is precomputed and nothing is stored. Each tab computes when you open it, and the OC-DFG and the Petri net take longest on large logs.
- Nothing else is needed: no model, no key and no external service. It runs entirely on your MATE server.

## Settings

The six tabs are **Overview**, **OC-DFG**, **OC Petri net**, **Object graph**, **Activity / type** and **Objects**. **Overview** is open first. The module has no configuration on its module page.

The three graph tabs are canvases with a toolbar in the top right corner: **Zoom out**, **Zoom in**, **Fit to view**, **Graph settings** (the sliders icon), **Reset layout** and **Fullscreen**. Their controls sit in **Graph settings**. **Reset layout** lays the graph out again and discards nodes you moved, which are not kept. A mini-map appears at the bottom right while you pan or zoom.

### Overview

| Setting | Default | What it changes |
| --- | --- | --- |
| Object type picker (placeholder "Object type") | The first object type, alphabetically | Which object type the directly-follows list and the start and end lists show. |

### OC-DFG

| Setting | Default | What it changes |
| --- | --- | --- |
| **Object type** | The first object type, alphabetically | Which object type's graph is drawn. |
| **Measure** | Unique objects | What the edge labels, the edge thickness and the start and end counts count: **Unique objects**, **Events** or **Total objects**. It is disabled while **Performance** is on. |
| **Performance** | Off | Weights and labels the edges by their mean duration instead of by frequency. |

### OC Petri net

| Setting | Default | What it changes |
| --- | --- | --- |
| **Object type** | The first object type, alphabetically | Which object type's net is drawn. |

### Object graph

| Setting | Default | What it changes |
| --- | --- | --- |
| **Relation** | Interaction | Which relation between objects is shown: **Interaction**, **Descendants**, **Inheritance**, **Co-birth** or **Co-death**. |

### Activity / type

| Setting | Default | What it changes |
| --- | --- | --- |
| Metric picker (**Events** or **Objects**) | Events | What each cell counts. |
| **Activities** and **Object types** | All shown | Two buttons next to the legend open checklists that hide rows or columns. Once something is hidden, the button shows how many are visible, for example `3/5`, and a **Show all** entry brings everything back. |

## Reading the results

### Overview

Four tiles give the number of **Object types**, **Objects**, **Events** and **Activities**. Below them one badge per object type shows its name and its number of objects.

Under "Directly-follows", pick an object type to see its edges as `source → target` with a count, ranked by that count, and its **Start activities** and **End activities** with counts ("None." if there are none). All counts here are unique objects: how many different objects of the type take the step, start there or end there. A type without edges reads "No directly-follows edges for this object type."

### OC-DFG

One object type's directly-follows graph, drawn from left to right. Each box is an activity. The directly-follows relation is taken per object: the next event of the same object, so an edge does not mean that two activities follow each other in the log as a whole.

- A play badge on an activity shows how many objects of the type start there, and a square badge how many end there. Both follow the selected **Measure**.
- The number under an activity name is the largest value among the edges and the start and end counts that touch it. It is not the number of events of the activity.
- An edge is labelled with the selected measure and drawn thicker the larger it is. **Unique objects** counts the different objects of the type that take the step. **Events** counts the pairs of consecutive events that carry the step, one per pair however many objects of the type they involve. **Total objects** counts every object once for each event pair it takes the step in.
- With **Performance** on, the edge label is the mean time between the two events, such as `~3 d 4 h`, and slower edges are drawn thicker.

### OC Petri net

The object-centric Petri net of one object type. Circles are places, and the source and sink places carry a ring and a dot. Boxes are transitions, one per activity; a small dark box is a silent τ transition, a routing step that is not an activity. A thicker arc marks a variable arc, where an activity takes or produces a varying number of objects of the type. The net carries no counts; use the OC-DFG for frequencies.

If a type has too few events to mine a model, the tab reads "No Petri net for this object type" and asks you to pick another type in **Graph settings**.

### Object graph

A graph with one node per object type. A node shows the number of objects of the type and, with a circular-arrows icon, the number of pairs of objects of that same type that stand in the chosen relation. An edge joins two different types and is labelled with the number of distinct object pairs, and it is drawn thicker the larger it is. **Descendants** and **Inheritance** are directed and have arrowheads; the others are undirected.

- **Interaction**: two objects that appear in the same event.
- **Descendants**: an object that appeared in an earlier event, linked to an object that appears for the first time in the current event.
- **Inheritance**: an object whose last event is the event in which another object first appears.
- **Co-birth**: objects that appear for the first time in the same event.
- **Co-death**: objects whose last event is the same event.

If no two types are related this way, the tab reads "No relations of this kind between object types."

### Activity / type

A table of activities against object types. Each cell counts the events of the activity that involve the type (**Events**) or the different objects of the type involved in the activity (**Objects**), and zero shows as a dot. Blue shading is relative to the largest cell for the chosen metric, and the legend runs from "low" to "high". Hover a cell for both numbers. An activity that touches several types is a point where the types meet.

### Objects

On the left, one entry per object type with its number of objects and the median and average lifecycle, the span from an object's first to its last event, and the average number of events per object. If the log has an object-to-object table, as OCEL 2.0 logs can, "avg interacting" adds the average number of other objects an object is related to there, over the objects that have such a relation. On the right, **Longest-lived objects** lists the 50 objects with the longest lifecycle with their type, lifecycle and number of events.

### Dashboard cards

Cards follow the filters of their dashboard. Each has an info icon with a short explanation and an arrow (**Open in module**) that opens this page.

- **Object types**: the tiles Object types, Objects and Activities, and a badge per object type with its object count.
- **Object-centric DFG**: a picker for the object type and a ranked list of its edges with the number of unique objects. It is a list, not a graph.
- **Activity / object-type matrix**: the compact matrix, counting events.
- **Object lifecycles**: per object type the number of objects, the median lifecycle, the average number of events and, when available, the average number interacting.

## Tips and limits

- The measures differ: a step that many objects of one type take together has a high **Unique objects** or **Total objects** value but a low **Events** value. Try the other measure before you conclude that a step is rare.
- Object types that share events are where the process synchronises. Look for activities that touch several types in the matrix and for the edges between types in the Object graph.
- Counts of objects, events and relations come straight from the log's tables. The OC-DFG, the Petri net and the Object graph relations other than Interaction come from PM4Py and can take a while on logs with many objects.
- Durations, such as lifecycles and edge times, are wall-clock times and include waiting.
- The module describes the process; it does not check it against a model. For a case-centric log, use Discovery for the models and Conformance Checking for a reference model.

## Method

The module uses PM4Py (Berti, van Zelst and Schuster, 2023). The OC-DFG links, for each object, its consecutive events and counts the resulting activity pairs per object type, in three measures, with edge durations taken between the two events. The Petri net is the object-centric net that PM4Py mines per object type with the Inductive Miner, shown one type at a time. The Object graph relations are PM4Py's object graphs: interaction is computed directly from the relations table, and the other four come from the event order. Matrix and lifecycle figures are counts and spans computed from the relations table. The formal citation is in the info popover next to the module name.
