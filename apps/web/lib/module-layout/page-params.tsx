"use client";

import { createContext, useCallback, useContext, useMemo, useState } from "react";

/**
 * Page parameters of a module layout page: the values a `controls` card edits
 * and every dataset card / `action` card on the page sends to its route.
 *
 * Held in memory, scoped to the page (shared across its tabs). Only values the
 * user changed are stored; a route's own Python defaults apply otherwise.
 * Dashboards have no provider, so `usePageParamsOptional()` is null there.
 */
interface PageParams {
  values: Record<string, unknown>;
  set: (key: string, value: unknown) => void;
}

const PageParamsContext = createContext<PageParams | null>(null);

export function PageParamsProvider({ children }: { children: React.ReactNode }) {
  // An outer provider wins: the Studio holds one above Edit/Preview so values
  // survive the toggle, which remounts the page body.
  const outer = useContext(PageParamsContext);
  const [values, setValues] = useState<Record<string, unknown>>({});
  const set = useCallback(
    (key: string, value: unknown) => setValues((prev) => ({ ...prev, [key]: value })),
    [],
  );
  const ctx = useMemo(() => ({ values, set }), [values, set]);
  return <PageParamsContext.Provider value={outer ?? ctx}>{children}</PageParamsContext.Provider>;
}

export function usePageParamsOptional(): PageParams | null {
  return useContext(PageParamsContext);
}

/** Append `params` to `path` as query parameters (arrays as repeated keys),
 * joining with `&` when the path already carries a query string. Null and
 * undefined values are skipped so the route's default applies. */
export function withQuery(path: string, params: Record<string, unknown>): string {
  const qs = new URLSearchParams();
  for (const [k, v] of Object.entries(params)) {
    if (v == null) continue;
    for (const one of Array.isArray(v) ? v : [v]) qs.append(k, String(one));
  }
  const s = qs.toString();
  return s ? `${path}${path.includes("?") ? "&" : "?"}${s}` : path;
}
