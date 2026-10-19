"use client";

import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";

import { api } from "@/lib/api";
import type { AdminModuleRow } from "@/lib/api-types";

/**
 * Admin → Modules data layer. Cross-user module ownership + controls, gated by
 * the `admin` role server-side. Mirrors `apps/api/.../routes/admin_modules.py`.
 */

const moduleAdminKey = ["admin", "modules"] as const;

export function useAdminModules() {
  return useQuery({
    queryKey: moduleAdminKey,
    queryFn: () => api<AdminModuleRow[]>("/api/v1/admin/modules"),
    staleTime: 15_000,
  });
}

/** Reconcile a single updated row into the cached list. */
function useRowUpdater() {
  const qc = useQueryClient();
  return (updated: AdminModuleRow) =>
    qc.setQueryData<AdminModuleRow[]>(moduleAdminKey, (old) =>
      old ? old.map((m) => (m.id === updated.id ? updated : m)) : old,
    );
}

export function useSetModuleDefault() {
  const patch = useRowUpdater();
  return useMutation({
    mutationFn: ({ moduleId, isDefault }: { moduleId: string; isDefault: boolean }) =>
      api<AdminModuleRow>(`/api/v1/admin/modules/${encodeURIComponent(moduleId)}/default`, {
        method: "PUT",
        json: { is_default: isDefault },
      }),
    onSuccess: patch,
  });
}

export function useSetModuleWithheld() {
  const patch = useRowUpdater();
  return useMutation({
    mutationFn: ({ moduleId, withheld }: { moduleId: string; withheld: boolean }) =>
      api<AdminModuleRow>(`/api/v1/admin/modules/${encodeURIComponent(moduleId)}/withhold`, {
        method: "PUT",
        json: { withheld },
      }),
    onSuccess: patch,
  });
}

/**
 * Override whether a module also computes in the background after every import.
 * Optimistic (an edit of an existing row): flip the switch at once, roll back from
 * the snapshot on error, reconcile with the server when settled.
 */
export function useSetModulePrecompute() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: ({ moduleId, mode }: { moduleId: string; mode: "eager" | "lazy" }) =>
      api<AdminModuleRow>(`/api/v1/admin/modules/${encodeURIComponent(moduleId)}/precompute`, {
        method: "PUT",
        json: { mode },
      }),
    onMutate: async ({ moduleId, mode }) => {
      await qc.cancelQueries({ queryKey: moduleAdminKey });
      const previous = qc.getQueryData<AdminModuleRow[]>(moduleAdminKey);
      qc.setQueryData<AdminModuleRow[]>(moduleAdminKey, (old) =>
        old?.map((m) =>
          m.id === moduleId && m.precompute
            ? {
                ...m,
                precompute: {
                  ...m.precompute,
                  effective: mode,
                  overridden: mode !== m.precompute.manifest,
                },
              }
            : m,
        ),
      );
      return { previous };
    },
    onError: (_err, _vars, ctx) => {
      if (ctx?.previous) qc.setQueryData(moduleAdminKey, ctx.previous);
    },
    onSettled: () => {
      void qc.invalidateQueries({ queryKey: moduleAdminKey });
    },
  });
}

export function useForceInstallModule() {
  const patch = useRowUpdater();
  return useMutation({
    mutationFn: ({ moduleId, userId }: { moduleId: string; userId: string }) =>
      api<AdminModuleRow>(`/api/v1/admin/modules/${encodeURIComponent(moduleId)}/installs`, {
        method: "POST",
        json: { user_id: userId },
      }),
    onSuccess: patch,
  });
}

export function useForceUninstallModule() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: ({ moduleId, userId }: { moduleId: string; userId: string }) =>
      api<void>(
        `/api/v1/admin/modules/${encodeURIComponent(moduleId)}/installs/${encodeURIComponent(userId)}`,
        { method: "DELETE" },
      ),
    // A force-uninstall can tear down the shared artifact (last owner), so the
    // whole list can shift – refetch rather than surgically patch.
    onSuccess: () => {
      void qc.invalidateQueries({ queryKey: moduleAdminKey });
    },
  });
}
