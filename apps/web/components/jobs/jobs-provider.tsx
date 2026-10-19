"use client";

import { useEffect } from "react";
import { useProgressRouter } from "@/lib/use-progress-router";
import { toast } from "sonner";

import { toastError } from "@/lib/toast";
import { useQueryClient, type QueryClient } from "@tanstack/react-query";

import { api } from "@/lib/api";
import { subscribeBus } from "@/lib/ws";
import { useJobsStore } from "@/lib/stores/jobs";
import { useUi } from "@/lib/stores/ui";
import { queryKeys } from "@/lib/queries";
import type { EventLogDetail, JobDetail, ModuleRunState } from "@/lib/api-types";

/** A module's precompute job (`module.<id>.event.<topic>`): started by the platform
 *  on a module's behalf, not by a button the user pressed. */
const isPrecomputeJob = (type: string) => /^module\..+\.event\./.test(type);

/**
 * True when a module page or dashboard card is open on this very run: its gate
 * already shows the outcome (a failure with Retry, a cancel with Run) right where the
 * user is looking, so a toast repeating it - a failure's would never even dismiss -
 * is just noise. Reads the gate queries' live observers in the app cache.
 */
const gateShowsJob = (qc: QueryClient, jobId: string): boolean =>
  qc
    .getQueryCache()
    .findAll({ queryKey: ["module-runs"] })
    .some(
      (q) =>
        q.queryKey[2] === "gate" &&
        q.getObserversCount() > 0 &&
        Array.isArray(q.state.data) &&
        (q.state.data as ModuleRunState[]).some((r) => r.job_id === jobId),
    );

/**
 * Mounts once in the platform layout. Hydrates the jobs store, subscribes to
 * `WS /events?topic=job.*` for the lifetime of the session, fans events into
 * the store, fires Sonner toasts (debounced), and invalidates TanStack
 * caches that depend on job state (event-logs list when an import completes,
 * a log's module compute state when a run changes).
 */
export function JobsProvider() {
  const setAll = useJobsStore((s) => s.setAll);
  const apply = useJobsStore((s) => s.applyEvent);
  const muted = useUi((s) => s.notificationsMuted);
  const qc = useQueryClient();
  const router = useProgressRouter();

  // Initial hydration.
  useEffect(() => {
    let cancelled = false;
    api<JobDetail[]>("/api/v1/jobs?limit=100")
      .then((rows) => {
        if (!cancelled) setAll(rows);
      })
      .catch(() => {
        /* the dock falls back to "no jobs yet" – non-fatal */
      });
    return () => {
      cancelled = true;
    };
  }, [setAll]);

  useEffect(() => {
    const sub = subscribeBus<Record<string, unknown>>(
      ["job.*", "log.imported", "log.ready", "module_run.updated"],
      (env) => {
        const id = (env.payload.id as string | undefined) ?? "";
        // Read before `apply`: a cancelled job is deleted from the store by it, and
        // `job.completed` / `job.cancelled` carry no title of their own.
        const storedTitle = useJobsStore.getState().byId.get(id)?.title;
        apply(env.topic, env.payload);

        const title = (env.payload.title as string | undefined) ?? storedTitle ?? id;

        if (env.topic === "log.imported") {
          const fixedCols = env.payload.fixed_columns as string[] | undefined;
          if (fixedCols && fixedCols.length > 0 && !muted) {
            toast.info(
              fixedCols.length === 1
                ? "1 column was automatically fixed during import"
                : `${fixedCols.length} columns were automatically fixed during import`,
              { description: `Mixed-type values coerced to null: ${fixedCols.join(", ")}` },
            );
          }
          return;
        }

        if (env.topic === "module_run.updated") {
          // A module's compute state for a log changed (queued → running → ready /
          // failed ...). Refetch both the per-log list (grid badges) and any
          // module gate watching it - they all live under this prefix.
          const logId = env.payload.log_id as string | undefined;
          if (logId) void qc.invalidateQueries({ queryKey: queryKeys.moduleRuns(logId) });
          return;
        }

        if (env.topic === "log.ready") {
          // The log is now openable. Modules compute lazily, so this fires right
          // after the parse - nothing waits on them. It is the success signal for
          // an import, and it un-blocks any module gate that was waiting on the
          // import (`409 → importing`) so it can start its run.
          qc.invalidateQueries({ queryKey: queryKeys.eventLogs() });
          const logId = env.payload.log_id as string | undefined;
          if (logId) void qc.invalidateQueries({ queryKey: queryKeys.moduleRuns(logId) });
          if (!muted && logId) {
            // The log's own name, from the detail query the import screen polls. A
            // job still live in the store has an empty `payload_json`, so looking the
            // import job up by log id never matched and the toast fell through to the
            // raw uuid; never show that.
            const name =
              qc.getQueryData<EventLogDetail>(queryKeys.eventLog(logId))?.name ??
              Array.from(useJobsStore.getState().byId.values())
                .find(
                  (j) =>
                    j.type === "event_log.import" &&
                    (j.payload_json as { log_id?: string } | undefined)?.log_id === logId,
                )
                ?.title.replace(/^(re-)?import - /i, "") ??
              "your process";
            toast.success(`Imported - ${name}`, {
              action: {
                label: "Open",
                onClick: () => router.push(`/processes/${logId}`),
              },
            });
          }
          return;
        }

        if (env.topic === "job.completed") {
          // Refresh anything keyed off the api state. Event-log imports flip a
          // log row from `importing` → `ready`, so the /processes table needs to
          // refetch. The "Imported" success toast itself fires on `log.ready`
          // (above).
          qc.invalidateQueries({ queryKey: queryKeys.eventLogs() });
          const type = (env.payload.type as string | undefined) ?? "";
          // Not for a module's precompute: the module page shows its own result
          // the moment it lands, and an eager one at import would just be noise.
          if (type !== "event_log.import" && !isPrecomputeJob(type) && !muted) {
            toast.success(`Completed - ${title}`);
          }
          return;
        }

        if (env.topic === "job.failed") {
          // Only for failures nobody is looking at (an eager run at import, another
          // page) - an open module page already shows it inline, with a Retry.
          if (gateShowsJob(qc, id)) return;
          const error = (env.payload.error as string | undefined) ?? "";
          toastError(`Failed - ${title}`, {
            description: error || undefined,
            duration: Number.POSITIVE_INFINITY,
            action: {
              label: "Details",
              onClick: () => useJobsStore.getState().setDrawerOpen(true),
            },
          });
          return;
        }

        if (env.topic === "job.cancelled") {
          // The user pressed Cancel on the gate that is showing this run: it already
          // says so (and offers Run), so no toast on top of it.
          if (!muted && !gateShowsJob(qc, id)) toast.warning(`Cancelled - ${title}`);
          return;
        }
      },
    );

    return () => sub.close();
  }, [apply, muted, qc, router]);

  return null;
}
