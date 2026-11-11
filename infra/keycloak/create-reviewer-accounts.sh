#!/usr/bin/env bash
#
# Anonymous reviewer accounts (e.g. a conference demo review) on a *running*
# Keycloak: local realm users reviewer1..reviewerN with strong generated
# passwords, no email address and no personal data. Use this instead of the
# admin console so the accounts are reproducible and can be expired.
#
# What `create` does (all idempotent – safe to re-run):
#   1. Creates the realm role `reviewer` if missing. The API reads it from the
#      token: a reviewer is never usage-tracked (USER_TRACKING_EXEMPT_ROLES,
#      default `reviewer`), and SANDBOXED_ROLES (default `reviewer`) keeps
#      reviewers inside their own account (no module upload, no custom
#      watched-folder path, no API tokens).
#   2. Creates or updates each user: enabled, no required actions, first/last
#      name "Reviewer" / "<n>" (needed by Keycloak's default user profile, so
#      no profile form appears on first login). Grants `reviewer`, removes
#      `admin` should anyone have granted it.
#      No email is set, unless the realm's user profile requires one: then a
#      non-routable placeholder `reviewerN@reviewers.invalid` (marked verified)
#      is set, so the login never stops at a form asking the reviewer for their
#      real address. Override with REVIEWER_EMAIL_MODE=none|placeholder.
#   3. Sets a generated password (not temporary) for a NEW user, or for an
#      existing user that has no entry in the credentials file yet. ROTATE=1
#      re-generates every password. Passwords go only into CREDENTIALS_FILE
#      (chmod 600, refused inside the repository) – never stdout, never argv.
#   4. Writes the sample-content manifest + copies the sample logs into
#      data/sample-content/reviewers/, so the API seeds each account with the
#      sample logs and an example dashboard at its next start (needs
#      SAMPLE_CONTENT_MANIFEST in the VM .env, printed at the end).
#
# Other commands:
#   status   list the accounts (enabled, id, roles) and the expiry date
#   disable  disable every account and end its sessions (reversible)
#   enable   re-enable every account
#   expire   `disable`, but only once today >= REVIEWER_EXPIRES (default
#            2026-12-15) – run daily by infra/systemd/mate-reviewer-expiry.timer
#   delete   delete the Keycloak accounts, their credentials-file entries and the
#            sample-content manifest (their MATE data stays until an admin
#            deletes the users under Admin → Users)
#
# Requirements: docker (Keycloak running in a container), python3 on the host.
# KEYCLOAK_ADMIN / KEYCLOAK_ADMIN_PASSWORD are read from the environment, else
# from the repository's .env (VM: ~/mate/.env).
#
# Usage (local compose):
#   ./infra/keycloak/create-reviewer-accounts.sh [create|status|disable|enable|expire|delete]
#
# Usage (uni VM – Keycloak lives under /auth there):
#   KC_SERVER=http://localhost:8080/auth ./infra/keycloak/create-reviewer-accounts.sh
#
set -euo pipefail
# Most kcadm calls are silenced; say which one failed instead of exiting mutely.
trap 'echo "✗ failed at line $LINENO: $BASH_COMMAND" >&2' ERR

CMD="${1:-create}"
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
MATE_DIR="$(cd "$SCRIPT_DIR/../.." && pwd)"

# ── Config (override via env) ────────────────────────────────────────────────
KC_CONTAINER="${KC_CONTAINER:-mate-keycloak}"
REALM="${REALM:-flows-funds}"
# Internal admin endpoint. In prod Keycloak serves under /auth – set
# KC_SERVER=http://localhost:8080/auth then (KC_HTTP_RELATIVE_PATH=/auth).
KC_SERVER="${KC_SERVER:-http://localhost:8080}"

env_from_dotenv() {  # KEY -> value from $MATE_DIR/.env ('' if absent); never sourced
  [ -f "$MATE_DIR/.env" ] || return 0
  sed -n "s/^$1=//p" "$MATE_DIR/.env" | tail -n 1 | sed -e 's/^"\(.*\)"$/\1/' -e "s/^'\(.*\)'$/\1/"
}
KC_ADMIN="${KEYCLOAK_ADMIN:-$(env_from_dotenv KEYCLOAK_ADMIN)}"
KC_ADMIN="${KC_ADMIN:-admin}"
KC_ADMIN_PASSWORD="${KEYCLOAK_ADMIN_PASSWORD:-$(env_from_dotenv KEYCLOAK_ADMIN_PASSWORD)}"
KC_ADMIN_PASSWORD="${KC_ADMIN_PASSWORD:-admin}"

