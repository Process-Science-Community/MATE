"use client";

// Sample bars: a dashboard / layout card of module e2e_viz (manifest `frontend.widgets`).
// Imports must come from apps/web/lib/runtime-externals.json (the host provides them).
import { useQuery } from "@tanstack/react-query";
import { Bar, BarChart, CartesianGrid, ResponsiveContainer, Tooltip, XAxis, YAxis } from "recharts";

import { CardEmpty, seriesColor } from "@/components/dashboards/kit";
import { Skeleton } from "@/components/ui/skeleton";
import { api } from "@/lib/api";
import type { WidgetProps } from "@/lib/module-widgets";

// Reads the `rows` dataset's route (manifest `datasets`).
const ROUTE = "/data/rows";

type Row = Record<string, unknown>;

/** The first array of objects in the response: the rows to draw. */
function rowsOf(json: unknown): Row[] {
  if (Array.isArray(json)) return json.filter((r): r is Row => typeof r === "object" && r !== null);
  if (typeof json !== "object" || json === null) return [];
  for (const v of Object.values(json)) {
    if (Array.isArray(v) && typeof v[0] === "object" && v[0] !== null) return v as Row[];
  }
  return [];
}

export default function SampleBars({ logId, config }: WidgetProps) {
  const { data, error, isLoading } = useQuery({
    queryKey: ["e2e_viz", "sample-bars", logId, config],
    queryFn: () =>
      api<unknown>(`/api/v1/modules/e2e_viz${ROUTE}?log_id=${encodeURIComponent(logId ?? "")}`),
    enabled: !!logId,
  });

  if (isLoading) return <Skeleton className="h-full min-h-24 w-full" />;
  if (error) return <p className="p-3 text-sm text-destructive">{String(error)}</p>;

  // A bar per row: the first text column on x, the first number column as the bar.
  const rows = rowsOf(data);
  const first = rows[0] ?? {};
  const x = Object.keys(first).find((k) => typeof first[k] === "string");
  const y = Object.keys(first).find((k) => typeof first[k] === "number");
  if (!x || !y) {
    return <CardEmpty>No rows with a text and a number column at {ROUTE} yet.</CardEmpty>;
  }

  return (
    <div className="h-full min-h-40 w-full p-2">
      <ResponsiveContainer width="100%" height="100%">
        <BarChart data={rows.slice(0, 30)}>
          <CartesianGrid strokeDasharray="3 3" vertical={false} />
          <XAxis dataKey={x} tick={{ fontSize: 11 }} interval="preserveStartEnd" />
          <YAxis tick={{ fontSize: 11 }} width={40} />
          <Tooltip />
          <Bar dataKey={y} fill={seriesColor(0)} radius={[3, 3, 0, 0]} />
        </BarChart>
      </ResponsiveContainer>
    </div>
  );
}
