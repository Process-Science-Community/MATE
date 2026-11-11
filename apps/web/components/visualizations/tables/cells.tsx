"use client";

import type { ReactNode } from "react";

import { formatValue, type ValueFormat } from "@/lib/format";

/** Option readers shared by the table, list, graph and text vizzes. Options
 * come from persisted card config, so every reader tolerates a wrong type. */
export const optStr = (o: Record<string, unknown>, k: string, d = ""): string =>
  typeof o[k] === "string" ? (o[k] as string) : d;
export const optBool = (o: Record<string, unknown>, k: string, d: boolean): boolean =>
  typeof o[k] === "boolean" ? (o[k] as boolean) : d;
export const optNum = (o: Record<string, unknown>, k: string, d: number): number => {
  const n = typeof o[k] === "number" ? (o[k] as number) : Number(o[k]);
  return o[k] != null && o[k] !== "" && Number.isFinite(n) ? n : d;
};
/** A column-list option: a string[] from the multiselect, or "a, b" typed text. */
export function optList(o: Record<string, unknown>, k: string): string[] {
  const v = o[k];
  if (Array.isArray(v)) return v.filter((s): s is string => typeof s === "string");
  return typeof v === "string" ? v.split(",").map((s) => s.trim()).filter(Boolean) : [];
}

/** An array of objects (e.g. histogram buckets) has no readable chip form. */
export const isObjectList = (v: unknown[]): boolean => v.some((it) => it !== null && typeof it === "object");

/** `formatValue` for cells: empty stays empty, arrays read `A → B` (object
 * arrays as "n items"), and a
 * string without an explicit format stays as-is (ids like "00123" or "2024"). */
export function formatAs(v: unknown, fmt?: string): string {
  if (v == null || v === "") return "";
  if (Array.isArray(v)) return isObjectList(v) ? `${v.length} items` : v.map(String).join(" → ");
  return formatValue(v, (fmt as ValueFormat) ?? (typeof v === "number" ? undefined : "text"));
}

export function Chip({ children, title }: { children: ReactNode; title?: string }) {
  return (
    <span
      title={title}
      className="inline-flex max-w-full items-center truncate rounded-full border border-border bg-muted/60 px-1.5 py-px text-[11px] leading-4"
    >
      {children}
    </span>
  );
}

/** An array cell as chips (`A → B → C`), cut at `max` with "+n more". */
export function SequenceChips({
  items,
  max = 12,
  arrows = true,
}: {
  items: unknown[];
  max?: number;
  arrows?: boolean;
}) {
  const shown = items.slice(0, max);
  return (
    <span className="inline-flex flex-wrap items-center gap-1">
      {shown.map((it, i) => (
        <span key={i} className="inline-flex items-center gap-1">
          {arrows && i > 0 && <span className="text-[10px] text-muted-foreground">→</span>}
          <Chip>{String(it)}</Chip>
        </span>
      ))}
      {items.length > shown.length && (
        <span className="text-[11px] text-muted-foreground">+{items.length - shown.length} more</span>
      )}
    </span>
  );
}
