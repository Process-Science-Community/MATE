import { cookies } from "next/headers";
import { redirect } from "next/navigation";

import { auth, signIn, DEMO_MODE, KEYCLOAK_IDP_HINT, SOCIAL_IDPS_ENABLED } from "@/auth";
import { SocialIcon } from "@/components/brand-icons";
import { ThemeToggleButton } from "@/components/theme-toggle-button";
import { MateLogo } from "@/components/mate-logo";
import { BorderBeam } from "@/components/glass/border-beam";
import { safeCallbackUrl } from "@/lib/safe-callback-url";
import {
  LAST_DOOR_COOKIE,
  SOCIAL_IDPS,
  doorQuery,
  isLoginDoor,
  type LoginDoor,
} from "@/lib/social-idps";
import { DemoAutoSignIn } from "./demo-auto-signin";
import { LoginCta, DemoSubmitButton } from "./login-cta";
import { RecoveryAutoRetry } from "./recovery-auto-retry";

export default async function LoginPage({
  searchParams,
}: {
  searchParams: Promise<{ callbackUrl?: string; error?: string }>;
}) {
  const session = await auth();
  const params = await searchParams;
  // Same-origin paths only: the signed-in redirect below would otherwise follow
  // any /login?callbackUrl=https://elsewhere link (open redirect).
  const callbackUrl = safeCallbackUrl(params.callbackUrl);

  // A healthy session → straight into the app.
  if (session && session.error !== "RefreshAccessTokenError") {
    redirect(callbackUrl);
  }

  // A refresh-failed session keeps a valid-looking cookie, so `auth()` (here and
  // in the platform layout) keeps returning it and bouncing back to /login – and
  // a server `redirect()` can't delete the cookie, so it never recovers (the
  // lock-in). Break it: /login/start wipes the stale cookie before a fresh
  // OAuth round-trip, which (like every sign-in) forces Keycloak → the
  // university IdP to re-authenticate rather than silently reissue a session
  // tied to the dead one.
  //
  // The CTA is a plain link (GET /login/start), not a server action: after
  // hydration a server action submits over fetch(), which dies under the same
  // fetch-layer interception (content blocker / stale service worker) that
  // strands Safari in the login loop. A navigation always works.
  const staleSession = session?.error === "RefreshAccessTokenError";
  const startUrl = `/login/start?callbackUrl=${encodeURIComponent(callbackUrl)}`;
  // With a brokered IdP configured, the CTA above jumps straight to it and
  // Keycloak's own form is unreachable - so accounts that live in the realm
  // itself (service/test accounts, external collaborators, break-glass admin)
  // need this second door. `local=1` tells /login/start to drop kc_idp_hint.
  // Hidden when no IdP is brokered, since the CTA already lands on that form.
  const localLoginUrl = KEYCLOAK_IDP_HINT ? `${startUrl}&local=1` : null;
  // Name the *method*, not the server: Keycloak is only what both go through. The
  // two ways in are the university account and an email + password that lives in
  // the realm itself. Without a brokered IdP the single CTA lands on the latter.
  const emailPasswordLabel = "Login with email";
  // Google / Microsoft / GitHub, when the deployment configured them (see
  // infra/keycloak/configure-social-idps.sh). Each is one more plain link to
  // /login/start, with the provider as the Keycloak hint.
  const showSocial = SOCIAL_IDPS_ENABLED.length > 0;
  // The door used last (cookie set by /login/start). Offered again as "Last used" – people
  // who forget which provider they picked end up with a second account – and what the
  // hands-free recovery goes through, instead of always the default (university) door.
  const remembered = (await cookies()).get(LAST_DOOR_COOKIE)?.value;
  const lastDoor: LoginDoor | null =
    isLoginDoor(remembered) &&
    (remembered === "email" ||
      (remembered === "university" ? Boolean(KEYCLOAK_IDP_HINT) : SOCIAL_IDPS_ENABLED.includes(remembered)))
      ? remembered
      : null;
  const signInAgainUrl = lastDoor ? `${startUrl}${doorQuery(lastDoor)}` : startUrl;
  // Only with providers on offer: a deployment without them keeps its two plain buttons.
  const lastUsed = (door: LoginDoor) => (showSocial && !staleSession && lastDoor === door ? "Last used" : undefined);

  return (
    <div className="relative z-10 w-full max-w-sm space-y-6 rounded-2xl border border-white/15 [border-top-color:var(--glass-refraction-top)] bg-card/70 p-8 shadow-xl backdrop-blur-2xl backdrop-saturate-150 supports-[backdrop-filter]:bg-card/60">
      <BorderBeam className="rounded-2xl" />
      <ThemeToggleButton className="absolute right-4 top-4 h-8 w-8 cursor-pointer text-muted-foreground" />
      <div className="space-y-2 text-center">
        <MateLogo className="mx-auto h-11 w-11 text-foreground" />
        <h1 className="text-2xl font-semibold">MATE</h1>
        <p className="text-balance text-sm text-muted-foreground">
          {DEMO_MODE
            ? "Demo mode – signing you in…"
            : staleSession
              ? "Your session expired – signing you back in…"
              : localLoginUrl || showSocial
                ? "Choose how you want to sign in."
                : "Sign in with your email and password to continue."}
        </p>
      </div>
      {DEMO_MODE ? (
        <>
          <DemoAutoSignIn callbackUrl={callbackUrl} />
          <form
            action={async () => {
              "use server";
              await signIn("demo", { redirectTo: callbackUrl });
            }}
          >
            <DemoSubmitButton label="Enter demo workspace" pendingLabel="Entering…" />
          </form>
        </>
      ) : (
        <>
          {/* Dead refresh token → auto-navigate once to recover hands-free; the
              one-shot guard inside stops a failing OAuth from looping and leaves
              the manual link as the fallback. */}
          {staleSession ? <RecoveryAutoRetry href={signInAgainUrl} /> : null}
          {/* Auth.js sends a failed sign-in here (`pages.signIn`) with ?error=…; say so
              instead of silently showing the same buttons. Generic on purpose: the
              error code is Auth.js internals, not something to show a user. */}
          {params.error && !staleSession ? (
            <p
              role="alert"
              className="rounded-md border border-destructive/30 bg-destructive/10 px-3 py-2 text-center text-sm text-destructive"
            >
              Sign-in didn&apos;t complete. Please try again.
            </p>
          ) : null}
          <LoginCta
            href={staleSession ? signInAgainUrl : startUrl}
            label={
              staleSession
                ? "Sign in again"
                : localLoginUrl
                  ? "Login with university account"
                  : emailPasswordLabel
            }
            pendingLabel="Signing in…"
            badge={lastUsed(localLoginUrl ? "university" : "email")}
          />
          {showSocial ? (
            <>
              <div className="flex items-center gap-3 text-xs text-muted-foreground">
                <span className="h-px flex-1 bg-border" />
                <span>Or continue with</span>
                <span className="h-px flex-1 bg-border" />
              </div>
              <div className="space-y-3">
                {SOCIAL_IDPS_ENABLED.map((id) => (
                  <LoginCta
                    key={id}
                    href={`${startUrl}${doorQuery(id)}`}
                    variant="outline"
                    icon={<SocialIcon id={id} className="size-[18px]" />}
                    label={SOCIAL_IDPS[id].label}
                    pendingLabel="Signing in…"
                    badge={lastUsed(id)}
                  />
                ))}
              </div>
            </>
          ) : null}
          {localLoginUrl ? (
            <LoginCta
              href={localLoginUrl}
              variant="ghost"
              label={emailPasswordLabel}
              pendingLabel="Signing in…"
              badge={lastUsed("email")}
            />
          ) : null}
          {showSocial ? (
            <p className="text-balance text-center text-xs text-muted-foreground">
              Each way of signing in is its own account until you connect them under Profile → Sign-in methods.
            </p>
          ) : null}
        </>
      )}
    </div>
  );
}
