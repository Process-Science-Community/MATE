#!/usr/bin/env bash
# make e2e-studio: Playwright check that the Studio shows a module's page 1:1.
# Scaffolds the git-ignored modules/e2e_studio, starts the lite stack with only
# that module on spare ports, runs apps/web/e2e, then stops the stack and
# removes the module. Extra arguments go to `playwright test`.
#
#   scripts/e2e-studio.sh
#   E2E_API_PORT=8778 E2E_WEB_PORT=3778 scripts/e2e-studio.sh --headed
#
# Uses a throwaway data dir, so the user's data/lite is never touched.
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT"

MOD=e2e_studio
PORT="${E2E_API_PORT:-8777}"
WEB_PORT="${E2E_WEB_PORT:-3777}"
STACK_LOG="$(mktemp "${TMPDIR:-/tmp}/mate-e2e-stack.XXXXXX")"
DATA_DIR="$(mktemp -d "${TMPDIR:-/tmp}/mate-e2e-data.XXXXXX")"

die() { echo "e2e-studio: $*" >&2; exit 1; }

[ -e "modules/$MOD" ] && die "modules/$MOD already exists (left over from a killed run?). Remove it and retry."

STACK_PID=""
cleanup() {
  trap - INT TERM EXIT
  if [ -n "$STACK_PID" ] && kill -0 "$STACK_PID" 2>/dev/null; then
    echo "e2e-studio: stopping the stack"
    kill -TERM "$STACK_PID" 2>/dev/null || true
    wait "$STACK_PID" 2>/dev/null || true
  fi
  rm -rf "modules/$MOD" "$DATA_DIR"
  echo "e2e-studio: stack log kept at $STACK_LOG"
}
trap cleanup INT TERM EXIT

echo "e2e-studio: scaffolding modules/$MOD"
uv run --extra dev mate-module new "$MOD"

echo "e2e-studio: starting the lite stack (API :$PORT, web :$WEB_PORT)"
LITE_DIR="$DATA_DIR" MODULES="$MOD" PORT="$PORT" WEB_PORT="$WEB_PORT" DEBUGPY_PORT=0 bash scripts/lite.sh >"$STACK_LOG" 2>&1 &
STACK_PID=$!

# lite.sh prints its banner once /health answers (module deps may install first).
for _ in $(seq 1 600); do
  grep -q "MATE lite is up" "$STACK_LOG" && break
  kill -0 "$STACK_PID" 2>/dev/null || { tail -40 "$STACK_LOG" >&2; die "the stack exited early"; }
  sleep 1
done
grep -q "MATE lite is up" "$STACK_LOG" || { tail -40 "$STACK_LOG" >&2; die "the stack did not come up in 10 minutes"; }

E2E_WEB_URL="http://localhost:$WEB_PORT" \
  E2E_API_URL="http://127.0.0.1:$PORT" \
  E2E_MODULE="$MOD" \
  pnpm --dir apps/web exec playwright test "$@"
