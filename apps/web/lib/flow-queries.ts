"use client";

import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";

import { api, ApiError } from "@/lib/api";
import type {
  FlowDetail,
  FlowEdge as FlowGraphEdge,
  FlowGraph,
  FlowLogModel,
  FlowModuleData,
  FlowNode as FlowGraphNode,
  FlowOutputData,
  FlowPalette,
  FlowProblem as FlowIssue,
  FlowRun,
  FlowSourceData,
  FlowSummary,
  FlowUpdate,
  FlowValidation,
} from "@/lib/api-types";

/**
 * Flows: saved module pipelines (source → modules → outputs), run against a log.
 * Mirrors `apps/api/.../routes/flows.py`; the types live in `api-types.ts`.
 */

export type {
  FlowConverter,
  FlowNodeRunState,
  FlowPaletteModule,
  FlowPalettePort,
} from "@/lib/api-types";
export type {
  FlowDetail,
  FlowGraph,
  FlowGraphEdge,
  FlowGraphNode,
  FlowIssue,
  FlowModuleData,
  FlowOutputData,
  FlowPalette,
  FlowRun,
  FlowSourceData,
  FlowSummary,
};

// ── Hooks ───────────────────────────────────────────────────────────────────

export const flowKeys = {
  all: ["flows"] as const,
  list: () => ["flows", "list"] as const,
  detail: (id: string) => ["flows", "detail", id] as const,
  palette: (logModel: FlowLogModel) => ["flows", "palette", logModel] as const,
  /** Prefix of a flow's run list and single runs (`flow_run.updated` invalidates it). */
  runs: (flowId: string) => ["flows", "runs", flowId] as const,
  run: (flowId: string, runId: string) => ["flows", "runs", flowId, runId] as const,
};

const IN_FLIGHT = new Set<string>(["queued", "running"]);
export const isRunInFlight = (status: string | undefined) => !!status && IN_FLIGHT.has(status);

export function useFlows(enabled = true) {
  return useQuery({
    queryKey: flowKeys.list(),
    queryFn: () => api<FlowSummary[]>("/api/v1/flows"),
    enabled,
  });
}

export function useFlow(id: string) {
  return useQuery({
    queryKey: flowKeys.detail(id),
    queryFn: () => api<FlowDetail>(`/api/v1/flows/${id}`),
  });
}

export function useFlowPalette(logModel: FlowLogModel | undefined) {
  return useQuery({
    queryKey: flowKeys.palette(logModel ?? "case_centric"),
    queryFn: () => api<FlowPalette>(`/api/v1/flows/palette?log_model=${logModel}`),
    enabled: !!logModel,
    staleTime: 60_000,
  });
}

export function useCreateFlow() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: (input: { name: string; log_model: FlowLogModel; graph: FlowGraph }) =>
      api<FlowDetail>("/api/v1/flows", { method: "POST", json: input }),
    onSuccess: () => qc.invalidateQueries({ queryKey: flowKeys.list() }),
  });
}

/** PUT replaces name + description + graph. Optimistic over the list and the detail. */
export function useUpdateFlow() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: ({ id, ...body }: FlowUpdate & { id: string }) =>
      api<FlowDetail>(`/api/v1/flows/${id}`, { method: "PUT", json: body }),
    onMutate: async ({ id, ...body }) => {
      await qc.cancelQueries({ queryKey: flowKeys.list() });
      await qc.cancelQueries({ queryKey: flowKeys.detail(id) });
      const prevList = qc.getQueryData<FlowSummary[]>(flowKeys.list());
      const prevDetail = qc.getQueryData<FlowDetail>(flowKeys.detail(id));
      qc.setQueryData<FlowSummary[]>(flowKeys.list(), (old) =>
        old?.map((f) => (f.id === id ? { ...f, name: body.name, description: body.description ?? null } : f)),
      );
      if (prevDetail) qc.setQueryData(flowKeys.detail(id), { ...prevDetail, ...body });
      return { prevList, prevDetail, id };
    },
    onError: (_e, _vars, ctx) => {
      if (!ctx) return;
      qc.setQueryData(flowKeys.list(), ctx.prevList);
      qc.setQueryData(flowKeys.detail(ctx.id), ctx.prevDetail);
    },
    // The detail is not refetched: the editor's local graph is the source of truth.
    onSettled: () => qc.invalidateQueries({ queryKey: flowKeys.list() }),
  });
}

