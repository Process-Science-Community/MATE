"use client";

import Link from "next/link";
import { Check, Circle, Loader2, Minus, X } from "lucide-react";

import { CountUp } from "@/components/count-up";
import { Button } from "@/components/ui/button";
import { Progress } from "@/components/ui/progress";
import type { EventLogDetail } from "@/lib/api-types";
import { cn } from "@/lib/cn";
import type { ImportStage, StageState } from "@/components/processes/import/use-import-stages";

/**
 * The live import, as a checklist. Deliberately mirrors the jobs drawer's
 * icon/state vocabulary (`job-child-row.tsx`) so the same import doesn't look
 * like two different things in two places.
 *
 * The log opens the moment it is saved: modules compute lazily, so there is no
 * "preparing" phase to wait out.
 */
export function ImportProgress({
  stages,
  fileName,
  error,
  log,
  logId,
  onRetry,
}: {
  stages: ImportStage[];
  fileName: string;
  error?: string | null;
  /** Set once the log row is readable – powers the finished summary. */
  log?: EventLogDetail | null;
  logId: string | null;
  onRetry: () => void;
}) {
  const ready = log?.status === "ready";

  return (
    <div className="space-y-5">
      <header className="space-y-1">
        <h2 className="text-base font-semibold">
          {ready ? "Import complete" : error ? "Import failed" : "Importing"}
        </h2>
        <p className="text-sm text-muted-foreground">
          {ready
            ? "Your process is ready to explore."
            : error
              ? error
              : `${fileName} - you can leave this page, the import keeps running.`}
        </p>
      </header>

      <ol className="space-y-1">
        {stages.map((stage) => (
          <StageRow key={stage.key} stage={stage} />
        ))}
      </ol>

      {ready && log && <ReadySummary log={log} />}

      <div className="flex flex-wrap justify-end gap-2 border-t border-border pt-4">
        {error ? (
          <>
            <Button variant="outline" asChild className="cursor-pointer">
              <Link href="/processes">Back to processes</Link>
            </Button>
            <Button onClick={onRetry} className="cursor-pointer">
              Try again
            </Button>
          </>
        ) : (
          <>
            <Button variant="outline" asChild className="cursor-pointer">
              <Link href="/processes">All processes</Link>
            </Button>
            {ready && logId ? (
              <Button asChild className="cursor-pointer">
                <Link href={`/processes/${logId}`}>Open process</Link>
              </Button>
            ) : (
              <Button disabled>Open process</Button>
            )}
          </>
        )}
      </div>
    </div>
  );
}

function StageRow({ stage }: { stage: ImportStage }) {
  const active = stage.state === "active";

  return (
    <li
      className={cn(
        "flex items-start gap-2.5 rounded-md px-2 py-2 transition-colors",
        active && "bg-accent/40",
      )}
    >
      <StageIcon state={stage.state} />
      <div className="min-w-0 flex-1 space-y-1">
        <div className="flex items-center justify-between gap-2">
          <span
            className={cn(
              "truncate text-sm",
              stage.state === "pending" && "text-muted-foreground",
              stage.state === "skipped" && "text-muted-foreground line-through",
              active && "font-medium",
            )}
          >
            {stage.label}
          </span>
          {stage.detail && (
            <span
              className={cn(
                "shrink-0 text-[11px] tabular-nums",
                stage.state === "failed" ? "text-destructive" : "text-muted-foreground",
              )}
            >
              {stage.detail}
            </span>
          )}
        </div>
        {active && (
          <Progress
            value={stage.pct ?? undefined}
            className={cn("h-1", (stage.pct === null || stage.pct === undefined) && "animate-pulse")}
          />
        )}
      </div>
    </li>
  );
}

function StageIcon({ state, className }: { state: StageState; className?: string }) {
  const base = cn("mt-0.5 h-4 w-4 shrink-0", className);
  if (state === "active") {
    return <Loader2 className={cn(base, "animate-spin text-foreground")} />;
  }
  if (state === "done") {
    return <Check className={cn(base, "animate-in zoom-in-50 text-chart-2 duration-200")} />;
  }
  if (state === "failed") {
    return <X className={cn(base, "text-destructive")} />;
  }
  if (state === "skipped") {
    return <Minus className={cn(base, "text-muted-foreground/40")} />;
  }
  return <Circle className={cn(base, "text-muted-foreground/40")} />;
}

function ReadySummary({ log }: { log: EventLogDetail }) {
  const stats: { label: string; value: number | null }[] =
    log.log_model === "object_centric"
      ? [
          { label: "Events", value: log.events_count },
          { label: "Objects", value: log.objects_count },
          { label: "Object types", value: log.object_types_count },
        ]
      : [
          { label: "Events", value: log.events_count },
          { label: "Cases", value: log.cases_count },
          { label: "Variants", value: log.variants_count },
        ];

  return (
    <div className="grid grid-cols-3 gap-3 rounded-lg border border-chart-2/30 bg-chart-2/5 p-4">
      {stats.map((stat) => (
        <div key={stat.label} className="space-y-0.5">
          <div className="text-xl font-semibold tabular-nums">
            <CountUp value={stat.value} />
          </div>
          <div className="text-xs text-muted-foreground">{stat.label}</div>
        </div>
      ))}
    </div>
  );
}
