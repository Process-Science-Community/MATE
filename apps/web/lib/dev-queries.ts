"use client";

import { useEffect, useRef } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";

import { api, ApiError } from "@/lib/api";
import type { DashboardItem } from "@/lib/dashboard-queries";

/**
 * Lite dev mode data layer. The API mounts `/api/v1/dev/*` only when
 * `ENV=dev` and `DEMO_MODE` are both on, so a 404 on the status call is the
 * "not dev" signal rather than an error.
 */

export interface LayoutTab {
  id: string;
  title: string;
}

/** A module's declarative page (`modules/<id>/layout.json`). The items are the
 * same shape a dashboard stores, so the dashboard canvas renders them as-is.
 * `tabs` absent means one page; an item without `tab` sits on the first tab. */
export interface ModuleLayout {
  version: 1;
  tabs?: LayoutTab[];
  items: DashboardItem[];
}

/** A route the loaded module registered; `path` is relative to the module. */
export interface DevRoute {
  method: "GET" | "POST" | "PUT" | "DELETE" | "PATCH";
  path: string;
  job: boolean;
}

export interface DevModuleStatus {
  id: string;
  name: string;
  folder: string;
  has_panel: boolean;
  has_layout: boolean;
  datasets: string[];
  widgets: string[];
  isolation: string;
  execution: string;
  load_error: string | null;
  /** Empty for a module that failed to load. */
  routes: DevRoute[];
}

export interface DevStatus {
  dev: true;
  modules_only: string[];
  debugpy_port: number;
  dev_force_thread: boolean;
  modules: DevModuleStatus[];
}

export const devKeys = {
  status: () => ["dev", "status"] as const,
  layout: (moduleId: string) => ["module-layout", moduleId] as const,
};

/** `data` is the dev status, or `null` when dev tools are off (404). */
export function useDevStatus() {
  return useQuery({
    queryKey: devKeys.status(),
    queryFn: async () => {
      try {
        return await api<DevStatus>("/api/v1/dev/status");
      } catch (e) {
        if (e instanceof ApiError && e.status === 404) return null;
        throw e;
      }
    },
    staleTime: 30_000,
    retry: false,
  });
}

/** True only once the status call confirmed dev tools are on. */
export function useIsDev(): boolean {
  return !!useDevStatus().data?.dev;
}

let _devProbe: Promise<boolean> | null = null;

/** Hook-free, once-per-page dev check for the bundle loaders, which run
 * outside (or inside isolated) react-query clients. */
export function isDevMode(): Promise<boolean> {
  // Cache only a definite answer: a network error or 5xx (e.g. uvicorn --reload
  // restarting) must not switch hot reload off for the rest of the tab.
  _devProbe ??= api<DevStatus>("/api/v1/dev/status").then(
    (s) => !!s?.dev,
    (err) => {
      if (!(err instanceof ApiError && err.status === 404)) _devProbe = null;
      return false;
    },
  );
  return _devProbe;
}

/** Dev only: call `cb` whenever the window regains focus (the moment a module
 * author comes back from the editor). No listener at all outside dev. */
export function useOnDevFocus(cb: () => void): void {
  const ref = useRef(cb);
  ref.current = cb;
  useEffect(() => {
    let off = () => {};
    let cancelled = false;
    void isDevMode().then((dev) => {
      if (!dev || cancelled) return;
      const on = () => ref.current();
      window.addEventListener("focus", on);
      off = () => window.removeEventListener("focus", on);
    });
    return () => {
      cancelled = true;
      off();
    };
  }, []);
}

export function useModuleLayout(moduleId: string) {
  return useQuery({
    queryKey: devKeys.layout(moduleId),
    queryFn: () =>
      api<{ layout: ModuleLayout | null }>(
        `/api/v1/modules/${encodeURIComponent(moduleId)}/layout`,
      ).then((r) => r.layout),
    // The module page waits on this; a failure means "no layout", not a retry loop.
    retry: false,
  });
}

export function useSaveModuleLayout(moduleId: string) {
  const qc = useQueryClient();
  return useMutation({
    // The whole layout: page-level fields (`tabs`) must survive a save.
    mutationFn: (layout: ModuleLayout) =>
      api<{ layout: ModuleLayout }>(
        `/api/v1/dev/modules/${encodeURIComponent(moduleId)}/layout`,
        { method: "PUT", json: layout },
      ),
    onSuccess: (res) => {
      qc.setQueryData(devKeys.layout(moduleId), res.layout);
      void qc.invalidateQueries({ queryKey: devKeys.status() });
      // `supports_log_filter` turns on once layout.json exists on disk.
      void qc.invalidateQueries({ queryKey: ["modules"] });
    },
  });
}

export function useReloadModule(moduleId: string) {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: () =>
      // 200 even when the load fails: check `load_error` on the result.
      api<DevModuleStatus>(`/api/v1/dev/modules/${encodeURIComponent(moduleId)}/reload`, {
        method: "POST",
      }),
    // A reload can change datasets, widgets and the layout, so drop every
    // cache that is derived from the loaded manifest.
    onSettled: () => {
      for (const key of [["dev"], ["module-layout"], ["datasets"], ["dashboard-cards"], ["modules"]]) {
        void qc.invalidateQueries({ queryKey: key });
      }
    },
  });
}
