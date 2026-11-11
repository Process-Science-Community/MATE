"use client";

import { useMutation, useQuery } from "@tanstack/react-query";

import type { ConfigSchema } from "@/components/schema-form/types";
import { api } from "@/lib/api";
import { getAppQueryClient } from "@/lib/get-query-client";
import { queryKeys } from "@/lib/query-keys";

/**
 * A module's per-log parameters: the properties it marks `ui.scope: dataset`,
 * which a user may override for one event log. The module-wide value is the
 * default every log inherits. Mirrors `GET/PUT /event-logs/{id}/modules/{module}/config`.
 *
 * Uses the **app** QueryClient explicitly (like `module-run-queries.ts`): saving
 * has to invalidate the run gate, which lives there, not in a module page's
 * isolated panel client.
 */
export interface LogModuleConfig {
  /** Only the module's `ui.scope: dataset` properties, in the config-schema dialect. */
  schema: ConfigSchema;
  /** The values stored for this log. A missing key inherits the module value. */
  overrides: Record<string, unknown>;
  /** The module-wide value per property (its schema default when unset). */
  module_values: Record<string, unknown>;
  /** What this log computes with (admin locks applied). */
  effective: Record<string, unknown>;
  /** Keys an admin-locked card owns: read-only here; `effective` holds the value in use. */
  locked?: string[];
}

const path = (logId: string, moduleId: string) =>
  `/api/v1/event-logs/${logId}/modules/${encodeURIComponent(moduleId)}/config`;

export function useLogModuleConfig(logId: string, moduleId: string) {
  return useQuery(
    {
      queryKey: queryKeys.logModuleConfig(logId, moduleId),
      queryFn: () => api<LogModuleConfig>(path(logId, moduleId)),
    },
    getAppQueryClient(),
  );
}

/** Replace the log's whole overrides object (`{}` = inherit everything). */
export function useUpdateLogModuleConfig(logId: string, moduleId: string) {
  const qc = getAppQueryClient();
  const key = queryKeys.logModuleConfig(logId, moduleId);
  return useMutation(
    {
      mutationFn: (overrides: Record<string, unknown>) =>
        api<LogModuleConfig>(path(logId, moduleId), { method: "PUT", json: { overrides } }),
      onMutate: async (overrides) => {
        await qc.cancelQueries({ queryKey: key });
        const prev = qc.getQueryData<LogModuleConfig>(key);
        if (prev) {
          qc.setQueryData<LogModuleConfig>(key, {
            ...prev,
            overrides,
            effective: { ...prev.module_values, ...overrides },
          });
        }
        return { prev };
      },
      onError: (_e, _overrides, ctx) => {
        if (ctx?.prev) qc.setQueryData(key, ctx.prev);
      },
      // Awaited on purpose: callers remount the panel once the gate has re-asked
      // `ensure`. A precompute setting moves the run's fingerprint, so the gate
      // (a `moduleRuns` sub-key) then shows the recompute instead of stale results.
      onSettled: () =>
        Promise.all([
          qc.invalidateQueries({ queryKey: key }),
          qc.invalidateQueries({ queryKey: queryKeys.moduleRuns(logId) }),
        ]),
    },
    qc,
  );
}
