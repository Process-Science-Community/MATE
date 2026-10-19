/**
 * Fetch-free sign-in entry point (GET /login/start).
 *
 * The login CTA links here instead of posting a server action: after
 * hydration, server actions are fetch() calls, and the environments that
 * break the session fetch (content blocker / privacy extension / stale
 * service worker – the Safari login loop) break those the same way. As a
 * plain navigation this works even when every fetch on the page is
 * intercepted: wipe stale auth cookies (dead session + leftover PKCE/state
 * check cookies), then hand off to the Keycloak authorize redirect.
 *
 * Every sign-in sends `prompt=login`: Keycloak – and, through it, the
 * university IdP – must show its login form instead of silently re-issuing a
 * code from a live SSO session. /logout ends the Keycloak session, but this is
 * what still holds when that call failed, when the IdP's own session is alive,
 * and when a refresh-failed session has to be replaced rather than reissued.
 *
 * `?local=1` omits kc_idp_hint so Keycloak renders its OWN login form instead
 * of bouncing to the university IdP - the only way in for accounts that live in
 * the realm itself (service/test accounts, external collaborators, the
 * break-glass admin). Requires the browser flow's Identity Provider Redirector
 * to have NO default provider, or Keycloak redirects anyway, hint or not.
 */
import type { NextRequest } from "next/server";

import { signIn, KEYCLOAK_IDP_HINT } from "@/auth";
import { clearAuthCookies } from "@/lib/clear-session";
import { safeCallbackUrl } from "@/lib/safe-callback-url";

export async function GET(req: NextRequest): Promise<Response> {
  // Same-origin paths only (no `//host`, `/\host` or absolute URLs → no open redirect).
  const callbackUrl = safeCallbackUrl(req.nextUrl.searchParams.get("callbackUrl"));
  const localAccount = req.nextUrl.searchParams.get("local") === "1";

  const authParams: Record<string, string> = { prompt: "login" };
  if (!localAccount && KEYCLOAK_IDP_HINT) authParams.kc_idp_hint = KEYCLOAK_IDP_HINT;

  await clearAuthCookies();
  // Throws NEXT_REDIRECT to the Keycloak authorize URL (Auth.js sets the fresh
  // PKCE/state cookies on that redirect response).
  await signIn("keycloak", { redirectTo: callbackUrl }, authParams);
  // signIn always redirects; reaching here means it didn't – fall back to the
  // login form instead of rendering an empty 200.
  return new Response(null, { status: 303, headers: { Location: "/login" } });
}
