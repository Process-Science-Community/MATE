"use client";

import { useMemo } from "react";
import { useMutation, useQuery } from "@tanstack/react-query";

import { ApiError, api } from "@/lib/api";
import type { ModuleRunState } from "@/lib/api-types";
import { getAppQueryClient } from "@/lib/get-query-client";
import { queryKeys } from "@/lib/query-keys";

/**
 * Per-log module compute state - the data layer behind lazy module processing.
 *
 * A log is ready the moment it is parsed; a module computes for it the first time
 * something needs it. These hooks read what has computed (`useModuleRuns`) and
 * ask for a module to be computed (`useModuleRunGate`, `useRunModule`). Mirrors
 * `apps/api/.../routes/module_runs.py`.
 *
 * Every hook here passes the **app** QueryClient explicitly. Module pages and
 * dashboards wrap their content in their own isolated clients, so the ambient one
 * is not the cache the SSE handler (`jobs-provider.tsx`) keeps fresh.
 */

const IN_FLIGHT = new Set<ModuleRunState["status"]>(["queued", "running"]);

/** The bus is lossy (a full subscriber queue drops its oldest event), so while a
 *  run is in flight we also poll, and a missed `module_run.updated` self-heals. */
const POLL_MS = 2500;

const anyInFlight = (states: ModuleRunState[] | undefined) =>
  !!states?.some((s) => IN_FLIGHT.has(s.status));

/** Where every precompute-capable module stands for one log. Read-only: it never
 *  starts anything (see `useModuleRunGate` for that). */
export function useModuleRuns(logId: string | null | undefined) {
  return useQuery(
    {
      queryKey: queryKeys.moduleRuns(logId ?? ""),
      queryFn: () => api<ModuleRunState[]>(`/api/v1/event-logs/${logId}/module-runs`),
      enabled: !!logId,
      staleTime: 10_000,
      refetchInterval: (q) => (anyInFlight(q.state.data) ? POLL_MS : false),
    },
    getAppQueryClient(),
  );
}

/** What a surface that needs a module's results should show right now. */
export type RunGate =
  /** Nothing to compute (no precompute in the module's closure): render it. */
  | { kind: "none" }
  /** First request still in flight - nothing known yet. */
  | { kind: "starting" }
  | {
      kind: "computing";
      /** The module whose job is running (may be a prerequisite of the one opened). */
      moduleId: string;
      /** Null while a module waits on one that has not started yet. */
      jobId: string | null;
    }
  | { kind: "ready" }
  | { kind: "failed"; moduleId: string; error: string | null }
  | { kind: "cancelled"; moduleId: string }
  | { kind: "unavailable"; moduleId: string; reason: string | null }
  /** The log is still being imported; modules can't run yet. */
  | { kind: "importing" }
  | { kind: "error"; message: string };

/** Collapse the (dependency-ordered) states `ensure` returns into one gate. Pure,
 *  so the precedence is easy to reason about: any failure wins over progress. */
export function deriveRunGate(states: readonly ModuleRunState[]): RunGate {
  if (states.length === 0) return { kind: "ready" };

  const failed = states.find((s) => s.status === "failed");
  if (failed) return { kind: "failed", moduleId: failed.module_id, error: failed.error };
  const cancelled = states.find((s) => s.status === "cancelled");
  if (cancelled) return { kind: "cancelled", moduleId: cancelled.module_id };
  const unavailable = states.find((s) => s.status === "unavailable");
  if (unavailable) {
    return { kind: "unavailable", moduleId: unavailable.module_id, reason: unavailable.error };
  }
  if (states.every((s) => s.status === "ready")) return { kind: "ready" };

  // Something is queued or running. Prefer the one actually executing, then one
  // with a job queued; a module parked behind a prerequisite has no job of its own.
  const active =
    states.find((s) => s.status === "running") ??
    states.find((s) => s.status === "queued" && s.job_id) ??
    states.find((s) => s.status !== "ready") ??
    states[0];
  return { kind: "computing", moduleId: active.module_id, jobId: active.job_id };
}

const ensureRun = (logId: string, moduleId: string, force: boolean) =>
  api<ModuleRunState[]>(
    `/api/v1/event-logs/${logId}/module-runs/${encodeURIComponent(moduleId)}`,
    { method: "POST", json: { force } },
  );

/**
 * Make sure a module (and what it needs first) has computed for a log, and track
 * it live. The request is an idempotent POST used as a query: it re-asks on every
 * open, which is exactly what notices that the data moved on since the last visit
 * and recomputes a stale result. SSE (`module_run.updated`, via the invalidation in
 * `jobs-provider.tsx`) and a light poll while a run is in flight keep it current.
 *
 * `enabled=false` (a module with no precompute) makes it inert.
 */
export function useModuleRunGate(logId: string, moduleId: string, enabled: boolean): RunGate {
  const query = useQuery(
    {
      queryKey: queryKeys.moduleRunGate(logId, moduleId),
      queryFn: () => ensureRun(logId, moduleId, false),
      enabled,
      staleTime: 0,
      refetchInterval: (q) => (anyInFlight(q.state.data) ? POLL_MS : false),
    },
    getAppQueryClient(),
  );

  const { data, error } = query;
  return useMemo<RunGate>(() => {
    if (!enabled) return { kind: "none" };
    if (error) {
      // 409: the log is still importing. The `log.ready` handler invalidates this
      // query, so it resolves on its own once the import finishes.
      if (error instanceof ApiError && error.status === 409) return { kind: "importing" };
      return { kind: "error", message: error.message };
    }
    if (!data) return { kind: "starting" };
    return deriveRunGate(data);
  }, [enabled, data, error]);
}

/** Run (or retry) a module explicitly: the gate's "Run" / "Retry" button. `force`
 *  retries a failed or cancelled run; a fresh result is never recomputed. */
export function useRunModule(logId: string, moduleId: string) {
  return useMutation(
    {
      mutationFn: (force: boolean = true) => ensureRun(logId, moduleId, force),
      onSuccess: (states) => {
        getAppQueryClient().setQueryData(queryKeys.moduleRunGate(logId, moduleId), states);
        void getAppQueryClient().invalidateQueries({ queryKey: queryKeys.moduleRuns(logId) });
      },
    },
    getAppQueryClient(),
  );
}
