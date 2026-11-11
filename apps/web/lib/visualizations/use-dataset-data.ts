"use client";

import { useMemo } from "react";
import { useQuery } from "@tanstack/react-query";

import { api } from "@/lib/api";
import { useDashboardFilterOptional } from "@/components/dashboards/dashboard-filter";
import {
  EVENT_FILTER_HEADER,
  effectiveWidgetFilterHeader,
  encodeFilterHeader,
  hasWidgetFilter,
  readWidgetFilter,
} from "@/components/dashboards/widget-filter";
import { useDatasetCatalog, type DashboardItem } from "@/lib/dashboard-queries";
import { usePageParamsOptional, withQuery } from "@/lib/module-layout/page-params";
import { normalize } from "@/lib/visualizations/adapters";
import type { DatasetEnvelope, DatasetShape } from "@/lib/visualizations/types";

export interface DatasetDataResult {
  envelope?: DatasetEnvelope;
  shape?: DatasetShape;
  isLoading: boolean;
  isError: boolean;
  /** The item's dataset_ref points at a module/dataset not in the catalog
   * (module uninstalled or dataset removed). */
  missing: boolean;
}

/**
 * Fetch + normalize a viz card's dataset. The catalog supplies the module
 * `route` + `shape` (so the placement only stores the `dataset_ref`); the data
 * is fetched straight from the module route via `api()`. Runs inside the
 * dashboard's widget QueryClient (the card mounts within `DashboardWidgetScope`),
 * so a filter commit resets + refetches it exactly like a module widget.
 * React-query dedups the fetch so the card body and its settings form share one
 * request.
 *
 * Filtering follows the single precedence rule in `effectiveWidgetFilterHeader`:
 * when a board-global filter is active it already rides the ambient
 * `X-FF-Event-Filter` header (which `api()` auto-attaches) and GLOBAL WINS — we
 * attach nothing here and let it apply. Only when no global filter is active is
 * the card's own per-widget filter (stored in `item.config`) emitted as a
 * per-request header. The widget filter's identity is in the query key so
 * editing it refetches; global toggles refetch via the provider's resetQueries.
 *
 * On a module layout page, the page params (`controls` cards) that the
 * dataset's `params_schema.properties` declares are sent as query params and
 * keyed on, so a control change refetches while the previous data stays shown.
 */
export function useDatasetData(item: DashboardItem, logId: string | null): DatasetDataResult {
  const ref = item.dataset_ref ?? null;
  const { data: catalog } = useDatasetCatalog();
  const entry = catalog?.find(
    (e) => e.module_id === ref?.module_id && e.dataset_id === ref?.dataset_id,
  );
  const route = entry?.route;
  const shape = entry?.shape as DatasetShape | undefined;
  const enabled = Boolean(ref && route && logId);

  // Precedence (global wins) is computed centrally in `widget-filter.ts`.
  const board = useDashboardFilterOptional();
  const globalActive =
    !!board && (board.columnFilters.length > 0 || board.timeFilters.length > 0);
  const widgetFilter = readWidgetFilter(item.config);
  const filterHeader = effectiveWidgetFilterHeader(globalActive, widgetFilter);
  // Key on the widget filter's identity only (NOT the effective header): a
  // global on/off toggle must not thrash this key — the provider's resetQueries
  // already refetches with the latest `filterHeader` closure on a global commit.
  const widgetKey = hasWidgetFilter(widgetFilter)
    ? encodeFilterHeader([...widgetFilter.timeFilters, ...widgetFilter.columnFilters])
    : null;

  const pageValues = usePageParamsOptional()?.values;
  const declared = entry?.params_schema?.properties;
  const params = useMemo(() => {
    const out: Record<string, unknown> = {};
    for (const k of Object.keys(declared ?? {})) {
      if (pageValues?.[k] !== undefined) out[k] = pageValues[k];
    }
    return out;
  }, [declared, pageValues]);

  const q = useQuery({
    queryKey: ["dataset-data", ref?.module_id, ref?.dataset_id, logId, widgetKey, params],
    queryFn: () =>
      api<unknown>(
        withQuery(`/api/v1/modules/${ref!.module_id}${route}`, { log_id: logId, ...params }),
        filterHeader ? { headers: { [EVENT_FILTER_HEADER]: filterHeader } } : undefined,
      ),
    enabled,
    staleTime: 30_000,
    // Keep showing the old rows only while the same dataset+log refetches (a
    // param or filter change), never data of another dataset or log.
    placeholderData: (prev, prevQuery) =>
      prevQuery?.queryKey.slice(1, 4).join("|") === [ref?.module_id, ref?.dataset_id, logId].join("|")
        ? prev
        : undefined,
  });

  const envelope = useMemo(
    () => (q.data !== undefined && shape ? normalize(shape, q.data) : undefined),
    [q.data, shape],
  );

  return {
    envelope,
    shape,
    isLoading: enabled && q.isLoading,
    isError: q.isError,
    missing: Boolean(catalog && ref && !entry),
  };
}
