"use client";

import { useMutation, useQuery, useQueryClient, type QueryKey } from "@tanstack/react-query";

import { api } from "@/lib/api";
import type {
  Artifact,
  ArtifactDetail,
  ArtifactPatch,
  ArtifactUpload,
  ModuleVariantState,
} from "@/lib/api-types";

/**
 * The artifact workspace - an index over the module result caches plus the user's
 * uploaded artifacts. Mirrors `apps/api/.../routes/artifacts.py`.
 */

export type { ArtifactConsumer, ArtifactDetail } from "@/lib/api-types";
export type ArtifactRow = Artifact;

export interface ArtifactListParams {
  log_id?: string;
  artifact_type?: string;
  pinned?: boolean;
}

export const artifactKeys = {
  all: ["artifacts"] as const,
  list: (params: ArtifactListParams) => ["artifacts", "list", params] as const,
  detail: (id: string) => ["artifacts", "detail", id] as const,
  content: (id: string) => ["artifacts", "content", id] as const,
};

const isListKey = (key: QueryKey) => key[0] === "artifacts" && key[1] === "list";

export function useArtifacts(params: ArtifactListParams = {}) {
  const qs = new URLSearchParams();
  if (params.log_id) qs.set("log_id", params.log_id);
  if (params.artifact_type) qs.set("artifact_type", params.artifact_type);
  if (params.pinned !== undefined) qs.set("pinned", String(params.pinned));
  return useQuery({
    queryKey: artifactKeys.list(params),
    queryFn: () => api<ArtifactRow[]>(`/api/v1/artifacts${qs.toString() ? `?${qs}` : ""}`),
    placeholderData: (prev) => prev,
  });
}

export function useArtifact(id: string | null) {
  return useQuery({
    queryKey: artifactKeys.detail(id ?? ""),
    queryFn: () => api<ArtifactDetail>(`/api/v1/artifacts/${id}`),
    enabled: !!id,
  });
}

/** The stored payload: json as-is, parquet as a row preview. */
export function useArtifactContent(id: string | null) {
  return useQuery({
    queryKey: artifactKeys.content(id ?? ""),
    queryFn: () => api<unknown>(`/api/v1/artifacts/${id}/content`),
    enabled: !!id,
    staleTime: 60_000,
  });
}

/** Rename / pin. Optimistic over every list and the detail. */
export function useUpdateArtifact() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: (input: ArtifactPatch & { id: string }) => {
      const { id, ...patch } = input;
      return api<ArtifactRow>(`/api/v1/artifacts/${id}`, { method: "PATCH", json: patch });
    },
    onMutate: async ({ id, ...patch }) => {
      await qc.cancelQueries({ predicate: (q) => isListKey(q.queryKey) });
      await qc.cancelQueries({ queryKey: artifactKeys.detail(id) });
      const prevLists = qc.getQueriesData<ArtifactRow[]>({ predicate: (q) => isListKey(q.queryKey) });
      const prevDetail = qc.getQueryData<ArtifactDetail>(artifactKeys.detail(id));
      qc.setQueriesData<ArtifactRow[]>({ predicate: (q) => isListKey(q.queryKey) }, (old) =>
        old?.map((a) => (a.id === id ? { ...a, ...patch } : a)),
      );
      if (prevDetail) qc.setQueryData(artifactKeys.detail(id), { ...prevDetail, ...patch });
      return { prevLists, prevDetail, id };
    },
    onError: (_e, _vars, ctx) => {
      ctx?.prevLists.forEach(([key, data]) => qc.setQueryData(key, data));
      if (ctx) qc.setQueryData(artifactKeys.detail(ctx.id), ctx.prevDetail);
    },
    onSettled: () => qc.invalidateQueries({ queryKey: artifactKeys.all }),
  });
}

/** Upload: delete file + row; variant artifact: delete the variant. Optimistic. */
export function useDeleteArtifact() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: (id: string) => api<void>(`/api/v1/artifacts/${id}`, { method: "DELETE" }),
    onMutate: async (id) => {
      await qc.cancelQueries({ predicate: (q) => isListKey(q.queryKey) });
      const prevLists = qc.getQueriesData<ArtifactRow[]>({ predicate: (q) => isListKey(q.queryKey) });
      qc.setQueriesData<ArtifactRow[]>({ predicate: (q) => isListKey(q.queryKey) }, (old) =>
        old?.filter((a) => a.id !== id),
      );
      return { prevLists };
    },
    onError: (_e, _id, ctx) => ctx?.prevLists.forEach(([key, data]) => qc.setQueryData(key, data)),
    onSettled: () => qc.invalidateQueries({ queryKey: artifactKeys.all }),
  });
}

/** Import a json artifact into the workspace (validated server-side, 422 on problems). */
export function useImportArtifact() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: (input: ArtifactUpload) =>
      api<ArtifactRow>("/api/v1/artifacts", { method: "POST", json: input }),
    onSuccess: () => qc.invalidateQueries({ queryKey: artifactKeys.all }),
  });
}

const variantPath = (logId: string, moduleId: string) =>
  `/api/v1/event-logs/${logId}/module-runs/${encodeURIComponent(moduleId)}/variants`;

const variantKey = (logId: string, moduleId: string, variant: string | null) =>
  ["module-variant", logId, moduleId, variant] as const;

/** "Use in…": run `moduleId` as a variant on `logId` with `{port: ref}` bound;
 *  `force` retries a failed or cancelled one. */
export function useRunWithArtifact() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: (input: { logId: string; moduleId: string; port: string; ref: string; force?: boolean }) =>
      api<ModuleVariantState>(variantPath(input.logId, input.moduleId), {
        method: "POST",
        json: { inputs: { [input.port]: input.ref }, force: input.force ?? false },
      }),
    // Seed the variant's state so a retry polls again instead of keeping the settled one.
    onSuccess: (state, input) =>
      qc.setQueryData(variantKey(input.logId, input.moduleId, state.variant), state),
  });
}

/** "Re-run" a variant artifact whose run failed: its spec again, with `force`. */
export function useRerunArtifact() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: (a: ArtifactRow) =>
      api<ModuleVariantState>(variantPath(a.log_id ?? "", a.module_id ?? ""), {
        method: "POST",
        json: { ...a.spec, force: true },
      }),
    onSuccess: (state, a) => {
      qc.setQueryData(variantKey(a.log_id ?? "", a.module_id ?? "", state.variant), state);
      void qc.invalidateQueries({ queryKey: artifactKeys.all });
    },
  });
}

const IN_FLIGHT = new Set(["queued", "running"]);

/** One variant's state, polled while its job is in flight. */
export function useModuleVariant(logId: string, moduleId: string, variant: string | null) {
  const qc = useQueryClient();
  return useQuery({
    queryKey: variantKey(logId, moduleId, variant),
    queryFn: async () => {
      const state = await api<ModuleVariantState>(`${variantPath(logId, moduleId)}/${variant}`);
      // A settled variant registers its outputs in the workspace.
      if (!IN_FLIGHT.has(state.status)) void qc.invalidateQueries({ queryKey: artifactKeys.all });
      return state;
    },
    enabled: !!variant,
    refetchInterval: (q) => (q.state.data && IN_FLIGHT.has(q.state.data.status) ? 2500 : false),
  });
}
