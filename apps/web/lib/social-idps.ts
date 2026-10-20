/**
 * The sign-in doors of the login page, and the identity providers behind them.
 *
 * `SOCIAL_IDPS` ids are the Keycloak IdP aliases infra/keycloak/configure-social-idps.sh
 * creates – which are also the last-but-one segment of the callback URL registered at
 * each provider (…/broker/<id>/endpoint), so they must not change. Adding a provider
 * means a new entry here, one in that script, a logo in components/brand-icons.tsx and
 * one in the Keycloak theme (infra/keycloak/themes/README.md).
 *
 * Pure data and no "use client": shared by server components, route handlers and the
 * client tree.
 */

export const SOCIAL_IDPS = {
  google: { label: "Google" },
  microsoft: { label: "Microsoft" },
  github: { label: "GitHub" },
} as const;

export type SocialIdp = keyof typeof SOCIAL_IDPS;

export function isSocialIdp(value: string | null | undefined): value is SocialIdp {
  return typeof value === "string" && (Object.keys(SOCIAL_IDPS) as string[]).includes(value);
}

/**
 * KEYCLOAK_SOCIAL_IDPS ("google,microsoft") → the providers to offer, in the order
 * given. Anything unknown is dropped, so a typo cannot put an arbitrary string into
 * `kc_idp_hint`, and a provider appears once.
 */
export function parseSocialIdps(raw: string | undefined): SocialIdp[] {
  const ids = (raw ?? "")
    .split(/[\s,]+/)
    .map((id) => id.trim().toLowerCase())
    .filter(isSocialIdp);
  return [...new Set(ids)];
}

/** A way into MATE: the university IdP, Keycloak's own email form, or one provider. */
export type LoginDoor = "university" | "email" | SocialIdp;

export function isLoginDoor(value: string | null | undefined): value is LoginDoor {
  return value === "university" || value === "email" || isSocialIdp(value);
}

/**
 * The door that was used last, remembered for the login page ("Last used") and for the
 * hands-free recovery after a session ends, which would otherwise always re-enter
 * through the default door – the university IdP in production – whoever signed in.
 * Holds a door name only, nothing about the person.
 */
export const LAST_DOOR_COOKIE = "mate-last-door";
export const LAST_DOOR_MAX_AGE = 60 * 60 * 24 * 365; // seconds

/** The extra /login/start query parameters that select a door (none = the default one). */
export function doorQuery(door: LoginDoor): string {
  if (door === "university") return "";
  if (door === "email") return "&local=1";
  return `&idp=${door}`;
}
