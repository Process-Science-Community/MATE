"use client";

import { useMemo, useState } from "react";
import { ArrowDown, ArrowUp } from "lucide-react";

import {
  Table,
  TableBody,
  TableCell,
  TableHead,
  TableHeader,
  TableRow,
} from "@/components/ui/table";
import { formatNumber } from "@/lib/format";
import type { ColumnType, VizComponentProps } from "@/lib/visualizations/types";
import { VizEmpty } from "@/components/visualizations/viz-shell";
import {
  asTable,
  durationKeys,
  formatDurationShort,
} from "@/components/visualizations/charts/chart-common";

const MAX_ROWS = 200;

function cell(value: unknown, type: ColumnType, duration: boolean): string {
  if (value == null) return "";
  if (typeof value === "number") {
    if (duration) return formatDurationShort(value);
    if (type === "number" || type === "integer") return formatNumber(value);
  }
  return String(value);
}

/** Numbers numerically, everything else as text; empty cells always last. */
function compare(a: unknown, b: unknown): number {
  if (a == null || a === "") return b == null || b === "" ? 0 : 1;
  if (b == null || b === "") return -1;
  if (typeof a === "number" && typeof b === "number") return a - b;
  return String(a).localeCompare(String(b), undefined, { numeric: true });
}

type Sort = { col: string; dir: "asc" | "desc" } | null;

/** Plain typed table - renders any `table`-shaped dataset, capped for size.
 * Click a header to sort (ascending, descending, off); columns typed
 * `duration` or picked in the viz options show seconds as "3h 4m". */
export function TableViz({ dataset, options }: VizComponentProps) {
  const table = asTable(dataset);
  const [sort, setSort] = useState<Sort>(null);
  const durations = durationKeys(options);

  const sorted = useMemo(() => {
    if (!sort) return table.rows;
    const sign = sort.dir === "asc" ? 1 : -1;
    return [...table.rows].sort((ra, rb) => {
      const a = ra[sort.col];
      const b = rb[sort.col];
      // Empty cells stay last in both directions.
      if (a == null || a === "" || b == null || b === "") return compare(a, b);
      return sign * compare(a, b);
    });
  }, [table.rows, sort]);

  if (table.columns.length === 0 || table.rows.length === 0) {
    return <VizEmpty message={dataset.meta?.note} />;
  }
  const rows = sorted.slice(0, MAX_ROWS);
  const cycle = (col: string) =>
    setSort((s) =>
      s?.col !== col ? { col, dir: "asc" } : s.dir === "asc" ? { col, dir: "desc" } : null,
    );

  return (
    <div className="h-full w-full overflow-auto">
      <Table>
        <TableHeader className="sticky top-0 z-[1] bg-card">
          <TableRow>
            {table.columns.map((c) => {
              const dir = sort?.col === c.id ? sort.dir : null;
              return (
                <TableHead
                  key={c.id}
                  aria-sort={dir === "asc" ? "ascending" : dir === "desc" ? "descending" : "none"}
                  className="h-8 whitespace-nowrap text-xs"
                >
                  <button
                    type="button"
                    onClick={() => cycle(c.id)}
                    className="inline-flex cursor-pointer items-center gap-1 hover:text-foreground"
                  >
                    {c.label}
                    {dir === "asc" && <ArrowUp className="h-3 w-3" aria-hidden />}
                    {dir === "desc" && <ArrowDown className="h-3 w-3" aria-hidden />}
                  </button>
                </TableHead>
              );
            })}
          </TableRow>
        </TableHeader>
        <TableBody>
          {rows.map((r, i) => (
            <TableRow key={i}>
              {table.columns.map((c) => (
                <TableCell key={c.id} className="whitespace-nowrap py-1.5 text-xs tabular-nums">
                  {cell(r[c.id], c.type, c.type === "duration" || durations.includes(c.id))}
                </TableCell>
              ))}
            </TableRow>
          ))}
        </TableBody>
      </Table>
      {table.rows.length > rows.length && (
        <p className="p-2 text-center text-[11px] text-muted-foreground">
          Showing {rows.length} of {formatNumber(table.rows.length)} rows
        </p>
      )}
    </div>
  );
}
