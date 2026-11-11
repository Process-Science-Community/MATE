"use client";

import { useMemo, useState } from "react";

import type { VizComponentProps } from "@/lib/visualizations/types";
import { VizEmpty } from "@/components/visualizations/viz-shell";
import { seriesColor } from "@/components/dashboards/kit/palette";
import {
  asTable,
  categoryCols,
  columnFormat,
  resolveValue,
} from "@/components/visualizations/charts/chart-common";
import { SequenceChips, formatAs, optBool, optNum, optStr } from "./cells";

interface Item {
  label: unknown;
  value: number | null;
  secondary?: unknown;
}

const keyOf = (v: unknown) => (Array.isArray(v) ? v.join("\u0000") : String(v ?? ""));

/** Ranked list for a `table`-shaped dataset: label (an array label renders as
 * `A → B` chips, e.g. variants), value with an optional bar relative to the
 * max, and a secondary caption. Without a value column it counts rows per
 * label. Top N with "Show all"; `layout: chips` wraps compact label/value chips. */
export function RankedListViz({ dataset, mapping, options }: VizComponentProps) {
  const o = options ?? {};
  const table = asTable(dataset);
  const [all, setAll] = useState(false);
  const cols = table.columns;
  const has = (k: unknown): k is string => typeof k === "string" && cols.some((c) => c.id === k);
  const labelKey = has(mapping.label) ? mapping.label : (categoryCols(cols)[0]?.id ?? cols[0]?.id);
  const valueKey = resolveValue(cols, mapping);
  const secondaryKey = has(mapping.secondary) ? mapping.secondary : undefined;
  const sort = optStr(o, "sort", "desc");
  const topN = Math.max(0, Math.round(optNum(o, "top_n", 10)));
  const showBar = optBool(o, "show_bar", true);
  const chips = optStr(o, "layout") === "chips";
  // Counts are integers; a bound value keeps the chosen format.
  const fmt = optStr(o, "value_format") || (valueKey ? (columnFormat(cols, valueKey) ?? "number") : "integer");

  const items = useMemo(() => {
    if (!labelKey) return [];
    let out: Item[];
    if (valueKey) {
      out = table.rows.map((r) => {
        const n = Number(r[valueKey]);
        return {
          label: r[labelKey],
          value: r[valueKey] != null && Number.isFinite(n) ? n : null,
          secondary: secondaryKey ? r[secondaryKey] : undefined,
        };
      });
    } else {
      const counts = new Map<string, Item>();
      for (const r of table.rows) {
        const k = keyOf(r[labelKey]);
        const it = counts.get(k) ?? { label: r[labelKey], value: 0 };
        it.value = (it.value ?? 0) + 1;
        counts.set(k, it);
      }
      out = [...counts.values()];
    }
    if (sort !== "none") {
      const sign = sort === "asc" ? 1 : -1;
      out.sort((a, b) => (a.value == null ? 1 : b.value == null ? -1 : sign * (a.value - b.value)));
    }
    return out;
  }, [table, labelKey, valueKey, secondaryKey, sort]);

  if (items.length === 0) return <VizEmpty message={dataset.meta?.note} />;
  const shown = all || topN === 0 ? items : items.slice(0, topN);
  const max = Math.max(1e-9, ...items.map((i) => Math.abs(i.value ?? 0)));
  const label = (v: unknown) =>
    Array.isArray(v) ? <SequenceChips items={v} max={8} /> : <span className="truncate">{String(v ?? "–")}</span>;

  const more = shown.length < items.length || all ? (
    <button
      type="button"
      onClick={() => setAll((a) => !a)}
      className="text-[11px] font-medium text-primary hover:underline"
    >
      {all ? "Show less" : `Show all ${items.length}`}
    </button>
  ) : null;

  if (chips) {
    return (
      <div className="h-full w-full overflow-auto p-2">
        <div className="flex flex-wrap items-center gap-1.5">
          {shown.map((it, i) => (
            <span
              key={i}
              className="inline-flex max-w-full items-center gap-1.5 rounded-full border border-border bg-muted/50 px-2 py-0.5 text-xs"
            >
              <span className="h-2 w-2 shrink-0 rounded-full" style={{ background: seriesColor(0) }} aria-hidden />
              {label(it.label)}
              {it.value != null && <span className="tabular-nums text-muted-foreground">{formatAs(it.value, fmt)}</span>}
            </span>
          ))}
          {more}
        </div>
      </div>
    );
  }

  return (
    <div className="h-full w-full overflow-auto px-2 py-1.5">
      <ol className="space-y-1.5">
        {shown.map((it, i) => (
          <li key={i} className="text-xs">
            <div className="flex items-baseline justify-between gap-2">
              <span className="flex min-w-0 items-baseline gap-1.5">
                <span className="w-5 shrink-0 text-right text-[10px] tabular-nums text-muted-foreground">{i + 1}</span>
                {label(it.label)}
              </span>
              {it.value != null && <span className="shrink-0 font-medium tabular-nums">{formatAs(it.value, fmt)}</span>}
            </div>
            {secondaryKey && it.secondary != null && (
              <div className="truncate pl-[1.625rem] text-[11px] text-muted-foreground">{formatAs(it.secondary)}</div>
            )}
            {showBar && it.value != null && (
              <div className="ml-[1.625rem] mt-0.5 h-1.5 rounded-full bg-muted" aria-hidden>
                <div
                  className="h-full rounded-full"
                  style={{ width: `${(Math.abs(it.value) / max) * 100}%`, background: seriesColor(0) }}
                />
              </div>
            )}
          </li>
        ))}
      </ol>
      {more && <div className="mt-1.5 pl-[1.625rem]">{more}</div>}
    </div>
  );
}
