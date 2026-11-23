#!/usr/bin/env bash
#
# Reset the anonymous reviewer accounts (docs/REVIEWER_ACCESS.md) to the state a
# reviewer sees on first login: the seeded sample logs + example dashboard, every
# module result precomputed, no onboarding/tour state, no usage events.
#
# Seeding (apps/api/src/mate/api/services/sample_content.py) runs only at API
# start and only for an account without the `sample_content.seeded` setting. It
# also reuses a log or dashboard that already has the seeded name. So `reset`:
#   1. checks the accounts against Keycloak (same id, role `reviewer`, no `admin`)
#      and that no other account has a running job (the API restart would fail it),
#   2. backs up the app DB (pg_dump), the Keycloak DB and the reviewers' data folders,
#   3. sets `warm_modules` in the sample-content manifest to WARM_MODULES, so the
#      seeder precomputes every module (not only discovery/complexity),
#   4. stops the api, deletes the reviewers' rows (logs, dashboards, jobs, module
#      runs/installs/configs/layouts, analytics, settings incl. onboarding/tour,
#      keeping only the analytics.exempt + auth.sandboxed safety flags and the
#      users row), their data/users/<id> + data/staging/<id> folders and their
#      web sessions, ends their Keycloak sessions and clears a brute-force lockout,
#   5. starts the api (which re-seeds them), waits for seeding + precompute, verifies.
#
# It never touches a password, username, role or the enabled flag in Keycloak,
# never prints a password, and only touches rows/folders of the ids in the
# manifest. Nothing of another account is deleted; the api restart is the only
# instance-wide effect (a few seconds of downtime).
#
# Usage (on the VM, from ~/mate; KC_SERVER as for create-reviewer-accounts.sh):
#   KC_SERVER=http://localhost:8080/auth ./infra/keycloak/reset-reviewer-accounts.sh --dry-run
#   ./infra/keycloak/reset-reviewer-accounts.sh backup     # backup only
#   KC_SERVER=http://localhost:8080/auth ./infra/keycloak/reset-reviewer-accounts.sh reset
#   KC_SERVER=http://localhost:8080/auth ./infra/keycloak/reset-reviewer-accounts.sh verify
#
#   --dry-run / report  what testing changed per account (no writes)
#   backup              pg_dump + data folders into BACKUP_DIR/reviewer-reset-<stamp>/
#   reset [--yes]       backup, then reset as above (asks for confirmation without --yes)
#   verify              check every account against the seeded state (exit 1 on a difference)
#
# Env: KC_CONTAINER, REALM, KC_SERVER, KEYCLOAK_ADMIN(_PASSWORD) as in
# create-reviewer-accounts.sh; BACKUP_DIR (default ~/backups); WARM_MODULES;
# REQUIRED_MODULES (verify: must be ready on every log, default
# "cv4cdd performance_over_time"); WAIT_TIMEOUT_SECONDS (default 3 h);
# ALLOW_BUSY=1 to restart the api although other accounts have queued/running jobs.
#
set -euo pipefail
trap 'echo "✗ failed at line $LINENO: $BASH_COMMAND" >&2' ERR

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
MATE_DIR="$(cd "$SCRIPT_DIR/../.." && pwd)"
HELPER="$SCRIPT_DIR/reviewer_reset.py"

CMD="${1:-}"
YES=0
for arg in "${@:2}"; do
  case "$arg" in
    --yes) YES=1 ;;
    *) echo "✗ unknown option '$arg'." >&2; exit 1 ;;
  esac
done
case "$CMD" in
  --dry-run|dry-run|report) CMD=report ;;
  backup|reset|verify) ;;
  *) sed -n '2,45p' "$0" | sed 's/^# \{0,1\}//'; exit 1 ;;
esac

