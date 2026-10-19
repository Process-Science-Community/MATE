"use client";

import { useEffect, useRef, useState } from "react";
import { AlertTriangle, Loader2, Play, RotateCw } from "lucide-react";

import { Button } from "@/components/ui/button";
import { Progress } from "@/components/ui/progress";
import { Skeleton } from "@/components/ui/skeleton";
import { PanelSkeleton } from "@/components/skeletons";
import { cn } from "@/lib/cn";
import { formatDuration } from "@/lib/format";
import { jobProgress, stageLabel } from "@/lib/job-progress";
import { useRunModule, useModuleRunGate, type RunGate } from "@/lib/module-run-queries";
import { useCancelJob, useModuleNames } from "@/lib/queries";
import { useJobsStore } from "@/lib/stores/jobs";

/**
 * How long a run has to take before the skeleton gives way to a progress card.
 * Most modules finish well inside this, and a card that flashes up for a moment
 * reads as flicker, not feedback.
 */
const PROGRESS_AFTER_MS = 400;

interface ModuleRunGateProps {
  logId: string;
  moduleId: string;
  /** `ModuleSummary.precompute_deps`. Empty = nothing to compute: children render at once. */
  deps: readonly string[];
  /** `page`: a module page. `card`: a dashboard card - compact, fills its frame. */
  variant?: "page" | "card";
  /**
   * Called when a run this gate watched finishes and the children are about to
   * (re)mount. A surface whose query cache outlives the gate - a dashboard's
   * widget client - uses it to drop what it cached before the recompute; a module
   * page does not need it, because its panel client is created fresh on mount.
   */
  onComputed?: () => void;
  children: React.ReactNode;
}

/**
 * Holds a module's surface back until its results exist for this log.
 *
 * Modules compute lazily, so opening one for the first time (or after the log's
 * data changed) starts its job - the gate asks, shows live progress with a Cancel,
 * and mounts `children` the moment the run is ready. Mounting only then is what
 * makes the panel read fresh data: a panel that mounts first would fetch while the
 * cache is empty and cache that miss.
 *
 * A module with no precompute (`deps` empty) is not gated at all.
 */
export function ModuleRunGate({
  logId,
  moduleId,
  deps,
  variant = "page",
  onComputed,
  children,
}: ModuleRunGateProps) {
  const gate = useModuleRunGate(logId, moduleId, deps.length > 0);

  // Only a run we actually saw in flight counts: opening a module whose results
  // already exist goes straight to `ready` and has nothing to invalidate.
  const sawRun = useRef(false);
  useEffect(() => {
    if (gate.kind === "computing") {
      sawRun.current = true;
    } else if (gate.kind === "ready" && sawRun.current) {
      sawRun.current = false;
      onComputed?.();
    }
  }, [gate.kind, onComputed]);

  if (gate.kind === "none" || gate.kind === "ready") return <>{children}</>;
  return <GateStatus gate={gate} logId={logId} moduleId={moduleId} variant={variant} />;
}

function GateStatus({
  gate,
  logId,
  moduleId,
  variant,
}: {
  gate: Exclude<RunGate, { kind: "none" } | { kind: "ready" }>;
  logId: string;
  moduleId: string;
  variant: "page" | "card";
}) {
  const moduleName = useModuleNames();
  const run = useRunModule(logId, moduleId);
  const compact = variant === "card";
  const retry = (
    <Button
      size={compact ? "sm" : "default"}
      variant="outline"
      className="cursor-pointer"
      disabled={run.isPending}
      onClick={() => run.mutate(true)}
    >
      {run.isPending ? (
        <Loader2 className="mr-1.5 h-3.5 w-3.5 animate-spin" />
      ) : (
        <RotateCw className="mr-1.5 h-3.5 w-3.5" />
      )}
      Retry
    </Button>
  );

  switch (gate.kind) {
    case "starting":
    case "computing":
      return <Computing gate={gate} moduleId={moduleId} compact={compact} />;

    case "failed":
      return (
        <Notice
          compact={compact}
          icon={<AlertTriangle className="h-4 w-4 text-destructive" />}
          title={`${moduleName(gate.moduleId)} could not be computed`}
          detail={gate.error ?? undefined}
          action={retry}
        />
      );

    case "cancelled":
      return (
        <Notice
          compact={compact}
          title={`${moduleName(gate.moduleId)} was cancelled`}
          detail="Run it again to see its results."
          action={
            <Button
              size={compact ? "sm" : "default"}
              className="cursor-pointer"
              disabled={run.isPending}
              onClick={() => run.mutate(true)}
            >
              <Play className="mr-1.5 h-3.5 w-3.5" />
              Run
            </Button>
          }
        />
      );

    case "unavailable":
      return (
        <Notice
          compact={compact}
          title={`${moduleName(gate.moduleId)} is not available for this process`}
          detail={gate.reason ?? undefined}
        />
      );

    case "importing":
      return (
        <Notice
          compact={compact}
          title="Available once the import finishes"
          detail="This module will start computing as soon as the process is ready."
        />
      );

    case "error":
      return (
        <Notice
          compact={compact}
          icon={<AlertTriangle className="h-4 w-4 text-destructive" />}
          title="Couldn't start the analysis"
          detail={gate.message}
          action={retry}
        />
      );
  }
}

