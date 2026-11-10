# Changelog

## 1.1.0

- Without an uploaded BPMN, the log is checked against Discovery's Inductive-miner Petri net (new optional `inputs: reference_model`), converted to BPMN; an uploaded model always takes precedence.
- The `results` dataset declares `artifact_type: conformance_result`.
- The panel renders the model with the platform's shared BPMN view; the module's own canvas and overlay code are gone.
- Shared dashboards: uploading, activating and deleting reference models are owner-only (403 for a read-only viewer).
- Conforming-trace threshold takes `scope: dataset` (per-process override).