REVIEWER_PREFIX="${REVIEWER_PREFIX:-reviewer}"
REVIEWER_COUNT="${REVIEWER_COUNT:-5}"
REVIEWER_ROLE="${REVIEWER_ROLE:-reviewer}"
# Last day of access is the day before; `expire` disables from this date on.
REVIEWER_EXPIRES="${REVIEWER_EXPIRES:-2026-12-15}"
REVIEWER_EMAIL_MODE="${REVIEWER_EMAIL_MODE:-auto}"   # auto | none | placeholder
ROTATE="${ROTATE:-0}"
# Outside the repository on purpose (the VM checkout is ~/mate).
CREDENTIALS_FILE="${CREDENTIALS_FILE:-$HOME/mate-secrets/reviewer-credentials.txt}"
PUBLIC_URL="${PUBLIC_URL:-https://mate.uni-muenster.de}"
# Sample content: host folder under the api's ./data bind mount (/app/data).
# One folder per account set (prefix `reviewer` -> sample-content/reviewers).
SAMPLE_REL="sample-content/${REVIEWER_PREFIX}s"
SAMPLE_DIR="$MATE_DIR/data/$SAMPLE_REL"
SAMPLE_SRC="$MATE_DIR/simulated_data/input/autonomous"

KCADM=/opt/keycloak/bin/kcadm.sh
# kc: run kcadm in the container, output to host stdout.
kc()      { docker exec     "$KC_CONTAINER" "$KCADM" "$@"; }
# kc_pipe: same, but forwards host stdin (for `-f -` reads).
kc_pipe() { docker exec -i  "$KC_CONTAINER" "$KCADM" "$@"; }

case "$CMD" in
  create|status|disable|enable|expire|delete) ;;
  *) echo "✗ unknown command '$CMD' (create|status|disable|enable|expire|delete)." >&2; exit 1 ;;
esac
# expire before the date: done without touching Keycloak (the daily timer run).
if [ "$CMD" = "expire" ] && [[ "$(date +%F)" < "$REVIEWER_EXPIRES" ]]; then
  echo "✓ Reviewer accounts stay active until $REVIEWER_EXPIRES (today $(date +%F))."
  exit 0
fi
if ! [[ "$REVIEWER_COUNT" =~ ^[1-9][0-9]?$ ]]; then
  echo "✗ REVIEWER_COUNT must be 1-99." >&2
  exit 1
fi
if ! docker ps --format '{{.Names}}' | grep -qx "$KC_CONTAINER"; then
  echo "✗ container '$KC_CONTAINER' is not running (set KC_CONTAINER)." >&2
  exit 1
fi
if ! command -v python3 >/dev/null 2>&1; then
  echo "✗ python3 is required on the host." >&2
  exit 1
fi

usernames() { local i; for ((i = 1; i <= REVIEWER_COUNT; i++)); do echo "${REVIEWER_PREFIX}${i}"; done; }

# The browser flow's Identity Provider Redirector. With a default provider set
# (configure-university-idp.sh with SET_DEFAULT_PROVIDER=1, or by hand), Keycloak
# sends EVERY sign-in to that provider, hint or not, so local accounts such as the
# reviewers can never reach the password form. Prints "<config id> <provider>"
# when one is set, nothing otherwise.
redirector_default() {
  local cfg
  cfg="$(kc get authentication/flows/browser/executions -r "$REALM" 2>/dev/null | python3 -c "
import json, sys
for e in json.load(sys.stdin):
    if e.get('providerId') == 'identity-provider-redirector':
        print(e.get('authenticationConfig') or ''); break")"
  [ -n "$cfg" ] || return 0
  kc get "authentication/config/$cfg" -r "$REALM" 2>/dev/null | C="$cfg" python3 -c "
import json, os, sys
p = (json.load(sys.stdin).get('config') or {}).get('defaultProvider', '')
if p: print(os.environ['C'], p)"
}

