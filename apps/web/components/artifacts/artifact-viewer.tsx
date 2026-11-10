"use client";

import { useMemo } from "react";

import { ARTIFACT_TYPES } from "../../../../packages/module-sdk-ts/src/artifacts.generated";
import { Skeleton } from "@/components/ui/skeleton";
import { TableViz } from "@/components/visualizations/tables/table-viz";
import { useArtifactContent, type ArtifactRow } from "@/lib/artifact-queries";
import { formatNumber } from "@/lib/format";
import { artifactTypeId, normalize } from "@/lib/visualizations/adapters";
import { vizRegistry } from "@/lib/visualizations/registry";
import type { DatasetShape } from "@/lib/visualizations/types";

const FRAME = "h-[520px] w-full overflow-hidden rounded-lg border bg-card";
const MAX_ENTRIES = 200;

/** The platform viewer for one artifact: its registered artifact viz, a table for
 *  tabular artifacts, else a collapsible JSON tree. */
export function ArtifactViewer({ artifact }: { artifact: ArtifactRow }) {
  const { data, isLoading, isError, error } = useArtifactContent(artifact.id);
  const type = artifactTypeId(artifact.artifact_type) ?? "";
  const viewer = Object.values(vizRegistry).find((v) => v.acceptsArtifactTypes?.includes(type));
  const storage = (ARTIFACT_TYPES as Record<string, { storage: string }>)[type]?.storage;
  const tabular = type === "table" || storage === "parquet";

  const dataset = useMemo(() => {
    if (data === undefined) return undefined;
    if (viewer) return normalize(viewer.accepts[0] as DatasetShape, data, type);
    return tabular ? normalize("table", data) : undefined;
  }, [data, viewer, tabular, type]);

  if (isLoading) return <Skeleton className="h-[520px] w-full rounded-lg" />;
  if (isError) {
    return (
      <div className={`${FRAME} flex items-center justify-center p-4 text-sm text-muted-foreground`}>
        Couldn&apos;t load this artifact: {(error as Error).message}
      </div>
    );
  }
  if (dataset) {
    const Viz = viewer?.Component ?? TableViz;
    // A parquet artifact arrives as a `{columns, rows, total_rows}` preview.
    const total = (data as { total_rows?: number } | null)?.total_rows;
    const shown = dataset.meta?.rowCount ?? 0;
    return (
      <div className="space-y-1">
        <div className={FRAME}>
          <Viz dataset={dataset} mapping={{}} options={{}} />
        </div>
        {total != null && total > shown && (
          <p className="text-xs text-muted-foreground">
            Preview: the first {formatNumber(shown)} of {formatNumber(total)} rows.
          </p>
        )}
      </div>
    );
  }
  return (
    <div className="max-h-[520px] overflow-auto rounded-lg border bg-card p-3 font-mono text-xs">
      <JsonNode name={artifact.name} value={data} depth={0} />
    </div>
  );
}

function JsonNode({ name, value, depth }: { name: string; value: unknown; depth: number }) {
  if (value === null || typeof value !== "object") {
    return (
      <div className="break-all py-px pl-4">
        <span className="text-muted-foreground">{name}: </span>
        {JSON.stringify(value)}
      </div>
    );
  }
  const entries = Array.isArray(value)
    ? value.map((v, i) => [String(i), v] as const)
    : Object.entries(value as Record<string, unknown>);
  return (
    <details open={depth < 2} className={depth ? "pl-4" : undefined}>
      <summary className="cursor-pointer py-px hover:text-primary">
        {name}{" "}
        <span className="text-muted-foreground">
          {Array.isArray(value) ? `[${entries.length}]` : `{${entries.length}}`}
        </span>
      </summary>
      {entries.slice(0, MAX_ENTRIES).map(([k, v]) => (
        <JsonNode key={k} name={k} value={v} depth={depth + 1} />
      ))}
      {entries.length > MAX_ENTRIES && (
        <div className="pl-4 text-muted-foreground">… {entries.length - MAX_ENTRIES} more</div>
      )}
    </details>
  );
}
