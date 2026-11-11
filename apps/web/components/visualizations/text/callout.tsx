"use client";

import dynamic from "next/dynamic";
import { CircleCheck, Info, OctagonAlert, TriangleAlert, type LucideIcon } from "lucide-react";

import type { BlobData, DatasetEnvelope, KpiData, VizComponentProps } from "@/lib/visualizations/types";
import { VizEmpty } from "@/components/visualizations/viz-shell";
import { statusColor } from "@/components/dashboards/kit/palette";
import { asTable, columnFormat, formatMeasure, formatValue } from "@/components/visualizations/charts/chart-common";
import { SequenceChips, formatAs, optBool, optStr } from "@/components/visualizations/tables/cells";

// The markdown libraries stay a lazy chunk, as for the text element.
const Markdown = dynamic(
  () => import("@/components/modules/guide-markdown").then((m) => m.Markdown),
  { loading: () => null },
);

const TONES: Record<string, { icon: LucideIcon; color: string } | undefined> = {
  info: { icon: Info, color: "var(--primary)" },
  success: { icon: CircleCheck, color: statusColor("good") },
  warning: { icon: TriangleAlert, color: statusColor("warning") },
  danger: { icon: OctagonAlert, color: statusColor("critical") },
};

/** Values `{field}` can reference: the first table row's columns, every KPI
 * measure key (formatted), or a text blob as `{value}`. */
function fieldsOf(d: DatasetEnvelope, options: Record<string, unknown>): Record<string, string> {
  if (d.shape === "kpi") {
    return Object.fromEntries((d.data as KpiData).items.map((m) => [m.key, formatMeasure(m, options)]));
  }
  if (d.shape === "blob") return { value: (d.data as BlobData).value ?? "" };
  const row = asTable(d).rows[0] ?? {};
  return Object.fromEntries(Object.entries(row).map(([k, v]) => [k, formatAs(v)]));
}

/** The text shown when no template is set: the blob text, the first text
 * column of the first row, or the KPI measures as "**Label**: value" lines. */
function fallback(d: DatasetEnvelope, options: Record<string, unknown>): string {
  if (d.shape === "blob") return (d.data as BlobData).value ?? "";
  if (d.shape === "kpi") {
    return (d.data as KpiData).items.map((m) => `**${m.label}**: ${formatMeasure(m, options)}`).join("  \n");
  }
  const t = asTable(d);
  const row = t.rows[0];
  if (!row) return "";
  const col = t.columns.find((c) => c.type === "string" && typeof row[c.id] === "string");
  if (col) return String(row[col.id]);
  // No text column (a time series, stats): the first row as "**Label**: value" lines.
  return t.columns
    .map((c) => `**${c.label}**: ${formatValue(row[c.id], columnFormat(t.columns, c.id, options))}`)
    .join("  \n");
}

/** Text & status callout: markdown with `{field}` placeholders filled from the
 * data, an optional chip list from an array column of the first row, and a
 * tone (info / success / warning / danger, or none for plain text). */
export function CalloutViz({ dataset, options }: VizComponentProps) {
  const o = options ?? {};
  const template = optStr(o, "text");
  const fields = fieldsOf(dataset, o);
  const body = template
    ? template.replace(/\{([\w.-]+)\}/g, (m, k: string) => fields[k] ?? m)
    : fallback(dataset, o);
  const listField = optStr(o, "list_field");
  const list = listField ? asTable(dataset).rows[0]?.[listField] : undefined;
  const items = Array.isArray(list) ? list : [];
  const tone = TONES[optStr(o, "tone", "info")];

  if (!body.trim() && items.length === 0) {
    return optBool(o, "hide_when_empty", false) ? null : <VizEmpty message={dataset.meta?.note ?? "Nothing to report."} />;
  }
  const Icon = tone?.icon;
  return (
    <div className="h-full w-full overflow-auto p-2">
      <div
        role={tone ? "note" : undefined}
        className="flex gap-2 rounded-md border px-3 py-2 text-xs"
        style={
          tone
            ? {
                borderColor: `color-mix(in oklab, ${tone.color} 40%, transparent)`,
                background: `color-mix(in oklab, ${tone.color} 7%, transparent)`,
              }
            : { borderColor: "transparent" }
        }
      >
        {Icon && <Icon className="mt-0.5 h-4 w-4 shrink-0" style={{ color: tone?.color }} aria-hidden />}
        <div className="min-w-0 flex-1 [&>*:first-child]:mt-0 [&>*:last-child]:mb-0">
          {body.trim() && <Markdown text={body} />}
          {items.length > 0 && (
            <div className="mt-1.5">
              <SequenceChips items={items} arrows={false} />
            </div>
          )}
        </div>
      </div>
    </div>
  );
}
