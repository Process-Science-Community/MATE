"use client";

import Link from "next/link";
import { Check, Circle, Loader2, X } from "lucide-react";

import { Button } from "@/components/ui/button";
import { Progress } from "@/components/ui/progress";
import { cn } from "@/lib/cn";
import { formatNumber } from "@/lib/format";
import { useEventLog } from "@/lib/queries";
import { useImportStages } from "@/components/processes/import/use-import-stages";

export interface QueuedImport {
  logId: string;
  jobId: string;
  name: string;
}

/**
 * Several imports from one archive, one row each. Rows settle independently -
 * a log opens as soon as its own job is done, whatever its siblings are doing.
 */
export function BatchImportProgress({
  imports,
  onReset,
}: {
  imports: QueuedImport[];
  onReset: () => void;
}) {
  return (
    <div className="space-y-4">
      <header className="space-y-1">
        <h2 className="text-base font-semibold">
          {imports.length === 1 ? "Importing 1 log" : `Importing ${imports.length} logs`}
        </h2>
        <p className="text-sm text-muted-foreground">
          Each log opens as soon as it is ready. You can leave this page - the jobs drawer keeps
          track of them.
        </p>
      </header>

      <ul className="space-y-1">
        {imports.map((item) => (
          <ImportRow key={item.logId} item={item} />
        ))}
      </ul>

      <div className="flex flex-wrap justify-end gap-2 border-t border-border pt-4">
        <Button variant="outline" asChild className="cursor-pointer">
          <Link href="/processes">All processes</Link>
        </Button>
        <Button onClick={onReset} className="cursor-pointer">
          Import more
        </Button>
      </div>
    </div>
  );
}

function ImportRow({ item }: { item: QueuedImport }) {
  const { stages, failed } = useImportStages({
    uploadPct: null,
    probing: false,
    staged: true,
    jobId: item.jobId,
  });
  const { data: log } = useEventLog(item.logId);

  const isFailed = failed || log?.status === "failed";
  const isReady = !isFailed && log?.status === "ready";
  const active = stages.find((s) => s.state === "active");

  let detail: string;
  if (isFailed) detail = log?.error ?? "The import failed.";
  else if (isReady) {
    detail =
      log.log_model === "object_centric"
        ? `${formatNumber(log.events_count)} events · ${formatNumber(log.objects_count)} objects`
        : `${formatNumber(log.events_count)} events · ${formatNumber(log.cases_count)} cases`;
  } else if (active) detail = active.detail ? `${active.label} · ${active.detail}` : active.label;
  else detail = "Queued";

  return (
    <li className="flex items-start gap-3 rounded-md px-2 py-2">
      {isFailed ? (
        <X className="mt-0.5 h-4 w-4 shrink-0 text-destructive" />
      ) : isReady ? (
        <Check className="mt-0.5 h-4 w-4 shrink-0 animate-in zoom-in-50 text-chart-2 duration-200" />
      ) : active ? (
        <Loader2 className="mt-0.5 h-4 w-4 shrink-0 animate-spin text-foreground" />
      ) : (
        <Circle className="mt-0.5 h-4 w-4 shrink-0 text-muted-foreground/40" />
      )}
      <div className="min-w-0 flex-1 space-y-1">
        <div className="truncate text-sm font-medium">{item.name}</div>
        <div
          className={cn(
            "text-xs",
            isFailed ? "text-destructive" : "tabular-nums text-muted-foreground",
          )}
        >
          {detail}
        </div>
        {!isFailed && !isReady && active && (
          <Progress
            value={active.pct ?? undefined}
            className={cn("h-1", (active.pct === null || active.pct === undefined) && "animate-pulse")}
          />
        )}
      </div>
      {isReady && (
        <Button variant="ghost" size="sm" asChild className="shrink-0 cursor-pointer">
          <Link href={`/processes/${item.logId}`}>Open</Link>
        </Button>
      )}
    </li>
  );
}
