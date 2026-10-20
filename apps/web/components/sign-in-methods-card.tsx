"use client";

import { useEffect, useState } from "react";
import { GraduationCap, Link2, Loader2 } from "lucide-react";
import { toast } from "sonner";

import { SocialIcon } from "@/components/brand-icons";
import { Button } from "@/components/ui/button";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { Skeleton } from "@/components/ui/skeleton";
import { useDisconnectSignInMethod, useSignInMethods } from "@/lib/sign-in-methods-queries";
import { isSocialIdp } from "@/lib/social-idps";
import { toastError } from "@/lib/toast";

// Keycloak's own codes (broker link endpoint), plus `unavailable` from our connect route.
const LINK_ERRORS: Record<string, string> = {
  not_logged_in: "Your sign-in at the identity server has ended. Sign out and back in, then try again.",
  not_allowed: "This account may not connect sign-in methods.",
  unknown_identity_provider: "That sign-in method is not available.",
  invalid_token: "Your session could not be verified. Sign out and back in, then try again.",
  unavailable: "Sign-in methods are unavailable right now. Try again in a moment.",
};

function MethodIcon({ id }: { id: string }) {
  if (isSocialIdp(id)) return <SocialIcon id={id} className="size-4" />;
  // The university IdP and any provider this app has no mark for.
  return id === "keycloak-oidc" ? <GraduationCap className="size-4" /> : <Link2 className="size-4" />;
}

/** The Connect button is a plain link (GET …/connect): the browser leaves for the
 * provider through Keycloak, and a fetch() there would fail the way every other
 * sign-in navigation avoids (see login-cta.tsx). `pageshow` undoes the spinner when
 * the back button restores this page from the cache. */
function ConnectButton({ id, label }: { id: string; label: string }) {
  const [pending, setPending] = useState(false);
  useEffect(() => {
    const reset = () => setPending(false);
    window.addEventListener("pageshow", reset);
    return () => window.removeEventListener("pageshow", reset);
  }, []);
  return (
    <Button asChild variant="outline" size="sm" className="shrink-0">
      <a
        href={`/api/account/sign-in-methods/${encodeURIComponent(id)}/connect`}
        aria-label={`Connect ${label}`}
        aria-busy={pending}
        onClick={() => setPending(true)}
      >
        {pending ? <Loader2 className="h-3.5 w-3.5 animate-spin" /> : null}
        Connect
      </a>
    </Button>
  );
}

/**
 * Profile -> Sign-in methods: which identity providers can sign in to THIS account, and
 * the means to add or drop one. Several emails, several providers, one account: signing
 * in with a provider never attaches itself to an existing account (it would otherwise
 * be an email match nobody proved), so this is where they are brought together.
 */
export function SignInMethodsCard({ enabled }: { enabled: boolean }) {
  const { data, isLoading, isError, refetch } = useSignInMethods(enabled);
  const disconnect = useDisconnectSignInMethod();
  const [linkError, setLinkError] = useState<string | null>(null);

  // Keycloak returns here with ?link_error=<code> when a connect did not go through.
  useEffect(() => {
    const params = new URLSearchParams(window.location.search);
    const code = params.get("link_error");
    if (!code) return;
    setLinkError(LINK_ERRORS[code] ?? "That sign-in method could not be connected.");
    params.delete("link_error");
    params.delete("nonce");
    const rest = params.toString();
    window.history.replaceState(null, "", window.location.pathname + (rest ? `?${rest}` : ""));
  }, []);

  if (!enabled || (data && !data.available)) return null;

  return (
    <Card>
      <CardHeader>
        <CardTitle className="text-base">Sign-in methods</CardTitle>
      </CardHeader>
      <CardContent className="space-y-4 text-sm">
        <p className="text-muted-foreground">
          Every way of signing in belongs to one MATE account. Connect the ones you use and any of
          them signs you in to this account, with the same processes, dashboards and settings. One
          that another MATE account already uses cannot be connected here.
        </p>

        {linkError ? (
          <p
            role="alert"
            className="rounded-md border border-destructive/30 bg-destructive/10 px-3 py-2 text-destructive"
          >
            {linkError}
          </p>
        ) : null}

        {isLoading ? (
          <div className="space-y-3">
            <Skeleton className="h-10 w-full" />
            <Skeleton className="h-10 w-full" />
          </div>
        ) : isError || !data ? (
          <div className="flex items-center justify-between gap-3 text-muted-foreground">
            <span>Sign-in methods are unavailable right now.</span>
            <Button variant="outline" size="sm" onClick={() => void refetch()}>
              Retry
            </Button>
          </div>
        ) : (
          <ul className="divide-y rounded-md border">
            {data.methods.map((m) => (
              <li key={m.id} className="flex items-center gap-3 px-3 py-2.5">
                <span className="flex size-8 shrink-0 items-center justify-center rounded-md border bg-background">
                  <MethodIcon id={m.id} />
                </span>
                <div className="min-w-0 flex-1">
                  <p className="font-medium">{m.label}</p>
                  <p className="break-words text-xs text-muted-foreground">
                    {m.connected ? `Connected${m.linkedAs ? ` as ${m.linkedAs}` : ""}` : "Not connected"}
                  </p>
                </div>
                {m.connected ? (
                  <Button
                    variant="outline"
                    size="sm"
                    className="shrink-0 cursor-pointer"
                    aria-label={`Disconnect ${m.label}`}
                    disabled={disconnect.isPending && disconnect.variables === m.id}
                    onClick={() =>
                      disconnect.mutate(m.id, {
                        onSuccess: () => toast.success(`${m.label} disconnected`),
                        onError: (err) => toastError("Could not disconnect", { description: err.message }),
                      })
                    }
                  >
                    Disconnect
                  </Button>
                ) : (
                  <ConnectButton id={m.id} label={m.label} />
                )}
              </li>
            ))}
          </ul>
        )}
      </CardContent>
    </Card>
  );
}
