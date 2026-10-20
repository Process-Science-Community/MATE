#!/usr/bin/env bash
#
# Configure Google, Microsoft and GitHub sign-in on a *running* Keycloak. Use this
# instead of editing the realm-export JSON when Keycloak already has a populated DB
# – the JSON is only imported on first boot (empty DB), so on a re-deploy it is
# ignored. Needs the OAuth app credentials of each provider (see docs/DEPLOY.md,
# "Google, Microsoft and GitHub sign-in", for how to register them).
#
# What it does (all idempotent – safe to re-run):
#   1. Creates the first-login flow `mate-social-first-login` once: a copy of
#      Keycloak's "first broker login" in which "Handle Existing Account" is
#      DISABLED and "Create User If Unique" is REQUIRED. A provider sign-in whose
#      email (or username) already belongs to a realm user therefore stops with an
#      error page instead of attaching to that account. That is the point: a
#      provider's email is not proof of owning the account behind it – Microsoft
#      (and any other Entra tenant admin) can put ANY address in `mail`, and
#      linking on it is how accounts get taken over or pre-hijacked. Accounts are
#      connected only from inside a signed-in session (Profile -> Sign-in methods).
#   2. For every provider whose <P>_CLIENT_ID and <P>_CLIENT_SECRET are both set,
#      creates/updates the identity provider (alias google | microsoft | github):
#      syncMode IMPORT (a later sign-in never rewrites name/email), the flow above,
#      PKCE S256, and trustEmail only where the provider vouches for the address
#      (Google, GitHub - not Microsoft). Providers without credentials are skipped,
#      never deleted.
#   3. Adds <WEB_URL>/profile to the web client's valid redirect URIs, where
#      Keycloak sends the browser back after Profile -> Sign-in methods linked an
#      account.
#
# It does NOT touch the browser flow (the Identity Provider Redirector must keep NO
# default provider, or "Login with email" and every provider button would bounce to
# the university IdP), the university IdP, users or roles.
#
# Requirements: docker (Keycloak running in a container), python3 on the host.
#
# Usage (local compose):
#   GITHUB_CLIENT_ID=... GITHUB_CLIENT_SECRET=... WEB_URL=http://localhost:3000 \
#     ./infra/keycloak/configure-social-idps.sh
#
# Usage (uni VM – Keycloak lives under /auth there):
#   KC_SERVER=http://localhost:8080/auth WEB_URL=https://mate.uni-muenster.de \
#   GOOGLE_CLIENT_ID=... GOOGLE_CLIENT_SECRET=... \
#   MICROSOFT_CLIENT_ID=... MICROSOFT_CLIENT_SECRET=... MICROSOFT_TENANT=... \
#   GITHUB_CLIENT_ID=... GITHUB_CLIENT_SECRET=... \
#     ./infra/keycloak/configure-social-idps.sh
#
# Then set KEYCLOAK_SOCIAL_IDPS=google,microsoft,github (the providers configured
# here) on the web service and restart it, so the MATE login page shows the buttons.
#
set -euo pipefail

# ── Config (override via env) ────────────────────────────────────────────────
KC_CONTAINER="${KC_CONTAINER:-mate-keycloak}"
REALM="${REALM:-flows-funds}"
# Internal admin endpoint. In prod Keycloak serves under /auth – set
# KC_SERVER=http://localhost:8080/auth then (KC_HTTP_RELATIVE_PATH=/auth).
KC_SERVER="${KC_SERVER:-http://localhost:8080}"
KC_ADMIN="${KEYCLOAK_ADMIN:-admin}"
KC_ADMIN_PASSWORD="${KEYCLOAK_ADMIN_PASSWORD:-admin}"

# First-login flow the providers use. The default is created here (see 1.). Point it
# at an existing flow instead (e.g. "first broker login" for Keycloak's stock
# behaviour: offer to link when the email matches) and nothing is created or changed.
SOCIAL_FIRST_LOGIN_FLOW="${SOCIAL_FIRST_LOGIN_FLOW:-mate-social-first-login}"
# PKCE (S256) on the Keycloak -> provider leg. All three document support for it;
# switch off only if a provider rejects it.
SOCIAL_PKCE="${SOCIAL_PKCE:-true}"