# ── Config (override via env) ────────────────────────────────────────────────
KC_CONTAINER="${KC_CONTAINER:-mate-keycloak}"
API_CONTAINER="${API_CONTAINER:-mate-api}"
DB_CONTAINER="${DB_CONTAINER:-mate-app-db}"
KC_DB_CONTAINER="${KC_DB_CONTAINER:-mate-keycloak-db}"
WEB_CONTAINER="${WEB_CONTAINER:-mate-web}"
REALM="${REALM:-flows-funds}"
KC_SERVER="${KC_SERVER:-http://localhost:8080}"
REVIEWER_ROLE="${REVIEWER_ROLE:-reviewer}"
BACKUP_DIR="${BACKUP_DIR:-$HOME/backups}"
# Every bundled module with a precompute (an @on_event("log.imported") @job),
# case-centric. Modules without one (performance, conformance, …) compute on open.
WARM_MODULES="${WARM_MODULES:-discovery complexity complexity_over_time complexity_v2 complexity_v2_over_time log_evolution cv4cdd performance_over_time performance_java}"
export REQUIRED_MODULES="${REQUIRED_MODULES:-cv4cdd performance_over_time}"
DC=(docker compose -f "$MATE_DIR/docker-compose.yml" -f "$MATE_DIR/docker-compose.prod.yml" --project-directory "$MATE_DIR")

env_from_dotenv() {  # KEY -> value from $MATE_DIR/.env ('' if absent); never sourced
  [ -f "$MATE_DIR/.env" ] || return 0
  sed -n "s/^$1=//p" "$MATE_DIR/.env" | tail -n 1 | sed -e 's/^"\(.*\)"$/\1/' -e "s/^'\(.*\)'$/\1/"
}
KC_ADMIN="${KEYCLOAK_ADMIN:-$(env_from_dotenv KEYCLOAK_ADMIN)}"
KC_ADMIN="${KC_ADMIN:-admin}"
KC_ADMIN_PASSWORD="${KEYCLOAK_ADMIN_PASSWORD:-$(env_from_dotenv KEYCLOAK_ADMIN_PASSWORD)}"
KC_ADMIN_PASSWORD="${KC_ADMIN_PASSWORD:-admin}"

kc() { docker exec "$KC_CONTAINER" /opt/keycloak/bin/kcadm.sh "$@"; }
running() { docker ps --format '{{.Names}}' | grep -x "$1" >/dev/null; }  # no -q: an early exit is a SIGPIPE under pipefail
py() { python3 -I "$HELPER" "$@"; }

# Run a command with the api's /app/data: inside the running api, or (api
# stopped) in a throw-away container of the same image with the same mount.
DATA_HOST=""
API_IMAGE=""
in_api() {
  if running "$API_CONTAINER"; then
    docker exec -i "$API_CONTAINER" "$@"
  else
    docker run --rm -i --network none --entrypoint "" -v "$DATA_HOST:/app/data" "$API_IMAGE" "$@"
  fi
}

# ── Preflight (read-only) ────────────────────────────────────────────────────
command -v python3 >/dev/null || { echo "✗ python3 is required on the host." >&2; exit 1; }
for c in "$DB_CONTAINER" "$API_CONTAINER" "$KC_CONTAINER"; do
  running "$c" || { echo "✗ container '$c' is not running." >&2; exit 1; }
