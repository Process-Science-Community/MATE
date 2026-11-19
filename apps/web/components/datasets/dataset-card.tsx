"use client";

import Link from "next/link";
import { CalendarRange, Lock, Users } from "lucide-react";

import type { DatasetSummary } from "@/lib/api-types";
import { formatDateRange, formatNumber } from "@/lib/format";
import { Card } from "@/components/ui/card";
import { LicenseBadge } from "./license-badge";

/** One catalog tile; the whole card links to the dataset page. */
export function DatasetCard({ ds }: { ds: DatasetSummary }) {
  const notPublic = ds.visibility !== "public";
  return (
    <Link
      href={`/datasets/${ds.namespace}/${ds.name}`}
      className="group block rounded-xl focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring"
    >
      <Card className="flex h-full flex-col gap-3 p-4 transition-colors group-hover:border-primary/40">
        <div className="min-w-0 space-y-1">
          <div className="flex items-start gap-2">
            <h3 className="min-w-0 flex-1 truncate text-sm font-semibold leading-tight">
              {ds.title}
            </h3>
            {notPublic && (
              <span
                className="inline-flex shrink-0 items-center gap-1 text-[10px] uppercase tracking-wide text-muted-foreground"
                title={ds.visibility === "private" ? "Only curators can see this" : "Visible to one team"}
              >
                <Lock className="h-3 w-3" />
                {ds.visibility}
              </span>
            )}
          </div>
          <p className="truncate font-mono text-[11px] text-muted-foreground">{ds.slug}</p>
        </div>
        {ds.summary && (
          <p className="line-clamp-2 text-xs text-muted-foreground">{ds.summary}</p>
        )}
        <div className="mt-auto flex flex-wrap items-center gap-x-3 gap-y-1.5 text-[11px] text-muted-foreground">
          <LicenseBadge license={ds.license} />
          {ds.status !== "active" && (
            <span className="rounded-md bg-destructive/10 px-1.5 py-0.5 font-medium text-destructive">
              disabled
            </span>
          )}
          <span className="tabular-nums">
            {formatNumber(ds.events_count)} events · {formatNumber(ds.cases_count)} cases
          </span>
          {ds.date_min && ds.date_max && (
            <span className="inline-flex items-center gap-1">
              <CalendarRange className="h-3 w-3" />
              {formatDateRange(ds.date_min, ds.date_max)}
            </span>
          )}
          <span className="ml-auto inline-flex items-center gap-1 tabular-nums" title="Users with a copy">
            <Users className="h-3 w-3" />
            {formatNumber(ds.uses)}
          </span>
        </div>
      </Card>
    </Link>
  );
}
