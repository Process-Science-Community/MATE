"""Complexity over time - how EPA-based complexity KPIs evolve.

Slices the event log along time (whole cases grouped by start time), runs the
vendored complexity math (:mod:`complexity_core`) on each slice, and returns a
per-slice metric series the panel plots as a line (x = time, y = a chosen KPI).

Routes
------
GET ``/timeseries`` - slice + compute the series for a chosen mode/params
(cached, offloaded to a worker thread).

Precompute
----------
``log.imported`` warms the default view (calendar / auto granularity) so the
first panel open is instant.
"""

from __future__ import annotations

import asyncio
import contextlib
import time
from pathlib import Path
from typing import Any

from mate.sdk import Module, ModuleContext, job, on_event, route

from .slicing import compute_timeseries

_MODES = ("absolute", "calendar", "sliding")


# ── Cache helpers (mirrors complexity / performance pattern) ─────────────────


def _cache_is_fresh(ctx: ModuleContext, key: str) -> bool:
    cache_root = Path(ctx.cache.dir) if hasattr(ctx.cache, "dir") else None  # type: ignore[attr-defined]
    if cache_root is None:
        return False
    candidate = cache_root / f"{key}.json"
    if not candidate.exists():
        return False
    try:
        events_path = ctx.event_log.events_path  # type: ignore[attr-defined]
    except AttributeError:
        return False
    try:
        return candidate.stat().st_mtime >= events_path.stat().st_mtime
    except FileNotFoundError:
        return False


async def _cached_or_compute(ctx: ModuleContext, key: str, compute: Any) -> dict[str, Any]:
    if _cache_is_fresh(ctx, key):
        cached = await ctx.cache.get(key)
        if cached is not None:
            ctx.logger.debug("complexity_over_time.cache_hit", key=key)
            return cached
    result = await compute()
    await ctx.cache.set(key, result)
    return result


def _exponential_k(ctx: ModuleContext) -> float:
    try:
        return float(ctx.config.get("exponential_k", 1.0))
    except (TypeError, ValueError):
        return 1.0


def _int_config(ctx: ModuleContext, key: str, default: int) -> int:
    try:
        return int(ctx.config.get(key, default))
    except (TypeError, ValueError):
        return default


def _cache_key(
    mode: str,
    *,
    k: float,
    min_cases: int,
    slices: int | None = None,
    granularity: str | None = None,
    window: float | None = None,
    step: float | None = None,
) -> str:
    """Deterministic cache key so route + precompute + guidance agree."""
    if mode == "absolute":
        return f"ts_absolute_n{slices}_k{k}_m{min_cases}"
    if mode == "sliding":
        return f"ts_sliding_w{window}_s{step}_k{k}_m{min_cases}"
    return f"ts_calendar_{granularity}_k{k}_m{min_cases}"


def _default_key(ctx: ModuleContext) -> str:
    return _cache_key(
        "calendar",
        k=_exponential_k(ctx),
        min_cases=_int_config(ctx, "min_cases_per_slice", 1),
        granularity="auto",
    )


def _log_series(
    ctx: ModuleContext,
    result: dict[str, Any],
    *,
    events: int,
    min_cases: int,
    t0: float,
    trigger: str,
) -> None:
    """Log a freshly computed series: sizes + duration, warn when slices had no metrics."""
    points = result.get("slices") or []
    skipped = sum(1 for p in points if p.get("metrics") is None)
    ctx.logger.info(
        "complexity_over_time.timeseries_computed",
        trigger=trigger,
        mode=result.get("mode"),
        params=result.get("params"),
        events=events,
        slices=len(points),
        computed=len(points) - skipped,
        metrics=len(result.get("metric_keys") or []),
        duration_ms=round((time.perf_counter() - t0) * 1000),
    )
    if not points:
        ctx.logger.warning("complexity_over_time.no_slices", mode=result.get("mode"), events=events)
    elif skipped:
        ctx.logger.warning(
            "complexity_over_time.slices_skipped",
            skipped=skipped,
            slices=len(points),
            min_cases=min_cases,
        )


# ── Module ───────────────────────────────────────────────────────────────────