done
DATA_HOST="$(docker inspect -f '{{range .Mounts}}{{if eq .Destination "/app/data"}}{{.Source}}{{end}}{{end}}' "$API_CONTAINER")"
API_IMAGE="$(docker inspect -f '{{.Image}}' "$API_CONTAINER")"
[ -n "$DATA_HOST" ] || { echo "✗ $API_CONTAINER has no /app/data bind mount." >&2; exit 1; }
MANIFEST_IN="$(docker exec "$API_CONTAINER" printenv SAMPLE_CONTENT_MANIFEST || true)"
case "$MANIFEST_IN" in
  /app/data/*) MANIFEST="$DATA_HOST/${MANIFEST_IN#/app/data/}" ;;
  *) echo "✗ SAMPLE_CONTENT_MANIFEST in $API_CONTAINER is '${MANIFEST_IN:-unset}', not under /app/data." >&2; exit 1 ;;
esac
[ -f "$MANIFEST" ] || { echo "✗ manifest $MANIFEST is missing (run create-reviewer-accounts.sh)." >&2; exit 1; }
STORAGE_MODE="$(docker exec "$API_CONTAINER" printenv STORAGE_MODE || true)"
if [ "${STORAGE_MODE:-local}" != "local" ]; then
  echo "✗ STORAGE_MODE=$STORAGE_MODE: this script only removes local copies, not bucket objects." >&2
  exit 1
fi

mapfile -t ACCOUNTS < <(py ids "$MANIFEST")   # "<username> <id>", ids already UUID-checked
IDS=()
for line in "${ACCOUNTS[@]}"; do IDS+=("${line#* }"); done

echo "→ Accounts from $MANIFEST: ${#IDS[@]}"
echo "→ Checking them against Keycloak ($KC_SERVER, realm $REALM)"
kc config credentials --server "$KC_SERVER" --realm master \
  --user "$KC_ADMIN" --password "$KC_ADMIN_PASSWORD" >/dev/null 2>&1 \
  || { echo "✗ kcadm login failed (check KC_SERVER / KEYCLOAK_ADMIN_PASSWORD)." >&2; exit 1; }
for line in "${ACCOUNTS[@]}"; do
  name="${line%% *}"; uid="${line#* }"
  # Only reviewer accounts: the id must exist and hold `reviewer` and not `admin`.
  roles="$(kc get "users/$uid/role-mappings/realm" -r "$REALM" 2>/dev/null \
    | python3 -c "import sys,json;print(' '.join(sorted(r['name'] for r in json.load(sys.stdin))))")" \
    || { echo "✗ $name ($uid) is not a Keycloak user in $REALM – refusing." >&2; exit 1; }
  case " $roles " in *" $REVIEWER_ROLE "*) ;; *) echo "✗ $name lacks role '$REVIEWER_ROLE' – refusing." >&2; exit 1 ;; esac
  case " $roles " in *" admin "*) echo "✗ $name holds 'admin' – refusing." >&2; exit 1 ;; esac
done

kc_state() {  # id -> "sessions=N lockout=…"
  local sessions bf
  sessions="$(kc get "users/$1/sessions" -r "$REALM" 2>/dev/null | python3 -c "import sys,json;print(len(json.load(sys.stdin)))")"
  bf="$(kc get "attack-detection/brute-force/users/$1" -r "$REALM" 2>/dev/null \
    | python3 -c "import sys,json;d=json.load(sys.stdin);print(('LOCKED' if d.get('disabled') else 'none')+' failures='+str(d.get('numFailures',0)))" || echo "n/a")"
  echo "sessions=$sessions lockout=$bf"
}

disk_report() {  # per account: size and log folders on disk
  in_api sh -c '
    for id in "$@"; do
      d=/app/data/users/$id; s=/app/data/staging/$id
      if [ -d "$d" ]; then
        printf "  %s  %s  logs on disk: %s  results: %s\n" "$id" "$(du -sh "$d" | cut -f1)" \
          "$(ls "$d/event_logs" 2>/dev/null | wc -l)" "$(ls "$d/module_results" 2>/dev/null | wc -l)"
        [ -d "$d/watched" ] && echo "    ! watched folder present"
      else
        echo "  $id  (no data folder)"
      fi
      [ -d "$s" ] && echo "    ! staging uploads: $(ls "$s" | wc -l)"
    done; true' sh "${IDS[@]}"
}

web_sessions() {  # count | purge  – Auth.js session files whose token.sub is a reviewer id
  local dir
  dir="$(docker inspect -f '{{range .Mounts}}{{if eq .Destination "/app/sessions"}}{{.Source}}{{end}}{{end}}' "$WEB_CONTAINER" 2>/dev/null || true)"
  case "$dir" in
    "$DATA_HOST"/*) ;;
    *) echo "n/a (no session store under data/)"; return 0 ;;
  esac
  in_api python3 -I - "$1" "/app/data/${dir#"$DATA_HOST"/}" "${IDS[@]}" <<'PY'
import json, sys
from pathlib import Path
mode, root, ids = sys.argv[1], Path(sys.argv[2]), set(sys.argv[3:])
hits = 0
for f in root.glob("*.json") if root.is_dir() else []:
    try:
        sub = (json.loads(f.read_text()).get("token") or {}).get("sub")
    except Exception:
        continue
    if sub in ids:
        hits += 1
        if mode == "purge":
            f.unlink()
print(f"{hits} {'removed' if mode == 'purge' else 'present'}")
PY
}

cv4cdd_model_ok() {
  in_api sh -c 'ls /app/modules/cv4cdd/model/*/saved_model.pb >/dev/null 2>&1'
}

