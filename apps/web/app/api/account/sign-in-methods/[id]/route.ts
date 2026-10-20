/**
 * DELETE /api/account/sign-in-methods/<id> – disconnect one identity provider from the
 * signed-in account. Keycloak decides whether that is allowed: it refuses to remove the
 * last way to sign in, and answers with a message fit to show, passed through here.
 */
import { NextResponse } from "next/server";

import { auth } from "@/auth";
import { AccountApiError, disconnectSignInMethod, isIdpId } from "@/lib/sign-in-methods";

export const dynamic = "force-dynamic";

export async function DELETE(
  _req: Request,
  { params }: { params: Promise<{ id: string }> },
): Promise<NextResponse> {
  const { id } = await params;
  if (!isIdpId(id)) return NextResponse.json({ error: "Unknown sign-in method." }, { status: 404 });

  const session = await auth();
  if (!session?.accessToken || session.error || session.provider === "demo") {
    return NextResponse.json({ error: "Not signed in." }, { status: 401 });
  }

  try {
    await disconnectSignInMethod(session.accessToken, id);
    return NextResponse.json({ ok: true });
  } catch (err) {
    if (err instanceof AccountApiError && err.status >= 400 && err.status < 500) {
      return NextResponse.json({ error: err.message }, { status: err.status });
    }
    console.warn("[account] disconnecting a sign-in method failed:", err instanceof Error ? err.message : err);
    return NextResponse.json({ error: "Sign-in methods are unavailable right now." }, { status: 502 });
  }
}