# Web client + public base URL of the deployment (https://mate.uni-muenster.de in
# prod, http://localhost:3000 in dev). Falls back to AUTH_URL, the web service's
# own name for it.
WEB_CLIENT_ID="${WEB_CLIENT_ID:-flows-funds-web}"
WEB_URL="${WEB_URL:-${AUTH_URL:-}}"
# Browser-facing Keycloak base URL, only to print the redirect URIs to register at
# each provider. Defaults to <WEB_URL>/auth (the prod layout).
KC_PUBLIC_URL="${KC_PUBLIC_URL:-${WEB_URL:+${WEB_URL%/}/auth}}"

# Per-provider OAuth app credentials (a provider is skipped when both are empty).
GOOGLE_CLIENT_ID="${GOOGLE_CLIENT_ID:-}"
GOOGLE_CLIENT_SECRET="${GOOGLE_CLIENT_SECRET:-}"
# Optional: only accounts of this Google Workspace domain ("*" = any Workspace
# account, a comma-separated list is accepted). Empty = any Google account.
GOOGLE_HOSTED_DOMAIN="${GOOGLE_HOSTED_DOMAIN:-}"

MICROSOFT_CLIENT_ID="${MICROSOFT_CLIENT_ID:-}"
MICROSOFT_CLIENT_SECRET="${MICROSOFT_CLIENT_SECRET:-}"
# Which Microsoft accounts may sign in – required, no default: it must equal what the
# Entra app registration's "Supported account types" allows (a mismatch fails every
# sign-in with an AADSTS error), and it is a trust decision:
#   <tenant id or domain>  one organisation; its admins vouch for the emails it asserts
#   organizations          work/school accounts of ANY organisation
#   consumers              personal Microsoft accounts only
#   common                 both of the above
MICROSOFT_TENANT="${MICROSOFT_TENANT:-}"

GITHUB_CLIENT_ID="${GITHUB_CLIENT_ID:-}"
GITHUB_CLIENT_SECRET="${GITHUB_CLIENT_SECRET:-}"
# Only for GitHub Enterprise Server (empty = github.com).
GITHUB_BASE_URL="${GITHUB_BASE_URL:-}"
GITHUB_API_URL="${GITHUB_API_URL:-}"

KCADM=/opt/keycloak/bin/kcadm.sh
FBL_FLOW="first%20broker%20login"   # Keycloak's stock flow, URL-encoded for the path

# kc: run kcadm in the container, output to host stdout.
kc()      { docker exec     "$KC_CONTAINER" "$KCADM" "$@"; }
# kc_pipe: same, but forwards host stdin (for `-f -` reads).
kc_pipe() { docker exec -i  "$KC_CONTAINER" "$KCADM" "$@"; }

if ! command -v python3 >/dev/null 2>&1; then
  echo "✗ python3 is required on the host." >&2
  exit 1
fi
if ! docker ps --format '{{.Names}}' | grep -qx "$KC_CONTAINER"; then
  echo "✗ container '$KC_CONTAINER' is not running (set KC_CONTAINER)." >&2
  exit 1
fi

# ── 0. Which providers are configured? ───────────────────────────────────────
# A half-configured provider is almost certainly a typo – stop before changing anything.
PROVIDERS=()
for p in google microsoft github; do
  up="$(printf '%s' "$p" | tr '[:lower:]' '[:upper:]')"
  id_var="${up}_CLIENT_ID"; secret_var="${up}_CLIENT_SECRET"
  if [ -n "${!id_var}" ] && [ -n "${!secret_var}" ]; then
    PROVIDERS+=("$p")
  elif [ -n "${!id_var}${!secret_var}" ]; then
    echo "✗ $p: set both $id_var and $secret_var (or neither)." >&2
    exit 2
  fi
done
if [ "${#PROVIDERS[@]}" -eq 0 ]; then
  echo "✗ no provider configured: set <PROVIDER>_CLIENT_ID and <PROVIDER>_CLIENT_SECRET" >&2
  echo "  for at least one of GOOGLE, MICROSOFT, GITHUB." >&2
  exit 2
