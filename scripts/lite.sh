#!/usr/bin/env bash
# Lite dev mode: the whole stack natively (no Docker), tuned for low memory.
# API (uvicorn --reload) + module panel bundler (watch) + Next.js dev, one Ctrl-C stops all.
# Needs no .env: demo login, SQLite and local storage under data/lite/.
#
#   scripts/lite.sh                      # all modules
#   MODULES=discovery,performance scripts/lite.sh
#   PORT=8001 WEB_PORT=3001 DEBUGPY_PORT=5679 scripts/lite.sh   # a second stack
#   scripts/lite.sh --print-env          # print the resolved env, start nothing
#
# See docs/DEV_LITE.md.
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT"

PORT="${PORT:-8000}"
WEB_PORT="${WEB_PORT:-3000}"
MODULES="${MODULES:-}"
LITE_DIR="${LITE_DIR:-$ROOT/data/lite}"

usage() {
  sed -n '2,11p' "$0" | sed 's/^# \{0,1\}//'
}

resolve_env() {
  export ENV=dev
  export DEMO_MODE=true
  export DEMO_ADMIN=true
  export STORAGE_MODE=local
  export MCP_ENABLED=false
  export DATA_DIR="$LITE_DIR"
  export DATABASE_URL="sqlite+aiosqlite:///$LITE_DIR/metadata.db"
  export MODULES_DIR="$ROOT/modules"
  export MODULES_ONLY="$MODULES"
  export DEV_FORCE_THREAD="${DEV_FORCE_THREAD:-true}"
  export DEBUGPY_PORT="${DEBUGPY_PORT:-5678}"
  # Memory levers (see docs/DEV_LITE.md): one job slot, one offload process, small DuckDB.
  export WORKER_CONCURRENCY=1
  export MODULE_PROCESS_POOL_SIZE=1
  export MAX_OFFLOADS_PER_USER=1
  export DUCKDB_THREADS=2
  export DUCKDB_MEMORY_LIMIT=1GB
  export EVENT_LOG_CACHE_ENTRIES=1
  export MALLOC_ARENA_MAX=2 # glibc only; a no-op on macOS
  export CORS_ORIGINS="[\"http://localhost:$WEB_PORT\",\"http://127.0.0.1:$WEB_PORT\"]"
  # Web: browser -> API, Next server -> API, Auth.js base URL.
  export NEXT_PUBLIC_API_URL="http://localhost:$PORT"
  export INTERNAL_API_URL="http://127.0.0.1:$PORT"
  export AUTH_URL="http://localhost:$WEB_PORT"
  export AUTH_SECRET="${AUTH_SECRET:-mate-lite-dev-auth-secret-local-only}"
}

ENV_KEYS="ENV DEMO_MODE DEMO_ADMIN STORAGE_MODE MCP_ENABLED DATA_DIR DATABASE_URL MODULES_DIR
MODULES_ONLY DEV_FORCE_THREAD DEBUGPY_PORT WORKER_CONCURRENCY MODULE_PROCESS_POOL_SIZE
MAX_OFFLOADS_PER_USER DUCKDB_THREADS DUCKDB_MEMORY_LIMIT EVENT_LOG_CACHE_ENTRIES MALLOC_ARENA_MAX
CORS_ORIGINS NEXT_PUBLIC_API_URL INTERNAL_API_URL AUTH_URL AUTH_SECRET"

case "${1:-}" in
  -h | --help) usage; exit 0 ;;
  --print-env)
    resolve_env
    for k in $ENV_KEYS; do printf '%s=%s\n' "$k" "${!k}"; done
    exit 0
    ;;
  "") ;;
  *) echo "lite: unknown argument '$1' (try --help)" >&2; exit 2 ;;
esac

die() { echo "lite: $*" >&2; exit 1; }

command -v uv >/dev/null || die "uv not found. Install it (https://docs.astral.sh/uv/), then run 'make install'."
command -v pnpm >/dev/null || die "pnpm not found. Install it (corepack enable), then run 'make install'."
command -v curl >/dev/null || die "curl not found."
[ -d apps/web/node_modules ] || die "apps/web/node_modules missing. Run 'make install' first."

resolve_env

# Refuse to start on a busy port instead of killing whatever owns it (another stack, another clone).
port_busy() { (exec 3<>"/dev/tcp/127.0.0.1/$1") 2>/dev/null; }
for spec in "PORT:$PORT" "WEB_PORT:$WEB_PORT" "DEBUGPY_PORT:$DEBUGPY_PORT"; do
  name="${spec%%:*}"
  p="${spec#*:}"
  if [ "$p" != 0 ] && port_busy "$p"; then
    die "port $p ($name) is already in use. Stop that process or pick another: make lite $name=<free port>"
  fi
