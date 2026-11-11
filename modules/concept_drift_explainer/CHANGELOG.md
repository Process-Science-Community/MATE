# Changelog

## 2.0.0

- Breaking: reads cv4cdd's detections through a required `inputs: drifts` port (`drift_points` from cv4cdd) instead of opening cv4cdd's result cache directly; the platform runs cv4cdd first.
- Breaking: no longer consumes `cv4cdd.completed` (the no-op refresh hook is gone).
- Shared dashboards: uploading or deleting documents and chatting are owner-only (403 for a read-only viewer).
- Cause confidence threshold takes `scope: dataset` (per-process override). Manifest links moved from `artifacts:` to `links:`.
