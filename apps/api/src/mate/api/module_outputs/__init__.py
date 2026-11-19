"""Server-side module-output layer (Phase 2).

A *module output* is a module's named, typed data output (manifest
``outputs:``). This package promotes the Phase-1 client-side normalization to
the server: it resolves a module output to a canonical
:class:`OutputEnvelope` and applies transform chains over it (DuckDB). The
envelope JSON intentionally matches the TS ``OutputEnvelope`` shape so the same
generic-viz components render it.
"""
