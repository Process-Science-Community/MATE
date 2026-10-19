"use client";

import { useMemo } from "react";

import { formatNumber } from "@/lib/format";
import { jobProgress } from "@/lib/job-progress";
import { useJobsStore, type LiveJob } from "@/lib/stores/jobs";

export type StageState = "pending" | "active" | "done" | "failed" | "skipped";

export interface ImportStage {
  key: string;
  label: string;
  state: StageState;
  /** Right-aligned trailer: byte counts, event counts, … */
  detail?: string;
  /** 0–100 for a determinate bar; `null` renders the indeterminate pulse. */
  pct?: number | null;
}

export interface ImportStagesInput {
  /** 0–100 while the file is going up, `null` once the request settled. */
  uploadPct: number | null;
  /** True between the last upload byte and the probe's response. */
  probing: boolean;
  /** True once the staged file has been described. */
  staged: boolean;
  /** The import job, once `POST /event-logs` has answered. */
  jobId: string | null;
}

// The backend's ingest stages, in the order dispatch.py emits them. Anything
// unknown (a future stage key) simply doesn't advance the checklist rather than
// breaking it.
const JOB_STAGES = [
  { key: "parsing", label: "Reading data" },
  { key: "normalizing", label: "Processing events" },
  { key: "writing", label: "Saving" },
] as const;

const STAGE_ORDER: Record<string, number> = {
  parsing: 0,
  normalizing: 1,
  writing: 2,
  done: 3,
};

/**
 * The import as a linear checklist: upload → read → process → save.
 *
 * Modules are deliberately not a stage: they compute lazily, the first time one
 * is opened, so the import is finished the moment the file is saved. The few
 * that are eager warm up in the background and stay visible in the jobs drawer,
 * but nothing here waits on them.
 *
 * Everything after the upload comes from the shared jobs store (hydrated and
 * kept live by `JobsProvider`), so this opens no second stream. Returns the
 * stage list plus the parent job, so the caller can render errors and the
 * finished state without re-deriving them.
 */
export function useImportStages(input: ImportStagesInput): {
  stages: ImportStage[];
  job: LiveJob | null;
  /** Every stage settled: the log is saved and openable. */
  finished: boolean;
  failed: boolean;
} {
  const byId = useJobsStore((s) => s.byId);
  const { uploadPct, probing, staged, jobId } = input;

  return useMemo(() => {
    const job = jobId ? (byId.get(jobId) ?? null) : null;

    const stages: ImportStage[] = [];

    // 1) The upload itself - the only phase the client measures directly.
    stages.push({
      key: "upload",
      label: "Uploading file",
      state: staged || job ? "done" : uploadPct !== null ? "active" : "pending",
      pct: uploadPct,
    });

    // 2) The server describing the staged file (sniff + sample).
    stages.push({
      key: "probe",
      label: "Reading columns",
      state: staged || job ? "done" : probing ? "active" : "pending",
      pct: null,
    });

    if (!jobId) {
      // Before the import is confirmed the remaining work is only a preview.
      for (const stage of JOB_STAGES) {
        stages.push({ key: stage.key, label: stage.label, state: "pending" });
      }
      return { stages, job, finished: false, failed: false };
    }

    const jobFailed = job?.status === "failed" || job?.status === "cancelled";
    const jobDone = job?.status === "completed";
    const currentIndex = jobDone ? STAGE_ORDER.done : (STAGE_ORDER[job?.stage ?? ""] ?? -1);
    const queued = !job || job.status === "queued";
    const progress = job ? jobProgress(job) : null;

    for (const [index, stage] of JOB_STAGES.entries()) {
      let state: StageState = "pending";
      if (jobFailed && index === Math.max(currentIndex, 0)) state = "failed";
      else if (currentIndex > index || jobDone) state = "done";
      else if (currentIndex === index && !queued) state = "active";

      stages.push({
        key: stage.key,
        label: stage.label,
        state,
        detail:
          state === "active" && progress
            ? stage.key === "parsing" && job?.progress_current
              ? `${formatNumber(job.progress_current)} events`
              : progress.label
            : undefined,
        pct: state === "active" ? (progress?.pct ?? null) : undefined,
      });
    }

    return { stages, job, finished: jobDone, failed: jobFailed };
    // Memoised on the stable `byId` map - never used as a shallow selector.
  }, [byId, uploadPct, probing, staged, jobId]);
}