fi
if [[ " ${PROVIDERS[*]} " == *" microsoft "* ]] && [ -z "$MICROSOFT_TENANT" ]; then
  cat >&2 <<'MSG'
✗ microsoft: set MICROSOFT_TENANT to what the Entra app registration's "Supported account
  types" allows:
    <tenant id or domain>  one organisation (its admins vouch for the emails it asserts)
    organizations          work/school accounts of ANY organisation
    consumers              personal Microsoft accounts only
    common                 both of the above
  Anyone who can create a tenant of their own can make `organizations` and `common` assert ANY
  email address – which is why MATE never links an account on a provider's email (see the
  header of this script). Prefer one tenant, or `consumers`, where that fits.
MSG
  exit 2
fi

# ── 1. Authenticate kcadm (stored in the container for subsequent calls) ─────
echo "→ Authenticating kcadm against $KC_SERVER (realm master)"
kc config credentials --server "$KC_SERVER" --realm master \
  --user "$KC_ADMIN" --password "$KC_ADMIN_PASSWORD"

# ── 1b. Warn about a default provider on the browser flow's redirector ───────
# configure-university-idp.sh sets one. With it, Keycloak sends EVERY sign-in that
# reaches its own login page – "Login with email", a link from an MCP client – straight
# to that provider, hint or not, so the email form and the provider buttons on
# Keycloak's page can never be shown. Read-only here: whether to clear it is the
# operator's call (it only matters if the university must stay the one way in).
redirector_cfg="$(kc get authentication/flows/browser/executions -r "$REALM" | python3 -c "
import json, sys
for e in json.load(sys.stdin):
    if e.get('providerId') == 'identity-provider-redirector':
        print(e.get('authenticationConfig') or ''); break")"
if [ -n "$redirector_cfg" ]; then
  default_provider="$(kc get "authentication/config/$redirector_cfg" -r "$REALM" | python3 -c "
import json, sys
print((json.load(sys.stdin).get('config') or {}).get('defaultProvider', ''))")"
  if [ -n "$default_provider" ]; then
    echo "  ! The browser flow's Identity Provider Redirector sends every sign-in that reaches" >&2
    echo "    Keycloak's own login page to '$default_provider': MATE's 'Login with email' lands there" >&2
    echo "    too, and the provider buttons on Keycloak's page never show. (The provider buttons on" >&2
    echo "    MATE's login page carry their own hint and still work.) To undo: Authentication ->" >&2
    echo "    Flows -> browser -> Identity Provider Redirector -> settings: delete the config." >&2
  fi
fi

# ── 2. The first-login flow ──────────────────────────────────────────────────
# execution_id <flow-path> <field> <value>: id of the execution of a flow whose
# <field> (displayName | providerId) equals <value>; empty if there is none. Copying a
# flow renames its sub-flows to "<new alias> <old name>", hence the suffix match.
execution_id() {
  kc get "authentication/flows/$1/executions" -r "$REALM" \
    | FIELD="$2" VALUE="$3" python3 -c "
import json, os, sys
want = os.environ['VALUE']
for e in json.load(sys.stdin):
    have = e.get(os.environ['FIELD']) or ''
    if have == want or have.endswith(' ' + want):
        print(e.get('id', '')); break"
}

# set_requirement <flow-path> <execution-id> <REQUIRED|ALTERNATIVE|DISABLED>
set_requirement() {
  kc update "authentication/flows/$1/executions" -r "$REALM" \
    -b "{\"id\":\"$2\",\"requirement\":\"$3\"}" >/dev/null
}

if [ "$SOCIAL_FIRST_LOGIN_FLOW" = "mate-social-first-login" ]; then
  FLOW="$SOCIAL_FIRST_LOGIN_FLOW"
  if kc get authentication/flows -r "$REALM" \
       | python3 -c "