# ── report ───────────────────────────────────────────────────────────────────
do_report() {
  echo "→ What differs from the seeded state (no writes)"
  py report "$MANIFEST"
  echo
  echo "→ Keycloak (password, username, roles, enabled are never changed)"
  for line in "${ACCOUNTS[@]}"; do echo "  ${line%% *}: $(kc_state "${line#* }")"; done
  echo "→ Data folders ($DATA_HOST)"
  disk_report
  echo "→ Web sessions of the reviewers: $(web_sessions count)"
  echo "→ Seeder warm-up: manifest has [$(python3 -c "import json,sys;print(' '.join(json.load(open(sys.argv[1])).get('warm_modules') or []))" "$MANIFEST")]; reset sets [$WARM_MODULES]"
  case " $WARM_MODULES " in
    *" cv4cdd "*) cv4cdd_model_ok && echo "  cv4cdd model: present" || echo "  ! cv4cdd model: MISSING under modules/cv4cdd/model – cv4cdd would fail" ;;
  esac
  echo "→ Other accounts' queued/running jobs (an api restart fails running ones): $(py busy "$MANIFEST")"
}

# ── backup ───────────────────────────────────────────────────────────────────
BACKUP=""
do_backup() {
  BACKUP="$BACKUP_DIR/reviewer-reset-$(date +%F-%H%M%S)"
  ( umask 077; mkdir -p "$BACKUP" )
  echo "→ Backup to $BACKUP"
  docker exec "$DB_CONTAINER" pg_dump -U mate mate | gzip >"$BACKUP/app-db.sql.gz"
  [ "$(stat -c %s "$BACKUP/app-db.sql.gz")" -gt 1024 ] || { echo "✗ app-db dump is empty." >&2; exit 1; }
  if running "$KC_DB_CONTAINER"; then
    docker exec "$KC_DB_CONTAINER" pg_dump -U keycloak keycloak | gzip >"$BACKUP/keycloak-db.sql.gz"
  fi
  in_api sh -c 'cd /app/data && set -- $(for id in "$@"; do for p in users/$id staging/$id; do [ -e "$p" ] && echo "$p"; done; done)
                [ $# -gt 0 ] && exec tar czf - "$@"; tar czf - --files-from /dev/null' sh "${IDS[@]}" \
    >"$BACKUP/reviewer-data.tgz"
  tar tzf "$BACKUP/reviewer-data.tgz" >/dev/null
  cp "$MANIFEST" "$BACKUP/manifest.json"
  printf '%s\n' "${ACCOUNTS[@]}" >"$BACKUP/accounts.txt"
  py report "$MANIFEST" >"$BACKUP/before.txt"
  ( cd "$BACKUP" && sha256sum -- * >SHA256SUMS )
  chmod 600 "$BACKUP"/*
  echo "  $(du -sh "$BACKUP" | cut -f1)  app-db.sql.gz, keycloak-db.sql.gz, reviewer-data.tgz (data/users + data/staging), manifest.json, before.txt"
}

# ── verify ───────────────────────────────────────────────────────────────────
do_verify() {
  local rc=0
  echo "→ Database"
  py verify "$MANIFEST" || rc=1
  echo "→ Data folders match the database"
  local expected actual
  expected="$(py logids "$MANIFEST" | sort)"
  actual="$(in_api sh -c 'for id in "$@"; do for l in $(ls /app/data/users/$id/event_logs 2>/dev/null); do echo "$id $l"; done; done' sh "${IDS[@]}" | sort)"
  if [ "$expected" = "$actual" ]; then echo "  ✓ every log folder belongs to a seeded log, none missing"
  else echo "  ✗ log folders differ from the database:"; diff <(echo "$expected") <(echo "$actual") | sed 's/^/    /' || true; rc=1; fi
  in_api sh -c 'for id in "$@"; do [ -e /app/data/staging/$id ] && echo "  ✗ staging folder left: $id"; [ -e /app/data/users/$id/watched ] && echo "  ✗ watched folder left: $id"; done; true' sh "${IDS[@]}"
  echo "→ Keycloak (login): create-reviewer-accounts.sh status"
  KC_SERVER="$KC_SERVER" REALM="$REALM" KC_CONTAINER="$KC_CONTAINER" "$SCRIPT_DIR/create-reviewer-accounts.sh" status \
    | grep -v '^✓ Done' || rc=1
  for line in "${ACCOUNTS[@]}"; do
    state="$(kc_state "${line#* }")"
    echo "  ${line%% *}: $state"
    case "$state" in *LOCKED*) rc=1 ;; esac
  done
  echo "  login door: https://mate.uni-muenster.de/login/start?local=1 (not exercised: a real login would create state)"
  return $rc
}

# ── commands ─────────────────────────────────────────────────────────────────
case "$CMD" in
  report) do_report; exit 0 ;;
  backup) do_backup; echo "✓ Done."; exit 0 ;;
  verify) do_verify && { echo "✓ Verified."; exit 0; } || { echo "✗ Verification found differences (see above)." >&2; exit 1; } ;;
esac

# reset
busy="$(py busy "$MANIFEST")"
if [ "$busy" != "none" ] && [ "${ALLOW_BUSY:-0}" != "1" ]; then
  echo "✗ other accounts have jobs ($busy). The api restart would fail running ones." >&2
  echo "  Wait until they finish, or re-run with ALLOW_BUSY=1." >&2
  exit 1
fi
case " $WARM_MODULES " in
  *" cv4cdd "*) cv4cdd_model_ok || { echo "✗ no cv4cdd model under modules/cv4cdd/model – it would fail on every log." >&2; exit 1; } ;;
esac
[ -w "$MANIFEST" ] || { echo "✗ $MANIFEST is not writable for $(id -un) (sudo chown $(id -un): it)." >&2; exit 1; }

echo "→ Resetting: ${ACCOUNTS[*]/#/ }"
echo "  The api restarts once (all users: a few seconds offline, live views reconnect)."
if [ "$YES" != "1" ]; then
  read -r -p "  Type RESET to continue: " answer
  [ "$answer" = "RESET" ] || { echo "Aborted, nothing changed."; exit 1; }
fi

do_backup

echo "→ Manifest warm_modules := [$WARM_MODULES]"
WARM="$WARM_MODULES" python3 -I - "$MANIFEST" <<'PY'
import json, os, sys
path = sys.argv[1]
with open(path, encoding="utf-8") as fh:
    manifest = json.load(fh)
manifest["warm_modules"] = os.environ["WARM"].split()
with open(path + ".tmp", "w", encoding="utf-8") as fh:
    json.dump(manifest, fh, indent=2)
    fh.write("\n")
os.chmod(path + ".tmp", 0o644)
os.replace(path + ".tmp", path)
PY

echo "→ Stopping the api"
# Whatever fails from here on, the api must not stay down for everyone else.
restart_api_on_exit() {
  running "$API_CONTAINER" && return 0
  echo "! starting the api again after a failure (backup: $BACKUP)" >&2
  "${DC[@]}" start api >/dev/null || echo "✗ could not start the api: run '${DC[*]} start api'" >&2
}
trap restart_api_on_exit EXIT
"${DC[@]}" stop api >/dev/null

echo "→ Deleting the reviewers' rows (one transaction)"
py reset "$MANIFEST"

echo "→ Deleting their data folders and web sessions"
in_api sh -c 'for id in "$@"; do rm -rf -- "/app/data/users/$id" "/app/data/staging/$id"; done' sh "${IDS[@]}"
echo "  web sessions: $(web_sessions purge)"

echo "→ Ending their Keycloak sessions, clearing any login lockout"
for line in "${ACCOUNTS[@]}"; do
  uid="${line#* }"
  kc create "users/$uid/logout" -r "$REALM" >/dev/null 2>&1 || true
  kc delete "attack-detection/brute-force/users/$uid" -r "$REALM" >/dev/null 2>&1 || true
done

echo "→ Starting the api (it re-seeds the accounts)"
"${DC[@]}" start api >/dev/null

echo "→ Waiting for seeding and precompute"
if ! py wait "$MANIFEST"; then
  docker logs "$API_CONTAINER" 2>&1 | grep -E 'sample_content\.' | tail -n 20 >&2 || true
  echo "✗ Not finished. Backup: $BACKUP. Re-check later with: $0 verify" >&2
  exit 1
fi

if do_verify; then
  echo "✓ Reset done. Backup: $BACKUP"
else
  echo "✗ Reset finished, but verification found differences (above). Backup: $BACKUP" >&2
  exit 1
fi
