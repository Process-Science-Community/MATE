# Changelog

## 1.0.1

- Manifest: the reference link moved from the deprecated `artifacts:` key to `links:`.
- Distance-metric variant cap now `affects: precompute` with `scope: dataset`: a changed value makes stored metrics stale (recomputed on the next open), and **Parameters for this log** can override it per process.
