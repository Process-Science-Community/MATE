"use client";

import { useMemo, useState, type ReactNode } from "react";
import { ArrowDown, ArrowUp, ChevronLeft, ChevronRight, Search } from "lucide-react";

import {
  Table,
  TableBody,
  TableCell,
  TableHead,
  TableHeader,
  TableRow,
} from "@/components/ui/table";
import { Input } from "@/components/ui/input";
import { formatNumber } from "@/lib/format";
import type { ColumnSpec, VizComponentProps } from "@/lib/visualizations/types";
import { VizEmpty } from "@/components/visualizations/viz-shell";
import { seriesColor } from "@/components/dashboards/kit/palette";
import { asTable, durationKeys, numericCols } from "@/components/visualizations/charts/chart-common";
import { Chip, SequenceChips, formatAs, isObjectList, optBool, optList, optNum, optStr } from "./cells";

/** Numbers numerically, everything else as text; empty cells always last. */
function compare(a: unknown, b: unknown): number {
  if (a == null || a === "") return b == null || b === "" ? 0 : 1;
  if (b == null || b === "") return -1;
  if (typeof a === "number" && typeof b === "number") return a - b;
  return String(a).localeCompare(String(b), undefined, { numeric: true });
}

type Sort = { col: string; dir: "asc" | "desc" } | null;

interface ColumnView {
  col: ColumnSpec;
  format?: string;
  /** Max |value| when the column shows data bars. */
  barMax?: number;
  chip: boolean;
  link: boolean;
}

function href(value: unknown, template: string): string | undefined {
  const v = String(value);
  if (template) return template.replaceAll("{value}", encodeURIComponent(v));
  return /^(https?:\/\/|\/)/.test(v) ? v : undefined;
}

/** Typed table for any `table`-shaped dataset. Header click sorts (asc, desc,
 * off); optional search and pagination; per-column formats (duration, percent,
 * datetime), data bars, chips and links come from the viz options. Array cells
 * always render as `A → B` chips. */