done

mkdir -p "$LITE_DIR"

echo "lite: migrating $LITE_DIR/metadata.db"
uv run --extra dev alembic -c apps/api/alembic.ini upgrade head

cleanup() {
  trap - INT TERM EXIT
  echo
  echo "lite: stopping"
  # Each child runs in its own process group (set -m), so this stops whole trees without
  # signalling whoever started us (make, a terminal, another script).
  for pid in $CHILD_PIDS; do kill -TERM -- "-$pid" 2>/dev/null || true; done
  # debugpy adapters detach into their own session; scoped to this checkout's venv.
  pkill -f "$ROOT/.venv/lib/.*/debugpy/" 2>/dev/null || true
}
CHILD_PIDS=""
trap cleanup INT TERM EXIT
set -m

# --reload covers platform + SDK code only; modules/ is hot-reloaded by the app itself (ENV=dev).
uv run --extra dev uvicorn mate.api.main:app --reload \
  --reload-dir apps/api/src --reload-dir packages/module-sdk-py/src \
  --app-dir apps/api/src --host 127.0.0.1 --port "$PORT" --timeout-graceful-shutdown 3 &
API_PID=$!
# The bundler honours MODULES_ONLY itself.
(cd apps/web && node scripts/bundle-modules.mjs --watch) &
BUNDLER_PID=$!
(cd apps/web && pnpm exec next dev --turbo --port "$WEB_PORT") &
CHILD_PIDS="$API_PID $BUNDLER_PID $!"

API="http://127.0.0.1:$PORT"
AUTH_HEADER="Authorization: Bearer demo-access-token" # auth/dependencies.DEMO_ACCESS_TOKEN

# /health answers only after the lifespan finished loading modules (first start may install module deps).
echo "lite: waiting for the API on $API/health"
for _ in $(seq 1 600); do
  curl -fsS "$API/health" >/dev/null 2>&1 && break
  kill -0 "$API_PID" 2>/dev/null || break
  sleep 1
done

if curl -fsS "$API/health" >/dev/null 2>&1; then
  logs="$(curl -fsS -H "$AUTH_HEADER" "$API/api/v1/event-logs" || echo "?")"
  # Skip the first-run wizard and tour; it would cover the Studio on a fresh data dir.
  curl -fsS -o /dev/null -X PUT -H "$AUTH_HEADER" -H "Content-Type: application/json" \
    -d '{"completed":true,"experience_level":"expert","tour_completed":true}' \
    "$API/api/v1/onboarding" || true
  if [ "$logs" = "[]" ]; then
    echo "lite: seeding a sample event log (scripts/lite/sample_log.csv, 120 cases)"
    curl -fsS -o /dev/null -H "$AUTH_HEADER" \
      -F "file=@scripts/lite/sample_log.csv" -F "name=Order process (lite)" \
      "$API/api/v1/event-logs" || echo "lite: seeding failed (upload it from the UI instead)" >&2
    # An object-centric module shows on no case-centric log: seed the OCEL fixture too.
    first="modules/${MODULES%%,*}/manifest.yaml"
    if [ -n "$MODULES" ] && [ -f "$first" ] && grep -Eq '^[[:space:]]*log_model:[[:space:]]*object_centric' "$first"; then
      echo "lite: seeding an OCEL sample (apps/api/tests/fixtures/sample.jsonocel)"
      curl -fsS -o /dev/null -H "$AUTH_HEADER" \
        -F "file=@apps/api/tests/fixtures/sample.jsonocel" -F "name=OCEL sample (lite)" \
        "$API/api/v1/event-logs" || echo "lite: OCEL seeding failed (upload it from the UI instead)" >&2
    fi
  fi
  loaded="$(curl -fsS -H "$AUTH_HEADER" "$API/api/v1/modules" | uv run --extra dev python -c \
    'import json, sys; print(", ".join(sorted(m["id"] for m in json.load(sys.stdin))))' || true)"
  cat <<EOF

  MATE lite is up
  ---------------------------------------------------------------
  Web          http://localhost:$WEB_PORT   (demo login, admin)
  Studio       http://localhost:$WEB_PORT/dev
  API          http://localhost:$PORT   (docs: /docs)
  Debugger     debugpy on 127.0.0.1:$DEBUGPY_PORT  (0 = off; VS Code: "Attach: MATE API (lite)")
  Data dir     $LITE_DIR   (wipe: make reset)
  MODULES      ${MODULES:-all}
  Loaded       ${loaded:-?}
  ---------------------------------------------------------------
  Ctrl-C stops everything.

EOF
else
  echo "lite: the API is not answering /health (crashed or still loading); check the uvicorn output above." >&2
fi

wait