check_redirector() {  # warn (or, with CLEAR_IDP_REDIRECTOR=1, fix) a forced IdP redirect
  local found cfg provider
  found="$(redirector_default)"
  [ -n "$found" ] || { echo "  login: Keycloak's own form is reachable (no forced IdP redirect)"; return 0; }
  cfg="${found%% *}"; provider="${found#* }"
  if [ "${CLEAR_IDP_REDIRECTOR:-0}" = "1" ]; then
    kc delete "authentication/config/$cfg" -r "$REALM" >/dev/null
    echo "  login: removed the browser flow's forced redirect to '$provider'. University users"
    echo "         still go straight to it: the web app sends KEYCLOAK_IDP_HINT on its default button."
  else
    echo "  ! login: the browser flow's Identity Provider Redirector forces EVERY sign-in to" >&2
    echo "    '$provider', so reviewers cannot reach the password form. Re-run with" >&2
    echo "    CLEAR_IDP_REDIRECTOR=1 to delete that config (normal users stay on SSO via" >&2
    echo "    KEYCLOAK_IDP_HINT), or delete it under Authentication → Flows → browser →" >&2
    echo "    Identity Provider Redirector → settings." >&2
    REDIRECTOR_BLOCKS=1
  fi
}
REDIRECTOR_BLOCKS=0

placeholder_email() { echo "$1@reviewers.invalid"; }

user_id_of() {  # name -> internal id ('' if absent); exact match only
  # By username, else by the placeholder email: with the realm's "Email as
  # username" on, Keycloak stores the email as the username.
  local id
  id="$(kc get users -r "$REALM" -q username="$1" -q exact=true --fields id,username 2>/dev/null \
    | U="$1" python3 -c "import sys,json,os;rows=[r for r in json.load(sys.stdin) if r.get('username')==os.environ['U']];print(rows[0]['id'] if rows else '')")"
  if [ -z "$id" ]; then
    id="$(kc get users -r "$REALM" -q email="$(placeholder_email "$1")" -q exact=true --fields id,email 2>/dev/null \
      | E="$(placeholder_email "$1")" python3 -c "import sys,json,os;rows=[r for r in json.load(sys.stdin) if (r.get('email') or '').lower()==os.environ['E']];print(rows[0]['id'] if rows else '')")"
  fi
  echo "$id"
}

login_name_of() {  # internal id -> the username Keycloak stored (what goes into the login form)
  kc get "users/$1" -r "$REALM" --fields username 2>/dev/null \
    | python3 -c "import sys,json;print(json.load(sys.stdin)['username'])"
}

# ── 1. Authenticate kcadm ────────────────────────────────────────────────────
echo "→ Authenticating kcadm against $KC_SERVER (realm master)"
kc config credentials --server "$KC_SERVER" --realm master \
  --user "$KC_ADMIN" --password "$KC_ADMIN_PASSWORD" >/dev/null 2>&1 \
  || { echo "✗ kcadm login failed (check KC_SERVER / KEYCLOAK_ADMIN_PASSWORD)." >&2; exit 1; }

# ── expire: the date is reached (checked above) ─────────────────────────────
if [ "$CMD" = "expire" ]; then
  echo "→ $REVIEWER_EXPIRES reached – disabling reviewer accounts"
  CMD=disable
fi

# ── status / disable / enable / delete ──────────────────────────────────────
if [ "$CMD" != "create" ]; then
  while read -r name; do
    uid="$(user_id_of "$name")"
    if [ -z "$uid" ]; then
      echo "  $name: (absent)"
      continue
    fi
    case "$CMD" in
      status)
        enabled="$(kc get "users/$uid" -r "$REALM" --fields enabled 2>/dev/null \
          | python3 -c "import sys,json;print(json.load(sys.stdin).get('enabled'))")"
        roles="$(kc get "users/$uid/role-mappings/realm" -r "$REALM" 2>/dev/null \
          | python3 -c "import sys,json;print(','.join(sorted(r['name'] for r in json.load(sys.stdin))))")"
        echo "  $name: login=$(login_name_of "$uid") enabled=$enabled id=$uid roles=$roles"
        ;;
      disable)
        kc update "users/$uid" -r "$REALM" -s enabled=false >/dev/null 2>&1
        # End live sessions so a signed-in reviewer is out at the next token refresh.
        kc create "users/$uid/logout" -r "$REALM" >/dev/null 2>&1 || true
        echo "  $name: disabled"
        ;;
      enable)
        kc update "users/$uid" -r "$REALM" -s enabled=true >/dev/null 2>&1
        echo "  $name: enabled"
        ;;
      delete)
        kc delete "users/$uid" -r "$REALM"
        echo "  $name: deleted from Keycloak (id $uid – delete its MATE data under Admin → Users)"
        ;;
    esac
  done < <(usernames)
  if [ "$CMD" = "delete" ]; then
    # Forget the passwords, and stop the API from re-seeding the deleted accounts.
    cred_file="$(python3 -c "import os,sys;print(os.path.abspath(os.path.expanduser(sys.argv[1])))" "$CREDENTIALS_FILE")"
    if [ -f "$cred_file" ]; then
      prefix_re="^${REVIEWER_PREFIX}[0-9]+ "
      grep -Ev "$prefix_re" "$cred_file" >"$cred_file.tmp" || true
      mv "$cred_file.tmp" "$cred_file"
      chmod 600 "$cred_file"
      echo "  removed the passwords from $cred_file"
    fi
    if [ -f "$SAMPLE_DIR/manifest.json" ]; then
      rm -f "$SAMPLE_DIR/manifest.json"
      echo "  removed $SAMPLE_DIR/manifest.json (unset SAMPLE_CONTENT_MANIFEST too)"
    fi
  fi
  if [ "$CMD" = "status" ]; then
    echo "  expiry: disabled from $REVIEWER_EXPIRES (expire command / systemd timer)"
    CLEAR_IDP_REDIRECTOR=0 check_redirector
  fi
  echo "✓ Done."
  exit 0
