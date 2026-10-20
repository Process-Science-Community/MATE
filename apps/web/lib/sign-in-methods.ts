/**
 * Profile -> Sign-in methods, server side: the signed-in user's linked identity
 * providers, through Keycloak's Account REST API with the session's own access token
 * (no admin credentials; the token carries the `account` audience and the
 * `manage-account-links` role).
 *
 * Why it exists: a Google / Microsoft / GitHub sign-in never attaches itself to an
 * existing account because the emails match (infra/keycloak/configure-social-idps.sh).
 * One person with several emails ends up with ONE MATE account by connecting the
 * methods here, while signed in. Keycloak enforces the rules: an identity that another
 * account already uses is refused, and so is removing the last way to sign in.
 *
 * Server-only (it reads the environment through @/auth); the browser talks to the
 * route handlers under /api/account/sign-in-methods.
 */

import { createHash, randomUUID } from "node:crypto";

import {
  KEYCLOAK_CLIENT_ID,
  KEYCLOAK_IDP_HINT,
  SOCIAL_IDPS_ENABLED,
  keycloakAccountEndpoint,
  keycloakLinkEndpoint,
} from "@/auth";
import { SOCIAL_IDPS, isSocialIdp } from "@/lib/social-idps";

export interface SignInMethod {
  /** The Keycloak IdP alias: google | microsoft | github | keycloak-oidc | … */
  id: string;
  label: string;
  /** This account can already sign in with it. */
  connected: boolean;
  /** The account's name at that provider (GitHub login, Google email, …) once connected. */
  linkedAs: string | null;
}

/** One row of GET {issuer}/account/linked-accounts. */
interface LinkedAccount {
  connected: boolean;
  providerAlias: string;
  displayName?: string;
  linkedUsername?: string;
}

export class AccountApiError extends Error {
  constructor(
    readonly status: number,
    message: string,
  ) {
    super(message);
  }
}

// Keycloak answers in milliseconds; a hung one must not hold the profile page.
const ACCOUNT_API_TIMEOUT_MS = 5000;

/** IdP aliases are used in URL paths – accept only what Keycloak itself produces. */
export function isIdpId(value: string): boolean {
  return /^[A-Za-z0-9][A-Za-z0-9._-]{0,63}$/.test(value);
}

async function accountApi(accessToken: string, path: string, init: RequestInit = {}): Promise<Response> {
  return fetch(keycloakAccountEndpoint(path), {
    ...init,
    headers: { Authorization: `Bearer ${accessToken}`, Accept: "application/json" },
    signal: AbortSignal.timeout(ACCOUNT_API_TIMEOUT_MS),
    cache: "no-store",
  });
}

/** The words the login page uses, not Keycloak's display names. */
function labelFor(alias: string, displayName: string | undefined): string {
  if (KEYCLOAK_IDP_HINT && alias === KEYCLOAK_IDP_HINT) return "University account";
  if (isSocialIdp(alias)) return SOCIAL_IDPS[alias].label;
  return displayName || alias;
}

/**
 * Every identity provider the realm offers, with whether this account has it linked.
 * A fixed order, so rows do not jump when one is connected or removed: the university
 * account, then the providers in KEYCLOAK_SOCIAL_IDPS order, then anything else the
 * realm has by name.
 */
export async function listSignInMethods(accessToken: string): Promise<SignInMethod[]> {
  const resp = await accountApi(accessToken, "/linked-accounts");
  if (!resp.ok) throw new AccountApiError(resp.status, `Keycloak account API answered HTTP ${resp.status}`);
  const rows = (await resp.json()) as LinkedAccount[];
  const methods = rows.map<SignInMethod>((row) => ({
    id: row.providerAlias,
    label: labelFor(row.providerAlias, row.displayName),
    connected: row.connected,
    linkedAs: row.connected ? (row.linkedUsername ?? null) : null,
  }));
  const rank = (m: SignInMethod): number => {
    if (KEYCLOAK_IDP_HINT && m.id === KEYCLOAK_IDP_HINT) return 0;
    const at = SOCIAL_IDPS_ENABLED.findIndex((id) => id === m.id);
    return at >= 0 ? 1 + at : 1 + SOCIAL_IDPS_ENABLED.length;
  };
  return methods.sort((a, b) => rank(a) - rank(b) || a.label.localeCompare(b.label));
}

/**
 * Remove a linked provider from the account. Keycloak refuses (400, with a message
 * fit to show) when it is the last way to sign in and the account has no password.
 */
export async function disconnectSignInMethod(accessToken: string, id: string): Promise<void> {
  const resp = await accountApi(accessToken, `/linked-accounts/${encodeURIComponent(id)}`, { method: "DELETE" });
  if (resp.status === 204) return;
  const body = (await resp.json().catch(() => null)) as { errorMessage?: string } | null;
  // The text is a raw message-bundle string, where a literal apostrophe is written ''.
  const reason = body?.errorMessage?.replace(/''/g, "'");
  throw new AccountApiError(resp.status, reason ?? "Could not disconnect that sign-in method.");
}

/** The Keycloak session id (`sid`) in an access token's payload. No signature check: it
 * is the session's own token, freshly minted by Keycloak, and only binds the link request
 * below to this session – Keycloak verifies the result against its own session. */
export function sessionIdOf(accessToken: string): string | null {
  const payload = accessToken.split(".")[1];
  if (!payload) return null;
  try {
    const claims = JSON.parse(Buffer.from(payload, "base64url").toString("utf8")) as {
      sid?: unknown;
      session_state?: unknown;
    };
    const sid = claims.sid ?? claims.session_state;
    return typeof sid === "string" && sid ? sid : null;
  } catch {
    return null;
  }
}

/**
 * Where to send the browser to connect provider `id` to the signed-in account:
 * Keycloak's client-initiated account linking. The request is bound to this session
 * and this client by `hash` = SHA-256(nonce + session id + client id + alias) – only a
 * server holding the session's token can build it, so another site cannot start a link
 * on the user's behalf – and `redirect_uri` must be one the web client lists (the
 * profile URL, registered by infra/keycloak/configure-social-idps.sh). Keycloak signs
 * the user in at the provider, attaches the identity to THIS account and returns the
 * browser to `redirectUri`, with `link_error=…` appended if it could not.
 */
export function brokerLinkUrl(sessionId: string, id: string, redirectUri: string): string {
  const nonce = randomUUID();
  const hash = createHash("sha256")
    .update(nonce + sessionId + KEYCLOAK_CLIENT_ID + id)
    .digest("base64url");
  const url = new URL(keycloakLinkEndpoint(id));
  url.searchParams.set("client_id", KEYCLOAK_CLIENT_ID);
  url.searchParams.set("redirect_uri", redirectUri);
  url.searchParams.set("nonce", nonce);
  url.searchParams.set("hash", hash);
  return url.toString();
}