import json, sys
sys.exit(0 if any(f.get('alias') == '$FLOW' for f in json.load(sys.stdin)) else 1)"; then
    echo "→ Flow '$FLOW' exists – re-applying its settings"
  else
    echo "→ Creating flow '$FLOW' (copy of 'first broker login')"
    kc create "authentication/flows/$FBL_FLOW/copy" -r "$REALM" -b "{\"newName\":\"$FLOW\"}" >/dev/null
  fi

  # Never attach a provider sign-in to an existing account because the emails match.
  handle_id="$(execution_id "$FLOW" displayName "Handle Existing Account")"
  create_id="$(execution_id "$FLOW" providerId idp-create-user-if-unique)"
  review_id="$(execution_id "$FLOW" providerId idp-review-profile)"
  if [ -z "$handle_id" ] || [ -z "$create_id" ] || [ -z "$review_id" ]; then
    echo "✗ flow '$FLOW' lacks an expected step (Handle Existing Account / Create User If" >&2
    echo "  Unique / Review Profile) – delete it in the console and re-run." >&2
    exit 1
  fi
  set_requirement "$FLOW" "$handle_id" DISABLED
  # REQUIRED (not ALTERNATIVE): only a required "Create User If Unique" shows the
  # "User with email … already exists" page; as an alternative it would fall
  # through to a generic error.
  set_requirement "$FLOW" "$create_id" REQUIRED
  # Ask for what a provider did not send (GitHub users without a last name, Microsoft
  # accounts without an email) – and only that. The stock flow may have had this step
  # switched off by configure-university-idp.sh before this copy was made.
  set_requirement "$FLOW" "$review_id" REQUIRED
  echo "  Handle Existing Account → DISABLED, Create User If Unique → REQUIRED, Review Profile → REQUIRED"
else
  FLOW="$SOCIAL_FIRST_LOGIN_FLOW"
  echo "→ Using the existing flow '$FLOW' as is (SOCIAL_FIRST_LOGIN_FLOW)"
fi

# ── 3. The identity providers ────────────────────────────────────────────────
idp_json() {  # $1 = provider alias
  PROVIDER="$1" FLOW="$FLOW" SOCIAL_PKCE="$SOCIAL_PKCE" \
  GOOGLE_CLIENT_ID="$GOOGLE_CLIENT_ID" GOOGLE_CLIENT_SECRET="$GOOGLE_CLIENT_SECRET" \
  GOOGLE_HOSTED_DOMAIN="$GOOGLE_HOSTED_DOMAIN" \
  MICROSOFT_CLIENT_ID="$MICROSOFT_CLIENT_ID" MICROSOFT_CLIENT_SECRET="$MICROSOFT_CLIENT_SECRET" \
  MICROSOFT_TENANT="$MICROSOFT_TENANT" \
  GITHUB_CLIENT_ID="$GITHUB_CLIENT_ID" GITHUB_CLIENT_SECRET="$GITHUB_CLIENT_SECRET" \
  GITHUB_BASE_URL="$GITHUB_BASE_URL" GITHUB_API_URL="$GITHUB_API_URL" \
  python3 - <<'PY'
import json, os

env = os.environ
p = env["PROVIDER"]
up = p.upper()

# Per provider: label, whether it vouches for the email address it returns, and its
# position on Keycloak's own login page (the university IdP has none and follows).
#  - Google signs in verified addresses; GitHub only lets a verified address be the
#    primary one (its /user/emails answer carries the flag, Keycloak does not read it).
#  - Microsoft's `mail` / userPrincipalName is whatever the tenant admin typed; an
#    Entra tenant of the attacker's own can claim anyone's address ("nOAuth"). Never
#    trusted: the account's email stays unverified.
meta = {
    "google":    {"name": "Google",    "trust_email": True,  "order": "1"},
    "microsoft": {"name": "Microsoft", "trust_email": False, "order": "2"},
    "github":    {"name": "GitHub",    "trust_email": True,  "order": "3"},
}[p]

cfg = {
    "clientId": env[f"{up}_CLIENT_ID"],
    "clientSecret": env[f"{up}_CLIENT_SECRET"],
    # Import name/email once, at the first sign-in. FORCE would rewrite them on every
    # sign-in, so signing in with a second linked provider could swap the email.
    "syncMode": "IMPORT",
    "guiOrder": meta["order"],
}
if env["SOCIAL_PKCE"].lower() in ("1", "true", "yes", "on"):
    cfg.update({"pkceEnabled": "true", "pkceMethod": "S256"})

if p == "google" and env["GOOGLE_HOSTED_DOMAIN"]:
    cfg["hostedDomain"] = env["GOOGLE_HOSTED_DOMAIN"]
if p == "microsoft":
    cfg["tenantId"] = env["MICROSOFT_TENANT"]
if p == "github":
    # MATE sends prompt=login on every sign-in and Keycloak forwards it. Google and
    # Microsoft accept it (checked against both authorize endpoints); GitHub documents
    # only select_account, so pin the one value it defines – the account picker.
    cfg["prompt"] = "select_account"
    if env["GITHUB_BASE_URL"]:
        cfg["baseUrl"] = env["GITHUB_BASE_URL"]
    if env["GITHUB_API_URL"]:
        cfg["apiUrl"] = env["GITHUB_API_URL"]

print(json.dumps({
    "alias": p,
    "displayName": meta["name"],
    "providerId": p,
    "enabled": True,
    "trustEmail": meta["trust_email"],
    "storeToken": False,
    "addReadTokenRoleOnCreate": False,
    "linkOnly": False,
    "hideOnLogin": False,
    "firstBrokerLoginFlowAlias": env["FLOW"],
    "config": cfg,
}))
PY
}

