"use client";

import {
  keepPreviousData,
  useInfiniteQuery,
  useMutation,
  useQuery,
  useQueryClient,
  type QueryClient,
} from "@tanstack/react-query";

import { api } from "@/lib/api";
import type {
  DatasetCreate,
  DatasetDetail,
  DatasetListParams,
  DatasetPage,
  DatasetUpdate,
  DatasetUseResponse,
  HubStatus,
  LicenseOut,
  PublishCandidate,
} from "@/lib/api-types";
import { datasetPath, datasetsListPath, queryKeys } from "@/lib/query-keys";
import { useProgressRouter } from "@/lib/use-progress-router";

/**
 * Dataset hub data layer – mirrors `apps/api/.../routes/hub_datasets.py`.
 *
 * Creates (publish, use) invalidate on success; edits of an existing dataset
 * (card PATCH, disable/enable, purge) are optimistic per AGENT.md: snapshot the
 * detail cache, write the expected state, roll back on error, reconcile on settle.
 */

export const DATASET_PAGE_SIZE = 24;

export function useHubStatus() {
  return useQuery({
    queryKey: queryKeys.hubStatus(),
    queryFn: () => api<HubStatus>("/api/v1/hub/status"),
    staleTime: 5 * 60_000,
  });
}

export function useLicenses(enabled = true) {
  return useQuery({
    queryKey: queryKeys.hubLicenses(),
    queryFn: () => api<LicenseOut[]>("/api/v1/hub/licenses"),
    staleTime: Infinity,
    enabled,
  });
}

/** The catalog, paged by offset ("Load more"). `params` excludes offset/limit. */
export function useDatasets(params: Omit<DatasetListParams, "offset" | "limit">, enabled = true) {
  return useInfiniteQuery({
    queryKey: queryKeys.datasets(params),
    queryFn: ({ pageParam }) =>
      api<DatasetPage>(
        datasetsListPath({ ...params, limit: DATASET_PAGE_SIZE, offset: pageParam }),
      ),
    initialPageParam: 0,
    getNextPageParam: (last) => {
      const next = last.offset + last.items.length;
      return last.items.length > 0 && next < last.total ? next : undefined;
    },
    placeholderData: keepPreviousData,
    enabled,
  });
}

export function useDataset(ns: string, name: string) {
  return useQuery({
    queryKey: queryKeys.dataset(ns, name),
    queryFn: () => api<DatasetDetail>(datasetPath(ns, name)),
    // A 404 (unreadable / unknown slug) is final – don't retry it three times.
    retry: (count, err) => (err as { status?: number })?.status !== 404 && count < 2,
  });
}

/** Curators: the module results a publish of `logId` ships by default – the
 *  same server-side rule the publish applies (fresh precompute results only). */
export function usePublishCandidates(logId: string) {
  return useQuery({
    queryKey: queryKeys.publishCandidates(logId),
    queryFn: () =>
      api<PublishCandidate[]>(`/api/v1/hub/publish-candidates/${encodeURIComponent(logId)}`),
    staleTime: 0,
  });
}

export function usePublishDataset() {
  const qc = useQueryClient();
  const router = useProgressRouter();
  return useMutation({
    mutationFn: (body: DatasetCreate) =>
      api<DatasetDetail>("/api/v1/hub/datasets", { method: "POST", json: body }),
    onSuccess: (ds, body) => {
      qc.setQueryData(queryKeys.dataset(ds.namespace, ds.name), ds);
      qc.invalidateQueries({ queryKey: queryKeys.datasetsAll() });
      qc.invalidateQueries({ queryKey: queryKeys.eventLog(body.source_log_id) });
      router.push(`/datasets/${ds.namespace}/${ds.name}`);
    },
  });
}

/** "Use this dataset": copies the current revision into the caller's processes.
 *  Idempotent unless `new_copy` – the caller navigates to the returned log. */