export function useDeleteFlow() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: (id: string) => api<void>(`/api/v1/flows/${id}`, { method: "DELETE" }),
    onMutate: async (id) => {
      await qc.cancelQueries({ queryKey: flowKeys.list() });
      const prevList = qc.getQueryData<FlowSummary[]>(flowKeys.list());
      qc.setQueryData<FlowSummary[]>(flowKeys.list(), (old) => old?.filter((f) => f.id !== id));
      return { prevList };
    },
    onError: (_e, _id, ctx) => qc.setQueryData(flowKeys.list(), ctx?.prevList),
    onSettled: () => qc.invalidateQueries({ queryKey: flowKeys.list() }),
  });
}

export function useValidateFlow(flowId: string) {
  return useMutation({
    mutationFn: async (logId: string | null) =>
      (
        await api<FlowValidation>(`/api/v1/flows/${flowId}/validate`, {
          method: "POST",
          json: logId ? { log_id: logId } : {},
        })
      ).errors,
  });
}

export function useRunFlow(flowId: string) {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: (logId: string) =>
      api<FlowRun>(`/api/v1/flows/${flowId}/runs`, { method: "POST", json: { log_id: logId } }),
    onSuccess: (run) => {
      qc.setQueryData(flowKeys.run(flowId, run.id), run);
      void qc.invalidateQueries({ queryKey: flowKeys.runs(flowId) });
      void qc.invalidateQueries({ queryKey: flowKeys.list() });
    },
  });
}

/** The flow's runs, latest first. */
export function useFlowRuns(flowId: string) {
  return useQuery({
    queryKey: flowKeys.runs(flowId),
    queryFn: () => api<FlowRun[]>(`/api/v1/flows/${flowId}/runs`),
  });
}

/** One run, polled while in flight (`flow_run.updated` also invalidates it). */
export function useFlowRun(flowId: string, runId: string | null) {
  return useQuery({
    queryKey: flowKeys.run(flowId, runId ?? ""),
    queryFn: () => api<FlowRun>(`/api/v1/flows/${flowId}/runs/${runId}`),
    enabled: !!runId,
    refetchInterval: (q) => (isRunInFlight(q.state.data?.status) ? 2000 : false),
  });
}

export function useCancelFlowRun(flowId: string) {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: (runId: string) =>
      api<FlowRun>(`/api/v1/flows/${flowId}/runs/${runId}/cancel`, { method: "POST" }),
    onSettled: () => qc.invalidateQueries({ queryKey: flowKeys.runs(flowId) }),
  });
}

/** The node/edge problems of a 422, or null for any other error. Accepts the flow
 *  routes' `{detail: FlowInvalidDetail}` and FastAPI's own `{detail: [{loc, msg}]}`. */
export function flowIssues(e: unknown): FlowIssue[] | null {
  if (!(e instanceof ApiError) || e.status !== 422) return null;
  const body = e.detail as { detail?: unknown } | null;
  const raw = body?.detail;
  const list = Array.isArray(raw)
    ? raw
    : Array.isArray((raw as { errors?: unknown })?.errors)
      ? (raw as { errors: unknown[] }).errors
      : [{ message: typeof raw === "string" ? raw : "The flow is invalid." }];
  return list.map((it) => {
    const r = (it ?? {}) as Record<string, unknown>;
    return {
      node_id: typeof r.node_id === "string" ? r.node_id : null,
      edge_id: typeof r.edge_id === "string" ? r.edge_id : null,
      message: String(r.message ?? r.msg ?? "Invalid"),
    };
  });
}
