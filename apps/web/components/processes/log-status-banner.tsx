"use client";

import { AlertTriangle, Loader2 } from "lucide-react";

import type { EventLogDetail } from "@/lib/api-types";

/**
 * The lifecycle strip above a process's tabs.
 *
 * A log is `ready` the moment it is parsed - modules compute lazily, the first time
 * one is opened - so the only states worth a strip are the two ends: still being
 * read, or failed. (`processing`, the old "precomputing every module" gate, is
 * never written any more.)
 */
export function LogStatusBanner({ log }: { log: EventLogDetail }) {
  if (log.status === "importing") {
    return (
      <Strip>
        <Loader2 className="h-3.5 w-3.5 shrink-0 animate-spin" />
        <span>
          Reading your file — parsing events and building the case index. Modules and analytics
          unlock once this finishes.
        </span>
      </Strip>
    );
  }

  if (log.status === "failed") {
    return (
      <div className="mb-4 flex items-start gap-2 rounded-lg border border-destructive/20 bg-destructive/10 px-3 py-2 text-sm text-destructive">
        <AlertTriangle className="mt-0.5 h-4 w-4 shrink-0" />
        <span>Import failed: {log.error ?? "Unknown error"}</span>
      </div>
    );
  }

  return null;
}

function Strip({ children }: { children: React.ReactNode }) {
  return (
    <div className="mb-4 flex items-center gap-2 rounded-lg border border-border bg-card px-3 py-2 text-sm text-muted-foreground">
      {children}
    </div>
  );
}
