/**
 * GET /api/account/sign-in-methods – the identity providers this account can sign in
 * with, and the ones it could still connect (Profile -> Sign-in methods).
 *
 * Answers from Keycloak's Account API with the session's own access token, so it is
 * per user and never cached. See lib/sign-in-methods.
 */
import { NextResponse } from "next/server";

import { SOCIAL_IDPS_ENABLED, auth } from "@/auth";
import { listSignInMethods } from "@/lib/sign-in-methods";

export const dynamic = "force-dynamic";

export async function GET(): Promise<NextResponse> {
  const session = await auth();
  if (!session?.accessToken || session.error) {
    return NextResponse.json({ error: "Not signed in." }, { status: 401 });
  }
  // Nothing to bring together unless providers are on offer – a deployment that did not
  // enable any sees no change – and the demo session has no Keycloak behind it at all.
  if (SOCIAL_IDPS_ENABLED.length === 0 || session.provider === "demo") {
    return NextResponse.json({ available: false, methods: [] });
  }

  try {
    return NextResponse.json({ available: true, methods: await listSignInMethods(session.accessToken) });
  } catch (err) {
    console.warn("[account] listing sign-in methods failed:", err instanceof Error ? err.message : err);
    return NextResponse.json({ error: "Sign-in methods are unavailable right now." }, { status: 502 });
  }
}
