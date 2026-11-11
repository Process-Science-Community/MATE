# Changelog

## 1.0.1

- Exponential forgetting k now `affects: precompute` with `scope: dataset`: a changed value makes stored measures stale (recomputed on the next open), and **Parameters for this log** can override it per process.
