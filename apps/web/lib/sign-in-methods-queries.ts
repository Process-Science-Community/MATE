"use client";

import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";

import type { SignInMethod } from "@/lib/sign-in-methods";

/**
 * Profile -> Sign-in methods data layer. Talks to the same-origin route handlers under
 * /api/account/sign-in-methods (cookie session), which call Keycloak's Account API
 * server-side - the browser never holds a token for it. Not the FastAPI `api()` wrapper:
 * this is identity, not platform data.
 */

export interface SignInMethodsResponse {
  /** False when no provider is enabled (KEYCLOAK_SOCIAL_IDPS) and in demo mode, which has
   * no Keycloak to connect anything to: the Profile card is then not shown. */
  available: boolean;
  methods: SignInMethod[];
}

export const signInMethodsKey = ["account", "sign-in-methods"] as const;

async function request<T>(url: string, init?: RequestInit): Promise<T> {
  const resp = await fetch(url, { ...init, cache: "no-store" });
  const body = (await resp.json().catch(() => null)) as (T & { error?: string }) | null;
  if (!resp.ok) throw new Error(body?.error ?? `Request failed (${resp.status})`);
  return body as T;
}

export function useSignInMethods(enabled: boolean) {
  return useQuery({
    queryKey: signInMethodsKey,
    queryFn: () => request<SignInMethodsResponse>("/api/account/sign-in-methods"),
    enabled,
    retry: false,
  });
}

/** Disconnect a provider. Optimistic, like every edit of an existing resource: the row
 * flips at once, rolls back if Keycloak refuses (the last way to sign in cannot go). */
export function useDisconnectSignInMethod() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: (id: string) =>
      request<{ ok: boolean }>(`/api/account/sign-in-methods/${encodeURIComponent(id)}`, { method: "DELETE" }),
    onMutate: async (id) => {
      await qc.cancelQueries({ queryKey: signInMethodsKey });
      const previous = qc.getQueryData<SignInMethodsResponse>(signInMethodsKey);
      if (previous) {
        qc.setQueryData<SignInMethodsResponse>(signInMethodsKey, {
          ...previous,
          methods: previous.methods.map((m) => (m.id === id ? { ...m, connected: false, linkedAs: null } : m)),
        });
      }
      return { previous };
    },
    onError: (_err, _id, ctx) => {
      if (ctx?.previous) qc.setQueryData(signInMethodsKey, ctx.previous);
    },
    onSettled: () => void qc.invalidateQueries({ queryKey: signInMethodsKey }),
  });
}
