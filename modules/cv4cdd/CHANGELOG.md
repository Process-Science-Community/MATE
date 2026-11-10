# Changelog

## 1.1.0

- The `detections` dataset declares `artifact_type: drift_points` and `cache_key: detections`, so Concept Drift Explainer reads it as an input.
- Window size and confidence threshold now `affect: precompute` with `scope: dataset`: changing them (or the model) makes stored detections stale, and **Parameters for this log** can override them per process.
- Manifest: the dataset link moved from the deprecated `artifacts:` key to `links:`.