export function TableViz({ dataset, options }: VizComponentProps) {
  const o = options ?? {};
  const table = asTable(dataset);
  const [sort, setSort] = useState<Sort>(null);
  const [query, setQuery] = useState("");
  const [page, setPage] = useState(0);
  const searchable = optBool(o, "search", false);
  const pageSize = Math.max(0, Math.round(optNum(o, "page_size", 50)));
  const template = optStr(o, "link_template");

  const views: ColumnView[] = useMemo(() => {
    const durations = durationKeys(o);
    const percent = optList(o, "percent_columns");
    const datetime = optList(o, "datetime_columns");
    const bars = optBool(o, "data_bars", false)
      ? numericCols(table.columns).map((c) => c.id)
      : optList(o, "bar_columns");
    const chips = optList(o, "chip_columns");
    const link = optStr(o, "link_column");
    return table.columns.map((col) => {
      // reduce, not Math.max(...): spreading >~125k rows overflows the stack.
      const absMax = table.rows.reduce((m, r) => {
        const n = Number(r[col.id]);
        return Number.isFinite(n) ? Math.max(m, Math.abs(n)) : m;
      }, 0);
      const format =
        col.type === "duration" || durations.includes(col.id)
          ? "duration"
          : percent.includes(col.id)
            ? absMax <= 1 ? "percent" : "percent100" // fractions vs already-×100 values
            : datetime.includes(col.id)
              ? "datetime"
              : col.format
                ? col.format
                : col.type === "number" || col.type === "integer"
                  ? "number"
                  : undefined;
      return {
        col,
        format,
        barMax: bars.includes(col.id) && absMax > 0 ? absMax : undefined,
        chip: chips.includes(col.id),
        link: link === col.id,
      };
    });
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [table, JSON.stringify(o)]);

  const filtered = useMemo(() => {
    const q = query.trim().toLowerCase();
    if (!q) return table.rows;
    return table.rows.filter((r) =>
      table.columns.some((c) => formatAs(r[c.id], "text").toLowerCase().includes(q)),
    );
  }, [table, query]);

  const sorted = useMemo(() => {
    if (!sort) return filtered;
    const sign = sort.dir === "asc" ? 1 : -1;
    return [...filtered].sort((ra, rb) => {
      const a = ra[sort.col];
      const b = rb[sort.col];
      // Empty cells stay last in both directions.
      if (a == null || a === "" || b == null || b === "") return compare(a, b);
      return sign * compare(a, b);
    });
  }, [filtered, sort]);

  if (table.columns.length === 0 || table.rows.length === 0) {
    return <VizEmpty message={dataset.meta?.note} />;
  }
  const pages = pageSize > 0 ? Math.max(1, Math.ceil(sorted.length / pageSize)) : 1;
  const current = Math.min(page, pages - 1);
  const rows = pageSize > 0 ? sorted.slice(current * pageSize, (current + 1) * pageSize) : sorted;
  const cycle = (col: string) =>
    setSort((s) =>
      s?.col !== col ? { col, dir: "asc" } : s.dir === "asc" ? { col, dir: "desc" } : null,
    );

  const render = (v: ColumnView, value: unknown): ReactNode => {
    if (value == null || value === "") return "";
    if (Array.isArray(value)) return isObjectList(value) ? formatAs(value) : <SequenceChips items={value} />;
    // Numeric-looking strings (ids like "00123") are typed integer but stay as-is.
    const text = formatAs(value, typeof value !== "number" && v.format === "number" ? undefined : v.format);
    let out: ReactNode = v.chip ? <Chip>{text}</Chip> : text;
    const url = v.link ? href(value, template) : undefined;
    if (url) {
      out = (
        <a href={url} className="text-primary underline-offset-2 hover:underline" {...(url.startsWith("/") ? {} : { target: "_blank", rel: "noreferrer" })}>
          {out}
        </a>
      );
    }
    const n = Number(value);
    if (v.barMax && Number.isFinite(n)) {
      return (
        <div className="relative min-w-16">
          <div
            aria-hidden
            className="absolute inset-y-0 left-0 rounded-sm"
            style={{
              width: `${(Math.abs(n) / v.barMax) * 100}%`,
              background: `color-mix(in oklab, ${seriesColor(0)} 28%, transparent)`,
            }}
          />
          <span className="relative px-1">{out}</span>
        </div>
      );
    }
    return out;
  };

  return (
    <div className="flex h-full w-full flex-col">
      {searchable && (
        <div className="relative shrink-0 p-1.5">
          <Search className="pointer-events-none absolute left-3.5 top-1/2 h-3.5 w-3.5 -translate-y-1/2 text-muted-foreground" aria-hidden />
          <Input
            value={query}
            onChange={(e) => {
              setQuery(e.target.value);
              setPage(0);
            }}
            placeholder="Search rows"
            aria-label="Search rows"
            className="h-7 pl-7 text-xs"
          />
        </div>
      )}
      <div className="min-h-0 flex-1 overflow-auto">
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
                {views.map((v) => (
                  <TableCell key={v.col.id} className="whitespace-nowrap py-1.5 text-xs tabular-nums">
                    {render(v, r[v.col.id])}
                  </TableCell>
                ))}
              </TableRow>
            ))}
          </TableBody>
        </Table>
        {sorted.length === 0 && <VizEmpty message="No rows match." />}
      </div>
      {(pages > 1 || dataset.meta?.truncated) && (
        <div className="flex shrink-0 items-center justify-between gap-2 border-t border-border/60 px-2 py-1 text-[11px] text-muted-foreground">
          <span>
            {pages > 1
              ? `${formatNumber(current * pageSize + 1)}–${formatNumber(current * pageSize + rows.length)} of ${formatNumber(sorted.length)}`
              : `${formatNumber(sorted.length)} rows`}
            {dataset.meta?.truncated && " (source truncated)"}
          </span>
          {pages > 1 && (
            <span className="flex items-center gap-1">
              <button
                type="button"
                aria-label="Previous page"
                disabled={current === 0}
                onClick={() => setPage(current - 1)}
                className="rounded p-0.5 hover:bg-muted disabled:opacity-40"
              >
                <ChevronLeft className="h-3.5 w-3.5" />
              </button>
              <button
                type="button"
                aria-label="Next page"
                disabled={current >= pages - 1}
                onClick={() => setPage(current + 1)}
                className="rounded p-0.5 hover:bg-muted disabled:opacity-40"
              >
                <ChevronRight className="h-3.5 w-3.5" />
              </button>
            </span>
          )}
        </div>
      )}
    </div>
  );
}
