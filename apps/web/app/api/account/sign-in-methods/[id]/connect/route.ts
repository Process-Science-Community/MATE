/**
 * GET /api/account/sign-in-methods/<id>/connect – start connecting one identity
 * provider to the signed-in account.
 *
 * A plain navigation (the Profile button is an <a>), like /login/start and /logout: the
 * browser leaves for Keycloak's link endpoint, which signs the user in at the provider,
 * attaches that identity to THIS account and sends the browser back to /profile. Failures
 * come back as /profile?link_error=<code> (Keycloak's own codes, plus `unavailable`);
 * Keycloak shows its themed error page itself for the rest (an identity another account
 * already uses, say). The request is bound to this session by a hash only this server can
 * build (lib/sign-in-methods.brokerLinkUrl).
 */
import { NextResponse } from "next/server";
import type { NextRequest } from "next/server";

import { auth } from "@/auth";
import { brokerLinkUrl, isIdpId, listSignInMethods, sessionIdOf } from "@/lib/sign-in-methods";

export const dynamic = "force-dynamic";

/** Relative Location keeps the redirect scheme/host-agnostic behind the proxy chain. */
function back(path: string): NextResponse {
  return new NextResponse(null, { status: 303, headers: { Location: path } });
}

export async function GET(
  req: NextRequest,
  { params }: { params: Promise<{ id: string }> },
): Promise<NextResponse> {
  const { id } = await params;
  if (!isIdpId(id)) return back("/profile?link_error=unknown_identity_provider");

  const session = await auth();
  if (!session?.accessToken || session.error || session.provider === "demo") {
    return back(`/login?callbackUrl=${encodeURIComponent("/profile")}`);
  }

  let method;
  try {
    method = (await listSignInMethods(session.accessToken)).find((m) => m.id === id);
  } catch (err) {
    console.warn("[account] connecting a sign-in method failed:", err instanceof Error ? err.message : err);
    return back("/profile?link_error=unavailable");
  }
  if (!method) return back("/profile?link_error=unknown_identity_provider");
  if (method.connected) return back("/profile");

  const sessionId = sessionIdOf(session.accessToken);
  if (!sessionId) return back("/profile?link_error=invalid_token");

  // The public base URL: req.nextUrl is the internal hop behind the proxy chain.
  const base = (process.env.AUTH_URL || req.nextUrl.origin).replace(/\/+$/, "");
  return NextResponse.redirect(brokerLinkUrl(sessionId, id, `${base}/profile`), 303);
}
