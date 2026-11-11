"use client";

import { useState } from "react";
import { Check, Copy } from "lucide-react";

import { Badge } from "@/components/ui/badge";
import { Skeleton } from "@/components/ui/skeleton";
import { formatNumber } from "@/lib/format";
import { VizEmpty } from "@/components/visualizations/viz-shell";
import { FieldMappingForm, type VizPatch } from "@/components/visualizations/field-mapping-form";
import { configWithoutWidgetFilter } from "@/components/dashboards/widget-filter";
import { useIsDev } from "@/lib/dev-queries";
import { needsDataSnippet } from "@/lib/visualizations/catalog";
import { vizAccepts, vizRegistry } from "@/lib/visualizations/registry";
import { useDatasetData } from "@/lib/visualizations/use-dataset-data";
import type { DashboardItem } from "@/lib/dashboard-queries";
import type { FieldMapping, VizSpec } from "@/lib/visualizations/types";

type VizState = "ready" | "needs-data" | "empty" | "error" | "loading";

/**
 * Body of a `kind:"viz"` dashboard card. Fetches + normalizes the module
 * dataset (`useDatasetData`) and renders the chosen generic visualization
 * against it, honoring the board's global filters automatically. A card with
 * no `dataset_ref` (a Studio placeholder) shows what data it needs.
 *
 * Every state is wrapped in `data-viz-id` / `data-viz-state` (e2e hooks).
 */
export function GenericVizBody({ item, logId }: { item: DashboardItem; logId: string | null }) {
  const { envelope, isLoading, isError, missing, unfiltered, truncated } = useDatasetData(item, logId);
  const spec = item.viz_id ? vizRegistry[item.viz_id] : undefined;
  const wrap = (state: VizState, node: React.ReactNode) => (
    <div data-viz-id={item.viz_id ?? ""} data-viz-state={state} className="h-full w-full">
      {node}
    </div>
  );

  if (!item.dataset_ref && spec) return wrap("needs-data", <NeedsData spec={spec} />);
  if (!logId) return wrap("empty", <VizEmpty message="Select an event log to populate this card." />);
  if (missing) return wrap("error", <VizEmpty message="This dataset's module isn't installed." />);
  // A saved card can name a viz id this web build doesn't ship.
  if (item.viz_id && !spec) {
    return wrap(
      "error",
      <VizEmpty message={`Unknown visualization "${item.viz_id}". Open settings to pick one.`} />,
    );
  }
  if (!item.viz_id || !spec) {
    return wrap("empty", <VizEmpty message="Open settings to choose a visualization." />);
  }
  if (isLoading) return wrap("loading", <Skeleton className="h-full w-full" />);
  if (isError || !envelope) return wrap("error", <VizEmpty message="Couldn't load this dataset." />);
  if (!vizAccepts(spec, envelope.shape, envelope.artifact?.type)) {
    return wrap("error", <VizEmpty message="This visualization can't render this dataset." />);
  }

  const Viz = spec.Component;
  const viz = (
    <Viz
      dataset={envelope}
      mapping={(item.mapping ?? {}) as FieldMapping}
      // Strip the reserved per-widget-filter key so the viz never sees it.
      options={configWithoutWidgetFilter(item.config) as Record<string, unknown>}
    />
  );
  if (!unfiltered && !truncated) return wrap("ready", viz);
  return wrap(
    "ready",
    <div className="flex h-full flex-col gap-1.5">
      <div className="flex shrink-0 flex-wrap items-center gap-1.5 text-[11px] text-muted-foreground">
        {unfiltered && (
          <Badge
            variant="outline"
            className="h-4 px-1 text-[10px] font-normal"
            title="The module precomputed this result for the whole log - the board's filter doesn't apply to it."
          >
            Unfiltered (precomputed)
          </Badge>
        )}
        {truncated && (
          <span className="tabular-nums">
            First {formatNumber(truncated.shown)} of {formatNumber(truncated.total)} rows
          </span>
        )}
      </div>
      <div className="min-h-0 flex-1">{viz}</div>
    </div>,
  );
}

/** An unbound viz card. Module authors (dev) get the two snippets that give it
 * data; everyone else sees a plain empty state. */
function NeedsData({ spec }: { spec: VizSpec }) {
  const isDev = useIsDev();
  if (!isDev) return <VizEmpty message="No data yet." />;
  const snippet = needsDataSnippet(spec);
  return (
    <div className="h-full w-full space-y-2 overflow-auto text-[11px] leading-snug">
      <p className="text-xs font-semibold">Needs data</p>
      <p className="text-muted-foreground">
        {spec.needs ?? `A dataset of shape ${spec.accepts.join(" or ")}.`}
      </p>
      <CopyBlock label="manifest.yaml, under datasets:" text={snippet.manifest} />
      <CopyBlock label="module.py, in the module class:" text={snippet.route} />
      <p className="text-muted-foreground">
        Save both files; the module reloads. Then pick the dataset under Data in the inspector.
      </p>
    </div>
  );
}

/** A labelled monospace block with a copy button. */
export function CopyBlock({ label, text }: { label?: string; text: string }) {
  const [copied, setCopied] = useState(false);
  return (
    <div className="space-y-1">
      {label && <p className="text-muted-foreground">{label}</p>}
      <div className="relative">
        <pre className="whitespace-pre-wrap rounded-md [overflow-wrap:anywhere] border border-border bg-muted/40 p-2 pr-8 font-mono text-[10.5px] leading-snug">
          {text}
        </pre>
        <button
          type="button"
          aria-label={copied ? "Copied" : "Copy"}
          className="absolute right-1 top-1 rounded p-1 text-muted-foreground hover:bg-muted hover:text-foreground"
          // The card body is a drag surface in edit mode.
          onPointerDown={(e) => e.stopPropagation()}
          onMouseDown={(e) => e.stopPropagation()}
          onClick={(e) => {
            e.stopPropagation();
            navigator.clipboard
              .writeText(text)
              .then(() => {
                setCopied(true);
                setTimeout(() => setCopied(false), 1500);
              })
              .catch(() => null);
          }}
        >
          {copied ? <Check className="h-3 w-3" /> : <Copy className="h-3 w-3" />}
        </button>
      </div>
    </div>
  );
}

/** Settings for a viz card - wires the field-mapping form to the (cached)
 * dataset so it can offer column bindings. `studioModuleId` (Studio only) adds
 * the Data select and lists every viz while the card is unbound. */
export function VizSettings({
  item,
  logId,
  onChange,
  studioModuleId,
}: {
  item: DashboardItem;
  logId: string | null;
  onChange: (patch: VizPatch) => void;
  studioModuleId?: string;
}) {
  const { envelope, shape, artifactType } = useDatasetData(item, logId);
  // The per-card filter editor used to sit below the mapping form. It's gone:
  // one filter row above the board scopes everything, and a second filter
  // hidden inside a single card contradicts it. A filter already stored on a
  // card is still applied (`readWidgetFilter` in `use-dataset-data`) so no
  // existing board silently changes what it shows — there is just no longer a
  // way to author a new one.
  return (
    <FieldMappingForm
      item={item}
      dataset={envelope}
      shape={shape}
      artifactType={artifactType}
      onChange={onChange}
      studioModuleId={studioModuleId}
    />
  );
}
