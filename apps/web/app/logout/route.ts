/**
 * Server-side logout as a plain top-level navigation (GET /logout).
 *
 * The client-side signOut() is a fetch to /api/auth/* – exactly what a content
 * blocker, privacy extension, or stale service worker intercepts. When that
 * happens the session cookie survives, /login sees a healthy session and
 * bounces straight back → the Safari infinite login loop. A document
 * navigation can't be intercepted at the fetch layer, so this route is the
 * reliable way out: it deletes the server-side session entry, ends the Keycloak
 * SSO session, expires every auth cookie on the redirect response, and lands on
 * /login.
 *
 * GET with a side effect is deliberate – logout is idempotent, and it must be
 * reachable via `window.location.assign` / a plain <a> with no JS at all.
 */
import { NextResponse } from "next/server";
import type { NextRequest } from "next/server";

import { endKeycloakSession, readRefreshTokens } from "@/auth";
import { SESSION_COOKIE_NAMES, isAuthCookie } from "@/lib/clear-session";
import { safeCallbackUrl } from "@/lib/safe-callback-url";
import * as sessionStore from "@/lib/session-store";

export async function GET(req: NextRequest): Promise<NextResponse> {
  // The Keycloak refresh token lives in the session about to be dropped – read
  // it first.
  const refreshTokens = await readRefreshTokens(req);

  // With the server-side store (prod), the cookie value is the opaque sid –
  // drop the entry so the session is dead even if a cookie copy lingers.
  // Cookie-JWT sessions (dev) have no server state; expiring the cookie below
  // is enough. deleteSession validates the sid format itself.
  if (sessionStore.sessionStoreEnabled) {
    for (const name of SESSION_COOKIE_NAMES) {
      const sid = req.cookies.get(name)?.value;
      if (sid) await sessionStore.deleteSession(sid);
    }
  }

  // End the Keycloak SSO session behind it too. Left alive, the next sign-in –
  // through either login button – is silently re-issued from the SSO cookie as
  // the previous user (shared computers, switching accounts). Best effort: a
  // failure never blocks the logout, and prompt=login on every sign-in
  // (/login/start) is the second layer.
  await Promise.all(refreshTokens.map((refreshToken) => endKeycloakSession(refreshToken)));

  // Same-origin paths only (no `//host`, `/\host` or absolute URLs → no open redirect).
  const safeCb = safeCallbackUrl(req.nextUrl.searchParams.get("callbackUrl"), "");
  const location = safeCb ? `/login?callbackUrl=${encodeURIComponent(safeCb)}` : "/login";

  // Relative Location keeps the redirect scheme/host-agnostic behind the
  // proxy chain (req.nextUrl reflects the internal hop, not the public URL).
  const res = new NextResponse(null, { status: 303, headers: { Location: location } });
  for (const cookie of req.cookies.getAll()) {
    if (!isAuthCookie(cookie.name)) continue;
    // Mirror the attributes Auth.js sets so the browser actually drops the
    // cookie (a `__Secure-` cookie only dies via a Secure Set-Cookie).
    res.cookies.set(cookie.name, "", {
      path: "/",
      maxAge: 0,
      httpOnly: true,
      sameSite: "lax",
      secure: cookie.name.startsWith("__Secure-"),
    });
  }
  return res;
}
