"use client";

import type { DatasetPreview as Preview } from "@/lib/api-types";
import { formatNumber } from "@/lib/format";
import {
  Table,
  TableBody,
  TableCell,
  TableHead,
  TableHeader,
  TableRow,
} from "@/components/ui/table";

function cell(v: unknown): string {
  if (v === null || v === undefined) return "";
  if (typeof v === "object") return JSON.stringify(v);
  return String(v);
}

/** Publish-time snapshot: top activities, top variants, first rows. */
export function DatasetPreview({ preview }: { preview: Preview }) {
  const activities = preview.top_activities ?? [];
  const variants = preview.top_variants ?? [];
  const rows = preview.rows;
  const maxCount = Math.max(1, ...activities.map((a) => a.count));

  return (
    <div className="space-y-6">
      <div className="grid gap-6 lg:grid-cols-2">
        <div className="min-w-0 space-y-2">
          <h3 className="text-xs font-semibold uppercase tracking-wide text-muted-foreground">
            Top activities
          </h3>
          {activities.length === 0 ? (
            <p className="text-xs text-muted-foreground">No activity summary.</p>
          ) : (
            <ul className="space-y-1">
              {activities.map((a) => (
                <li key={a.activity} className="relative overflow-hidden rounded px-2 py-1 text-xs">
                  <span
                    aria-hidden
                    className="absolute inset-y-0 left-0 bg-primary/10"
                    style={{ width: `${(a.count / maxCount) * 100}%` }}
                  />
                  <span className="relative flex items-center gap-2">
                    <span className="min-w-0 flex-1 truncate">{a.activity}</span>
                    <span className="tabular-nums text-muted-foreground">{formatNumber(a.count)}</span>
                  </span>
                </li>
              ))}
            </ul>
          )}
        </div>
        <div className="min-w-0 space-y-2">
          <h3 className="text-xs font-semibold uppercase tracking-wide text-muted-foreground">
            Top variants
          </h3>
          {variants.length === 0 ? (
            <p className="text-xs text-muted-foreground">No variant summary.</p>
          ) : (
            <ol className="space-y-1.5">
              {variants.map((v, i) => (
                <li key={i} className="rounded border border-border/60 px-2 py-1.5 text-xs">
                  <div className="mb-0.5 flex items-center justify-between text-[11px] text-muted-foreground">
                    <span>#{i + 1}</span>
                    <span className="tabular-nums">{formatNumber(v.case_count)} cases</span>
                  </div>
                  <p className="line-clamp-2 break-words" title={v.activities.join(" → ")}>
                    {v.activities.join(" → ")}
                  </p>
                </li>
              ))}
            </ol>
          )}
        </div>
      </div>

      {rows && rows.columns.length > 0 && (
        <div className="space-y-2">
          <h3 className="text-xs font-semibold uppercase tracking-wide text-muted-foreground">
            First {rows.data.length} rows
          </h3>
          <div className="max-h-[420px] overflow-auto rounded-md border">
            <Table className="text-xs">
              <TableHeader className="sticky top-0 bg-background">
                <TableRow>
                  {rows.columns.map((c) => (
                    <TableHead key={c} className="whitespace-nowrap">
                      {c}
                    </TableHead>
                  ))}
                </TableRow>
              </TableHeader>
              <TableBody>
                {rows.data.map((r, i) => (
                  <TableRow key={i}>
                    {r.map((v, j) => (
                      <TableCell key={j} className="max-w-[240px] truncate whitespace-nowrap">
                        {cell(v)}
                      </TableCell>
                    ))}
                  </TableRow>
                ))}
              </TableBody>
            </Table>
          </div>
        </div>
      )}
    </div>
  );
}