/** Skeleton for the first moments, then a live progress card with Cancel. */
function Computing({
  gate,
  moduleId,
  compact,
}: {
  gate: Extract<RunGate, { kind: "starting" } | { kind: "computing" }>;
  moduleId: string;
  compact: boolean;
}) {
  const moduleName = useModuleNames();
  const cancel = useCancelJob();
  const showProgress = useAfter(PROGRESS_AFTER_MS);

  const jobId = gate.kind === "computing" ? gate.jobId : null;
  const runningModule = gate.kind === "computing" ? gate.moduleId : moduleId;
  // The store is fed by the app-wide job stream, so progress is live without a
  // per-job subscription. A job it has not seen (a missed `job.queued`) simply
  // reads as indeterminate until its first tick.
  const job = useJobsStore((s) => (jobId ? s.byId.get(jobId) : undefined));

  if (!showProgress) {
    return compact ? <Skeleton className="h-full min-h-24 w-full" /> : <PanelSkeleton />;
  }

  const progress = job ? jobProgress(job) : null;
  // A sub-second "~42 ms left" is noise, not information: only say it once it helps.
  const eta = progress?.eta != null && progress.eta >= 2 ? progress.eta : null;
  const detail = job?.message || (job?.stage ? stageLabel(job.stage) : "");

  return (
    <div
      role="status"
      aria-live="polite"
      className={cn(
        "flex flex-col items-center justify-center text-center",
        compact
          ? "h-full gap-2 px-2"
          : "gap-3 rounded-xl border border-dashed border-border bg-card/40 px-6 py-16",
      )}
    >
      <div className="flex items-center gap-2 text-sm font-medium">
        <Loader2 className="h-4 w-4 animate-spin text-primary" />
        Computing {moduleName(runningModule)}…
      </div>
      <div className={cn("w-full space-y-1", compact ? "max-w-48" : "max-w-xs")}>
        <Progress
          value={progress?.pct ?? undefined}
          className={progress?.pct == null ? "h-1.5 animate-pulse" : "h-1.5"}
        />
        <div className="flex items-center justify-between text-[11px] text-muted-foreground tabular-nums">
          <span>{progress?.pct != null ? progress.label : "Working…"}</span>
          <span>{eta ? `${formatDuration(eta)} left` : ""}</span>
        </div>
      </div>
      {detail && !compact && <p className="text-xs text-muted-foreground">{detail}</p>}
      {!compact && (
        <p className="text-xs text-muted-foreground">
          Started when you opened this. It runs once - next time it&apos;s instant.
        </p>
      )}
      {jobId && (
        <Button
          size="sm"
          variant="ghost"
          className="cursor-pointer text-muted-foreground"
          disabled={cancel.isPending}
          onClick={() => cancel.mutate(jobId)}
        >
          Cancel
        </Button>
      )}
    </div>
  );
}

/** A quiet, static state: what happened and the one thing to do about it. */
function Notice({
  title,
  detail,
  action,
  icon,
  compact,
}: {
  title: string;
  detail?: string;
  action?: React.ReactNode;
  icon?: React.ReactNode;
  compact: boolean;
}) {
  return (
    <div
      className={cn(
        "flex flex-col items-center justify-center text-center",
        compact
          ? "h-full gap-1.5 px-2"
          : "gap-2 rounded-xl border border-dashed border-border bg-card/40 px-6 py-16",
      )}
    >
      <div className="flex items-center gap-2 text-sm font-medium">
        {icon}
        {title}
      </div>
      {detail && (
        <p
          className={cn(
            "max-w-md break-words text-muted-foreground",
            compact ? "line-clamp-3 text-[11px]" : "text-sm",
          )}
        >
          {detail}
        </p>
      )}
      {action && <div className="mt-1">{action}</div>}
    </div>
  );
}

/** `true` once `ms` have passed since mount. */
function useAfter(ms: number): boolean {
  const [elapsed, setElapsed] = useState(false);
  useEffect(() => {
    const t = setTimeout(() => setElapsed(true), ms);
    return () => clearTimeout(t);
  }, [ms]);
  return elapsed;
}