class ComplexityOverTimeModule(Module):
    id = "complexity_over_time"

    guidance_system_prompt = (
        "You are a process-mining analyst interpreting how structural and "
        "entropy-based complexity metrics evolve over the lifetime of a "
        "process. Each point is a time slice - whole cases grouped by their "
        "start time. Read the series for trends, spikes, and regime shifts in "
        "variant/sequence entropy, Pentland's process complexity, structure "
        "and affinity. Reference specific slice labels and values, and "
        "distinguish genuine drift from thin slices (low n_cases). Suggest "
        "concrete next steps when relevant."
    )

    async def guidance_payload(self, ctx: ModuleContext) -> dict[str, Any] | None:
        key = _default_key(ctx)
        if not await ctx.cache.exists(key):
            return None
        series = await ctx.cache.get(key)
        if not isinstance(series, dict):
            return None
        # Slim each point to the headline KPIs so the LLM gets the trajectory
        # without the full 24-metric payload per slice.
        headline = (
            "variant_entropy",
            "normalized_variant_entropy",
            "sequence_entropy",
            "structure",
            "affinity",
            "pentland_process",
        )
        slim_slices = []
        for point in series.get("slices", []):
            metrics = point.get("metrics")
            slim_metrics = (
                {k: metrics.get(k) for k in headline} if isinstance(metrics, dict) else None
            )
            slim_slices.append(
                {
                    "label": point.get("label"),
                    "n_cases": point.get("n_cases"),
                    "n_events": point.get("n_events"),
                    "metrics": slim_metrics,
                }
            )
        return {
            "mode": series.get("mode"),
            "params": series.get("params"),
            "metric_keys": series.get("metric_keys"),
            "slices": slim_slices,
        }

    @route.get("/timeseries")
    async def timeseries(
        self,
        ctx: ModuleContext,
        mode: str = "calendar",
        slices: int = 0,
        granularity: str = "auto",
        window: float = 30.0,
        step: float = 7.0,
    ) -> dict[str, Any]:
        if mode not in _MODES:
            ctx.logger.warning(
                "complexity_over_time.unknown_mode", requested=mode[:40], used="calendar"
            )
        mode = mode if mode in _MODES else "calendar"
        k = _exponential_k(ctx)
        min_cases = _int_config(ctx, "min_cases_per_slice", 1)

        if mode == "absolute":
            n = slices if slices and slices > 0 else _int_config(ctx, "default_slices", 50)
            params: dict[str, Any] = {"slices": n}
            key = _cache_key("absolute", k=k, min_cases=min_cases, slices=n)
        elif mode == "sliding":
            params = {"window": float(window), "step": float(step)}
            key = _cache_key(
                "sliding", k=k, min_cases=min_cases, window=float(window), step=float(step)
            )
        else:
            params = {"granularity": granularity}
            key = _cache_key("calendar", k=k, min_cases=min_cases, granularity=granularity)

        async def _compute() -> dict[str, Any]:
            t0 = time.perf_counter()
            async with ctx.event_log as log:
                df = await log.pandas()
            try:
                result = await asyncio.to_thread(
                    compute_timeseries,
                    df,
                    mode,
                    params,
                    exponential_k=k,
                    min_cases=min_cases,
                )
            except Exception:
                ctx.logger.exception(
                    "complexity_over_time.timeseries_failed", mode=mode, events=len(df)
                )
                raise
            _log_series(ctx, result, events=len(df), min_cases=min_cases, t0=t0, trigger="request")
            return result

        return await _cached_or_compute(ctx, key, _compute)

    @on_event("log.imported")
    @job(progress=True, title="Complexity over time - precompute")
    async def precompute(self, ctx: ModuleContext, payload: dict[str, Any]) -> None:
        t0 = time.perf_counter()
        await ctx.progress.update(0.0, "Loading log")
        k = _exponential_k(ctx)
        min_cases = _int_config(ctx, "min_cases_per_slice", 1)
        async with ctx.event_log as log:
            df = await log.pandas()
        await ctx.progress.update(0.2, "Slicing & computing")

        # Per-slice ticks from the compute thread (scaled into [0.2, 0.9])
        # so a many-slice series doesn't look stalled.
        loop = asyncio.get_running_loop()

        def report(fraction: float, message: str) -> None:
            with contextlib.suppress(RuntimeError):
                asyncio.run_coroutine_threadsafe(
                    ctx.progress.update(0.2 + min(max(fraction, 0.0), 1.0) * 0.7, message),
                    loop,
                )

        try:
            result = await asyncio.to_thread(
                compute_timeseries,
                df,
                "calendar",
                {"granularity": "auto"},
                exponential_k=k,
                min_cases=min_cases,
                progress=report,
            )
        except Exception:
            ctx.logger.exception("complexity_over_time.precompute_failed", events=len(df))
            raise
        _log_series(ctx, result, events=len(df), min_cases=min_cases, t0=t0, trigger="precompute")
        await ctx.progress.update(0.95, "Caching")
        await ctx.cache.set(_default_key(ctx), result)
        await ctx.progress.update(1.0, "Done")