export function useUseDataset(ns: string, name: string) {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: (newCopy: boolean) =>
      api<DatasetUseResponse>(`${datasetPath(ns, name)}/use`, {
        method: "POST",
        json: { new_copy: newCopy },
      }),
    onSuccess: (res) => {
      qc.setQueryData(queryKeys.eventLog(res.log.id), res.log);
      qc.invalidateQueries({ queryKey: queryKeys.eventLogs() });
      qc.invalidateQueries({ queryKey: queryKeys.dataset(ns, name) });
    },
  });
}

/** Shared optimistic plumbing for edits of one dataset's detail cache. */
function optimistic(qc: QueryClient, ns: string, name: string) {
  const key = queryKeys.dataset(ns, name);
  return {
    onMutate: async (patch: (prev: DatasetDetail) => DatasetDetail) => {
      await qc.cancelQueries({ queryKey: key });
      const prev = qc.getQueryData<DatasetDetail>(key);
      if (prev) qc.setQueryData<DatasetDetail>(key, patch(prev));
      return { prev };
    },
    onError: (ctx: { prev?: DatasetDetail } | undefined) => {
      if (ctx?.prev) qc.setQueryData(key, ctx.prev);
    },
    onSuccess: (ds: DatasetDetail) => {
      qc.setQueryData(key, ds);
    },
    onSettled: () => {
      qc.invalidateQueries({ queryKey: queryKeys.datasetsAll() });
    },
  };
}

export function useUpdateDataset(ns: string, name: string) {
  const qc = useQueryClient();
  const o = optimistic(qc, ns, name);
  return useMutation({
    mutationFn: (body: DatasetUpdate) =>
      api<DatasetDetail>(datasetPath(ns, name), { method: "PATCH", json: body }),
    onMutate: (body) =>
      o.onMutate((prev) => {
        // Only card fields the detail carries 1:1; `audience_team_id` maps to the
        // nested team the server resolves, so leave that to the reconcile.
        const { audience_team_id: _team, ...rest } = body;
        return { ...prev, ...(rest as Partial<DatasetDetail>) };
      }),
    onError: (_e, _b, ctx) => o.onError(ctx),
    onSuccess: o.onSuccess,
    onSettled: o.onSettled,
  });
}

export type DatasetStatusChange = { action: "disable"; reason: string } | { action: "enable" };

export function useSetDatasetStatus(ns: string, name: string) {
  const qc = useQueryClient();
  const o = optimistic(qc, ns, name);
  return useMutation({
    mutationFn: (change: DatasetStatusChange) =>
      api<DatasetDetail>(`${datasetPath(ns, name)}/${change.action}`, {
        method: "POST",
        json: change.action === "disable" ? { reason: change.reason } : undefined,
      }),
    onMutate: (change) =>
      o.onMutate((prev) =>
        change.action === "disable"
          ? { ...prev, status: "disabled", disabled_reason: change.reason }
          : { ...prev, status: "active", disabled_reason: null },
      ),
    onError: (_e, _c, ctx) => o.onError(ctx),
    onSuccess: o.onSuccess,
    onSettled: () => {
      o.onSettled();
      // Derived logs flip to / from the withdrawn state.
      qc.invalidateQueries({ queryKey: queryKeys.eventLogs() });
    },
  });
}

export function usePurgeDataset(ns: string, name: string) {
  const qc = useQueryClient();
  const o = optimistic(qc, ns, name);
  return useMutation({
    mutationFn: (confirm: string) =>
      api<DatasetDetail>(`${datasetPath(ns, name)}/purge`, {
        method: "POST",
        json: { confirm },
      }),
    onMutate: () =>
      o.onMutate((prev) => ({ ...prev, revision: { ...prev.revision, purged: true } })),
    onError: (_e, _c, ctx) => o.onError(ctx),
    onSuccess: o.onSuccess,
    onSettled: () => {
      o.onSettled();
      qc.invalidateQueries({ queryKey: queryKeys.eventLogs() });
    },
  });
}