fi

# ── create: preconditions ────────────────────────────────────────────────────
cred_abs="$(python3 -c "import os,sys;print(os.path.abspath(os.path.expanduser(sys.argv[1])))" "$CREDENTIALS_FILE")"
case "$cred_abs/" in
  "$MATE_DIR"/*)
    echo "✗ CREDENTIALS_FILE ($cred_abs) is inside the repository – choose a path outside $MATE_DIR." >&2
    exit 1 ;;
esac
umask 077
mkdir -p "$(dirname "$cred_abs")"
touch "$cred_abs"
chmod 600 "$cred_abs"
if [ ! -s "$cred_abs" ]; then
  printf '# MATE reviewer accounts (%s). Login: %s/login/start?local=1\n# Disabled from %s. Format: <username> <password>\n' \
    "$REALM" "$PUBLIC_URL" "$REVIEWER_EXPIRES" >"$cred_abs"
fi

# ── 2. Realm role ────────────────────────────────────────────────────────────
if ! kc get "roles/$REVIEWER_ROLE" -r "$REALM" >/dev/null 2>&1; then
  echo "→ Creating realm role '$REVIEWER_ROLE'"
  kc create roles -r "$REALM" -s "name=$REVIEWER_ROLE" \
    -s 'description=Anonymous reviewer account: never usage-tracked, sandboxed' >/dev/null 2>&1
fi

# ── 3. Email requirement of the realm's user profile ────────────────────────
use_placeholder_email=0
case "$REVIEWER_EMAIL_MODE" in
  placeholder) use_placeholder_email=1 ;;
  none) ;;
  auto)
    if kc get users/profile -r "$REALM" 2>/dev/null | python3 -c "
import sys, json
cfg = json.load(sys.stdin)
email = next((a for a in cfg.get('attributes', []) if a.get('name') == 'email'), None)
req = (email or {}).get('required')
# Required for end users when 'required' is present and either has no role
# restriction or names the 'user' role.
raise SystemExit(0 if req is not None and (not req.get('roles') or 'user' in req.get('roles', [])) else 1)
"; then
      use_placeholder_email=1
      echo "  ! the realm's user profile requires an email – setting a non-routable"
      echo "    placeholder (<user>@reviewers.invalid) so no form asks reviewers for theirs"
    fi
    ;;
  *) echo "✗ REVIEWER_EMAIL_MODE must be auto, none or placeholder." >&2; exit 1 ;;
esac
# "Email as username" makes Keycloak replace the username with the email (and
# reject a user without one): the login name is then the placeholder address.
EMAIL_AS_USERNAME="$(kc get "realms/$REALM" --fields registrationEmailAsUsername 2>/dev/null \
  | python3 -c "import sys,json;print('1' if json.load(sys.stdin).get('registrationEmailAsUsername') else '0')")"
if [ "$EMAIL_AS_USERNAME" = "1" ]; then
  use_placeholder_email=1
  echo "  ! the realm has 'Email as username' on – reviewers log in with"
  echo "    <user>@reviewers.invalid (printed below and in the credentials file)"
fi

gen_password() {  # 4x5 groups of letters/digits, joined by '-': ~119 bits, every class present
  python3 - <<'PY'
import secrets, string
alphabet = string.ascii_letters + string.digits
while True:
    groups = ["".join(secrets.choice(alphabet) for _ in range(5)) for _ in range(4)]
    pw = "-".join(groups)
    if any(c.islower() for c in pw) and any(c.isupper() for c in pw) and any(c.isdigit() for c in pw):
        print(pw)
        break
PY
}

stored_password() {  # username -> password from the credentials file ('' if absent)
  awk -v u="$1" '$1 == u { p = $2 } END { print p }' "$cred_abs"
}

store_password() {  # name password login -> credentials file (replaces the name's line)
  U="$1" P="$2" L="$3" F="$cred_abs" python3 - <<'PY'
import os
path, user, pw, login = os.environ["F"], os.environ["U"], os.environ["P"], os.environ["L"]
lines = [l for l in open(path, encoding="utf-8").read().splitlines() if l.split()[:1] != [user]]
lines.append(f"{user} {pw}" + (f"   login: {login}" if login != user else ""))
with open(path, "w", encoding="utf-8") as fh:
    fh.write("\n".join(lines) + "\n")
PY
}

user_json() {  # username n -> user representation
  U="$1" N="$2" E="$use_placeholder_email" python3 - <<'PY'
import json, os
rep = {
    "username": os.environ["U"],
    "enabled": True,
    "firstName": "Reviewer",
    "lastName": os.environ["N"],
    "requiredActions": [],
}
if os.environ["E"] == "1":
    rep["email"] = f"{os.environ['U']}@reviewers.invalid"  # = placeholder_email
    rep["emailVerified"] = True
print(json.dumps(rep))
PY
}

echo "→ Checking that local accounts can reach Keycloak's login form"
check_redirector

# ── 4. Users ─────────────────────────────────────────────────────────────────
declare -A IDS=()
# kcadm errors of the password call (its stdin carries the password, so it is
# not wrapped in a command substitution like the others).
SCRATCH_ERR="$(mktemp)"
trap 'rm -f "$SCRATCH_ERR"' EXIT
n=0
while read -r name; do
  n=$((n + 1))
  uid="$(user_id_of "$name")"
  created=0
  if [ -n "$uid" ]; then
    echo "→ Updating user '$name' ($uid)"
    out="$(user_json "$name" "$n" | kc_pipe update "users/$uid" -r "$REALM" -f - 2>&1)" \
      || { echo "✗ updating '$name' failed – Keycloak said: $out" >&2; exit 1; }
  else
    echo "→ Creating user '$name'"
    out="$(user_json "$name" "$n" | kc_pipe create users -r "$REALM" -f - 2>&1)" \
      || { echo "✗ creating '$name' failed – Keycloak said: $out" >&2; exit 1; }
    uid="$(user_id_of "$name")"
    created=1
  fi
  if [ -z "$uid" ]; then
    echo "✗ user '$name' not found after create – aborting. kcadm said: ${out:-(nothing)}" >&2
    exit 1
  fi
  IDS[$name]="$uid"

  # A new account always gets a fresh password (a stale line in the file, e.g.
  # after `delete` or a Keycloak reset, must not leave it without one).
  if [ "$created" = "1" ] || [ "$ROTATE" = "1" ] || [ -z "$(stored_password "$name")" ]; then
    pw="$(gen_password)"
    # Through stdin, so the password never shows up in a process list.
    PW="$pw" python3 -c "import json,os;print(json.dumps({'type':'password','value':os.environ['PW'],'temporary':False}))" \
      | kc_pipe update "users/$uid/reset-password" -r "$REALM" -f - -n >/dev/null 2>"$SCRATCH_ERR" \
      || { echo "✗ setting the password of '$name' failed – Keycloak said: $(cat "$SCRATCH_ERR")" >&2; exit 1; }
    store_password "$name" "$pw" "$(login_name_of "$uid")"
    echo "  password set (stored in $cred_abs)"
  fi

  roles="$(kc get "users/$uid/role-mappings/realm" -r "$REALM" 2>/dev/null)"
  if ! R="$REVIEWER_ROLE" python3 -c "import sys,json,os;raise SystemExit(0 if any(r['name']==os.environ['R'] for r in json.load(sys.stdin)) else 1)" <<<"$roles"; then
    kc add-roles -r "$REALM" --uid "$uid" --rolename "$REVIEWER_ROLE" >/dev/null
    echo "  granted role '$REVIEWER_ROLE'"
  fi
  if python3 -c "import sys,json;raise SystemExit(0 if any(r['name']=='admin' for r in json.load(sys.stdin)) else 1)" <<<"$roles"; then
    kc remove-roles -r "$REALM" --uid "$uid" --rolename admin >/dev/null
    echo "  ! removed role 'admin'"
  fi
done < <(usernames)

# ── 5. Sample content manifest ───────────────────────────────────────────────
echo "→ Writing sample content to $SAMPLE_DIR"
# World-readable on purpose: the api container must read it, and it holds only
# public sample logs + account ids (no secrets). The credentials file above was
# created under umask 077.
umask 022
if ! mkdir -p "$SAMPLE_DIR" 2>/dev/null || [ ! -w "$SAMPLE_DIR" ]; then
  # data/ is written by the api container (root): hand this one folder to the
  # operator instead of running the whole script as root (which would look for
  # the credentials file in /root and re-generate every password).
  echo "✗ $SAMPLE_DIR is not writable for $(id -un). The accounts are fine; run once:" >&2
  echo "    sudo mkdir -p $SAMPLE_DIR && sudo chown -R $(id -un): $(dirname "$SAMPLE_DIR")" >&2
  echo "  then re-run this script (passwords are kept)." >&2
  exit 1
fi
chmod 755 "$(dirname "$SAMPLE_DIR")" "$SAMPLE_DIR"
for f in test_preprocessed.csv train_preprocessed.csv simulated_log_0.csv; do
  if [ ! -f "$SAMPLE_SRC/$f" ]; then
    echo "✗ sample log $SAMPLE_SRC/$f is missing." >&2
    exit 1
  fi
done
install -m 644 "$SAMPLE_SRC/test_preprocessed.csv" "$SAMPLE_DIR/helpdesk-sample-a.csv"
install -m 644 "$SAMPLE_SRC/train_preprocessed.csv" "$SAMPLE_DIR/helpdesk-sample-b.csv"
install -m 644 "$SAMPLE_SRC/simulated_log_0.csv" "$SAMPLE_DIR/helpdesk-simulated.csv"

users_tsv=""
for name in $(usernames); do users_tsv+="$name ${IDS[$name]}"$'\n'; done
USERS="$users_tsv" OUT="$SAMPLE_DIR/manifest.json" python3 - <<'PY'
import json, os
users = [
    {"username": u, "id": i}
    for u, i in (line.split() for line in os.environ["USERS"].splitlines() if line.strip())
]
mapping = {
    "case_id": "case_id",
    "activity": "activity_name",
    "timestamp": "start_timestamp",
    "end_timestamp": "end_timestamp",
    "resource": "resource",
}
manifest = {
    "tracking_exempt": True,
    "users": users,
    "logs": [
        {"file": "helpdesk-sample-a.csv", "name": "Helpdesk ticketing (sample A)", "csv_mapping": mapping},
        {"file": "helpdesk-sample-b.csv", "name": "Helpdesk ticketing (sample B)", "csv_mapping": mapping},
        {"file": "helpdesk-simulated.csv", "name": "Helpdesk ticketing (simulated)", "csv_mapping": mapping},
    ],
    "dashboard": {
        "name": "Example dashboard",
        "template_id": "process-overview",
        "log": 0,
        "description": "Process overview of the Helpdesk sample log.",
    },
}
with open(os.environ["OUT"], "w", encoding="utf-8") as fh:
    json.dump(manifest, fh, indent=2)
    fh.write("\n")
PY
chmod 644 "$SAMPLE_DIR/manifest.json"

echo
logins=""
for name in $(usernames); do logins+="$(login_name_of "${IDS[$name]}") "; done
echo "✓ Done. ${REVIEWER_COUNT} reviewer accounts ready. Log in as: ${logins% }"
echo "  Passwords:  $cred_abs (mode 600, outside the repository)"
echo "  Login URL:  $PUBLIC_URL/login/start?local=1"
if [ "$REDIRECTOR_BLOCKS" = "1" ]; then
  echo "  ! Login is BLOCKED by the forced IdP redirect (see above) – fix that first." >&2
fi
echo "  Expiry:     run 'sudo ./infra/install-reviewer-expiry.sh' once (disables on $REVIEWER_EXPIRES)"
echo
echo "  Add to the VM .env (once), then recreate the api so it seeds the sample content:"
echo "    SAMPLE_CONTENT_MANIFEST=/app/data/$SAMPLE_REL/manifest.json"
echo "    docker compose -f docker-compose.yml -f docker-compose.prod.yml up -d api"
echo "  ('restart' does NOT re-read .env)"
