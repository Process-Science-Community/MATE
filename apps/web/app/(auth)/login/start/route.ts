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
 * Which door the sign-in goes through:
 *  - no parameter: the default one – the university IdP when KEYCLOAK_IDP_HINT is
 *    set, otherwise Keycloak's own email form;
 *  - `?local=1` omits kc_idp_hint so Keycloak renders its OWN login form instead
 *    of bouncing to the university IdP - the only way in for accounts that live in
 *    the realm itself (service/test accounts, external collaborators, the
 *    break-glass admin). Requires the browser flow's Identity Provider Redirector
 *    to have NO default provider, or Keycloak redirects anyway, hint or not;
 *  - `?idp=google|microsoft|github` sends that provider as the hint, so Keycloak
 *    goes straight to it. Only providers listed in KEYCLOAK_SOCIAL_IDPS count.
 * The door used is remembered (LAST_DOOR_COOKIE) for the login page and for the
 * hands-free recovery after a session ends.
 */
import { cookies } from "next/headers";
import type { NextRequest } from "next/server";

import { signIn, KEYCLOAK_IDP_HINT, SOCIAL_IDPS_ENABLED } from "@/auth";
import { clearAuthCookies } from "@/lib/clear-session";
import { safeCallbackUrl } from "@/lib/safe-callback-url";
import { LAST_DOOR_COOKIE, LAST_DOOR_MAX_AGE, isSocialIdp, type LoginDoor } from "@/lib/social-idps";

export async function GET(req: NextRequest): Promise<Response> {
  // Same-origin paths only (no `//host`, `/\host` or absolute URLs → no open redirect).
  const callbackUrl = safeCallbackUrl(req.nextUrl.searchParams.get("callbackUrl"));
  const localAccount = req.nextUrl.searchParams.get("local") === "1";
  const idp = req.nextUrl.searchParams.get("idp");

  const authParams: Record<string, string> = { prompt: "login" };
  let door: LoginDoor;
  if (idp !== null) {
    // A stale bookmark or a hand-made link names a provider this deployment does not
    // offer: back to the choice, not into a sign-in nobody asked for.
    if (!isSocialIdp(idp) || !SOCIAL_IDPS_ENABLED.includes(idp)) {
      return new Response(null, { status: 303, headers: { Location: "/login" } });
    }
    door = idp;
    authParams.kc_idp_hint = idp;
  } else if (localAccount || !KEYCLOAK_IDP_HINT) {
    door = "email";
  } else {
    door = "university";
    authParams.kc_idp_hint = KEYCLOAK_IDP_HINT;
  }

  await clearAuthCookies();
  (await cookies()).set(LAST_DOOR_COOKIE, door, {
    path: "/",
    maxAge: LAST_DOOR_MAX_AGE,
    httpOnly: true,
    sameSite: "lax",
    secure: (process.env.AUTH_URL ?? "").startsWith("https://"),
  });
  // Throws NEXT_REDIRECT to the Keycloak authorize URL (Auth.js sets the fresh
  // PKCE/state cookies on that redirect response).
  await signIn("keycloak", { redirectTo: callbackUrl }, authParams);
  // signIn always redirects; reaching here means it didn't – fall back to the
  // login form instead of rendering an empty 200.
  return new Response(null, { status: 303, headers: { Location: "/login" } });
}