for p in "${PROVIDERS[@]}"; do
  if kc get "identity-provider/instances/$p" -r "$REALM" >/dev/null 2>&1; then
    echo "→ Updating identity provider '$p'"
    idp_json "$p" | kc_pipe update "identity-provider/instances/$p" -r "$REALM" -f - >/dev/null
  else
    echo "→ Creating identity provider '$p'"
    idp_json "$p" | kc_pipe create identity-provider/instances -r "$REALM" -f -
  fi
done

# ── 4. Redirect back to the profile page after "Connect" ─────────────────────
# Profile -> Sign-in methods sends the browser to Keycloak's broker link endpoint,
# which returns it to <WEB_URL>/profile; Keycloak only redirects to URIs the client
# lists. Existing entries are kept.
if [ -n "$WEB_URL" ]; then
  PROFILE_URL="${WEB_URL%/}/profile"
  web_client_id="$(kc get clients -r "$REALM" -q "clientId=$WEB_CLIENT_ID" --fields id --format csv --noquotes | tr -d '\r\n')"
  if [ -z "$web_client_id" ]; then
    echo "  ! client '$WEB_CLIENT_ID' not found – add $PROFILE_URL to its valid redirect URIs by hand." >&2
  else
    merged="$(kc get "clients/$web_client_id" -r "$REALM" --fields redirectUris \
      | PROFILE_URL="$PROFILE_URL" python3 -c "
import json, os, sys
uris = json.load(sys.stdin).get('redirectUris') or []
url = os.environ['PROFILE_URL']
if url not in uris:
    uris.append(url)
print(json.dumps(uris))")"
    echo "→ Valid redirect URIs of '$WEB_CLIENT_ID': $merged"
    kc update "clients/$web_client_id" -r "$REALM" -s "redirectUris=$merged" >/dev/null
  fi
else
  echo "  ! WEB_URL (or AUTH_URL) is not set – add <web base URL>/profile to the valid redirect URIs" >&2
  echo "    of client '$WEB_CLIENT_ID' by hand, or Profile -> Sign-in methods cannot connect accounts." >&2
fi

# ── Done ─────────────────────────────────────────────────────────────────────
echo
echo "✓ Done. Register these redirect (callback) URIs at the providers – the alias is the"
echo "  last-but-one segment and must not change:"
for p in "${PROVIDERS[@]}"; do
  echo "    $p: ${KC_PUBLIC_URL:-<keycloak public URL>}/realms/$REALM/broker/$p/endpoint"
done
configured="$(IFS=,; echo "${PROVIDERS[*]}")"
echo "  Then set KEYCLOAK_SOCIAL_IDPS=$configured on the web service and restart it."
