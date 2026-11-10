# How to use Model Interop

Model Interop converts process models between the process tree, Petri net and BPMN notations, and imports or exports them as PNML and BPMN files. Use it to take a model from one module or tool to another, for example a mined model to BPMN or PNML, or a ProM Petri net into MATE.

## When to use it

- You want Discovery's model as a BPMN model, or as a PNML file that ProM, pm4py or another Petri net tool can open.
- You have a Petri net (PNML) or a BPMN model from another tool and want it next to the models MATE mined from the log.
- You want a BPMN model as a Petri net, the notation conformance checking and most process mining algorithms work on.
- To mine a model from the log, use Discovery. To check a log against a model, use Conformance Checking, which takes BPMN files directly.

## Before you start

- The module needs a case-centric event log. It never reads the log's events, so there is no column or size requirement.
- The models it converts by default come from Discovery: its Petri net and its process tree, both mined with the Inductive Miner. Without Discovery installed there is nothing to convert, and only imported models are available.
- The module runs after Discovery. Opening it for a process runs Discovery first if Discovery has not computed for the log yet, and it runs again whenever Discovery recomputes, for example after a filter is committed or the log is reimported. If Discovery fails or is cancelled, Model Interop shows that error with a **Retry**, even when you only want to work with imported models. On a log too small for Discovery (it needs at least 50 events) the automatic conversion never starts; imports, exports of imported models and run variants still work.
- The BPMN conversion has no default source, because Discovery does not offer its BPMN model as a stored result. It runs only in a run variant that hands the module a BPMN model, for example an imported one: `POST /api/v1/event-logs/<log id>/module-runs/interop/variants` with `{"inputs": {"bpmn": "<log id>/interop/imported_bpmn"}}`.
- The conversion runs as a background job with progress and a **Cancel** button.
- Once the conversion has finished, the module page shows the models on a board, described under *Reading the results*.

## Settings

This module has no settings.

## Reading the results

### The board

The module page shows the models of the log on a board of four cards: **Petri net (from process tree)** and **BPMN (from Petri net)** on top, **Imported Petri net** and **Imported BPMN model** below. Each card draws its model with MATE's Petri net or BPMN viewer. A card whose model is not there shows *No Petri net.* or *No BPMN model.*, for example the imported cards before anything is imported.

- Click **Edit** to move, resize or remove cards, or to add other cards from **Cards & data**. Changes save automatically and are kept per log: each log has its own board, and each user their own.
- In edit mode, **Reset to module default** puts the four cards back where the module places them. **Undo** takes the reset back.
- **Petri net (from BPMN)** is not on the board. Only a run variant produces it, and the board shows the log's default results.
- The board's filter bar does not change the models: they are converted from the log's committed data, so with a filter set each card keeps its model and shows an *Unfiltered (precomputed)* badge.

### Converted models

Each conversion is stored as a result of this module for the log:

- **Petri net (from process tree)**: Discovery's process tree, or the process tree handed to the module, as a Petri net.
- **BPMN (from Petri net)**: Discovery's Petri net, or the Petri net handed to the module, as a BPMN model.
- **Petri net (from BPMN)**: a BPMN model handed to the module, as a Petri net.

A result is missing when its source is missing. It is removed again when the conversion runs once more without its source.

### Imported models

- **Imported Petri net**: the last PNML file imported for this log.
- **Imported BPMN model**: the last BPMN file imported for this log.

### Where else the models show

The **Cards & data** palette of any dashboard lists all five models under Model Interop, so a card for them can sit next to other modules' results. Other modules can read them as inputs, and the export described under *Tips and limits* downloads them.

## Tips and limits

- There are no upload or download buttons yet. Import and export run through the module's API and always name the log: `POST /api/v1/modules/interop/import/pnml?log_id=<log id>` or `/import/bpmn?log_id=<log id>` with the file as `file`, and `GET /api/v1/modules/interop/export/pnml?log_id=<log id>` or `/export/bpmn?log_id=<log id>` for a download.
- Export downloads one of this module's own stored results for the log. Without a `source`, PNML export takes the first that exists of `petri_net_from_process_tree`, `petri_net_from_bpmn` and `imported_petri_net`, and BPMN export the first of `bpmn_from_petri_net` and `imported_bpmn`. Name one with `&source=`, for example `source=imported_petri_net`. The file is named after the result it holds. When the result is not there, the export answers 404 and names it: open Model Interop for the log, or import a model, first.
- Export reads the log's default results only. A run variant's results are not exported as files; read them through the platform's dataset API instead, `GET /api/v1/datasets/interop/<result>?log_id=<log id>&variant=<variant key>`, which returns JSON: a BPMN model's XML is in `data.value`, a Petri net comes as a graph of nodes and edges.
- An exported BPMN file has no diagram layout: every shape and flow sits at the same point. Run the auto-layout of the modeler you open it in, or a BPMN layout tool, before you edit it by hand. pm4py's own layouter needs Graphviz, which MATE does not ship.
- The BPMN cards lay the model out in the browser. For some converted models that layout fails, and the card shows every shape stacked on one spot. Export the model and run your modeler's auto-layout instead.
- An import must be at most 10 MB and well-formed XML without a DOCTYPE declaration. Files with a DOCTYPE are refused, because they can make an XML reader fetch other files. An imported model also needs at least one labelled activity.
- A PNML file with inhibitor, reset or other special arcs is refused with an error naming the arc type: MATE's Petri nets have normal arcs only, and turning such arcs into normal ones would change what the net allows.
- Each log keeps one imported Petri net and one imported BPMN model. Importing another file of the same kind replaces it; an import cannot be deleted. Imports are stored with the module's results for the log and your account, kept when the results recompute after the log's data changes, and saved with them to the S3 bucket when MATE stores its data there. They belong to the log's default results, not to run variants. Only the log's owner can import: someone who sees the log through a shared dashboard sees the imported models there, but an import from them is refused with 403.
- When a PNML file holds several nets, pm4py reads only the last one. When it gives no final marking, pm4py guesses one.
- BPMN import keeps only what pm4py reads from the file and writes it out again in pm4py's form, so elements pm4py does not support are dropped. A BPMN model that pm4py cannot turn into a Petri net is refused.
- Converting a Petri net to BPMN works best on a workflow net with an initial and a final marking, the kind Discovery mines. Without them the BPMN model has no path from its start or to its end event.
- Converting back and forth keeps the activities and the order between them, not the drawing: places and invisible steps are renamed and gateways may differ from the original.

## Method

All conversions are pm4py's, described by Alessandro Berti, Sebastiaan van Zelst and Daniel Schuster. A process tree becomes a sound workflow net in which every operator is expressed with places and invisible transitions. A Petri net becomes BPMN by turning each labelled transition into a task, each place and each invisible transition into a gateway, joining an initial place to a start event and a final place to an end event, and then removing redundant gateways. A BPMN model becomes a Petri net with places for its sequence flows, a labelled transition per task and invisible transitions for gateways and events, starting with one token in a source place and ending in a sink place. PNML files are read and written with pm4py's PNML importer and exporter. The Petri net and BPMN results use the same stored form as Discovery's, so any module that reads Discovery's models can read them.
