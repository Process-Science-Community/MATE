# Changelog

## 1.1.0

- Datasets declare their artifact types: `dfg`, `petri_net` (`cache_key: petri_net_inductive`, read by Conformance) and `process_tree` (`cache_key: process_tree`).
- The panel renders BPMN, Petri net and process tree with the platform's shared artifact views; the module's own canvases, edges, nodes and layout code are gone.
- `POST /dfg/layout` is viewer-safe, so a shared dashboard's viewer can lay out the DFG.
