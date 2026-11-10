#!/bin/sh
# Minimal stack for developing ONE module: `make module-dev MODULE=modules/<folder>`.
#
# Stages MODULE plus its hard deps (manifest `requirements.modules` and `inputs[].from`, transitively,
# looked up by id in modules/) as symlinks named by manifest id under
# data/.module-dev/modules, points the API loader and the panel bundler at that
# folder, keeps every runtime byte under data/.module-dev/data, seeds a sample log
# big enough for the staged modules, and runs API + web in DEMO_MODE (no Keycloak).
# DEMO_MODE only ever lives in this process's environment - never in .env.
set -eu

root_dir=$(CDPATH= cd -- "$(dirname -- "$0")/.." && pwd)
cd "$root_dir"

module=${1:-${MODULE:-}}
if [ -z "$module" ] || [ ! -f "$module/manifest.yaml" ]; then
  printf '%s\n' "usage: make module-dev MODULE=modules/<folder>  (the folder needs a manifest.yaml)" >&2
  exit 2
fi
module=$(CDPATH= cd -- "$module" && pwd -P)

port=${PORT:-8000}
web_port=${WEB_PORT:-3000}
dev_root="$root_dir/data/.module-dev"
stage="$dev_root/modules"

# An explicit `next dev --port` on a busy port breaks the demo sign-in redirect.
if lsof -nP -tiTCP:"$web_port" -sTCP:LISTEN >/dev/null 2>&1; then
  printf '%s\n' "error: port $web_port is in use - free it or pass WEB_PORT=<port>" >&2
  exit 1
fi

rm -rf "$stage"
mkdir -p "$stage" "$dev_root/data"
# Line 1: the staged ids, MODULE's own id first. Line 2: the sample log to seed.
staged=$(uv run python - "$module" "$root_dir/modules" "$stage" <<'PY'
import re
import sys
from pathlib import Path

import yaml

target, repo_modules, stage = (Path(p) for p in sys.argv[1:])
# Smallest first: (path, log model, events, cases) - fixed facts about these files.
SAMPLES = [
    ("apps/api/tests/fixtures/sample.xes", "case_centric", 9, 3),
    ("simulated_data/input/autonomous/test_preprocessed.csv", "case_centric", 3989, 814),
    ("apps/api/tests/fixtures/sample.jsonocel", "object_centric", 4, 0),
]


def manifest(folder: Path) -> dict:
    return yaml.safe_load((folder / "manifest.yaml").read_text())


def event_log(folder: Path) -> dict:
    return (manifest(folder).get("requirements") or {}).get("event_log") or {}


try:
    root_id = manifest(target)["id"]
except Exception as exc:
    sys.exit(f"module-dev: cannot read the id from {target}/manifest.yaml: {exc}")
by_id: dict[str, Path] = {}
clashes: dict[str, set[Path]] = {}
for path in sorted(repo_modules.glob("*/manifest.yaml")):
    try:
        mid = manifest(path.parent)["id"]
    except Exception:
        continue  # an unrelated broken manifest must not block this module
    if mid in by_id:
        clashes.setdefault(mid, {by_id[mid]}).add(path.parent)
    by_id[mid] = path.parent
by_id[root_id] = target  # MODULE wins over a repo copy with the same id
clashes.pop(root_id, None)

order: list[str] = []
todo = [root_id]
while todo:
    mid = todo.pop(0)
    if mid in order:
        continue
    if mid not in by_id:
        sys.exit(f"module-dev: hard dependency {mid!r} not found under {repo_modules}")
    if mid in clashes:
        folders = ", ".join(sorted(str(f) for f in clashes[mid]))
        sys.exit(f"module-dev: module id {mid!r} is declared by several folders: {folders}")
    order.append(mid)
    # Named by id: hot reload maps a changed entry name back to the module id.
    (stage / mid).symlink_to(by_id[mid].resolve())
    # An entry may carry a version specifier (`discovery>=1,<2`): keep the id.
    todo += [re.match(r"\s*([a-z0-9_]+)", str(r)).group(1)
             for r in (manifest(by_id[mid]).get("requirements") or {}).get("modules") or []]
    # Input producers too (`inputs[].from`), as precompute_deps orders them.
    todo += [i["from"] for i in manifest(by_id[mid]).get("inputs") or [] if i.get("from")]

model = event_log(target).get("log_model") or "case_centric"
reqs = [r for r in (event_log(by_id[m]) for m in order)
        if (r.get("log_model") or "case_centric") == model]
need_events = max(r.get("min_events") or 0 for r in reqs)
need_cases = max(r.get("min_cases") or 0 for r in reqs)
sample = next(
    (p for p, m, ev, cs in SAMPLES if m == model and ev >= need_events and cs >= need_cases), ""
)
if not sample:
    print(
        f"module-dev: no bundled {model} sample log has {need_events} events and"
        f" {need_cases} cases - upload one in the web UI",
        file=sys.stderr,
    )
print(" ".join(order))
print(sample)
PY
)
staged_ids=$(printf '%s\n' "$staged" | sed -n 1p)
sample=$(printf '%s\n' "$staged" | sed -n 2p)
module_id=${staged_ids%% *}

# Like `make dev`: take .env when there is one, then pin what this stack relies on.
if [ -f .env ]; then
  set -a
  . ./.env
  set +a
fi
# Staged modules load as bundled defaults, so the plain demo user owns them - no admin.
export DEMO_MODE=true ENV=dev STORAGE_MODE=local
export MODULES_DIR="$stage" FF_MODULES_DIR="$stage" DATA_DIR="$dev_root/data"
export DATABASE_URL="sqlite+aiosqlite:///$dev_root/data/metadata.db"
export AUTH_URL="http://localhost:$web_port" CORS_ORIGINS="[\"http://localhost:$web_port\"]"
export NEXT_PUBLIC_API_URL="http://localhost:$port" INTERNAL_API_URL="http://localhost:$port"

uv run alembic -c apps/api/alembic.ini upgrade head

trap 'trap - INT TERM; kill 0' INT TERM
# Module edits (manifest.yaml included) go through the platform's hot reload, which
# also watches each staged symlink's real folder; uvicorn only reloads platform code.
uv run uvicorn mate.api.main:app --reload --reload-dir apps/api/src \
  --reload-dir packages/module-sdk-py/src --app-dir apps/api/src \
  --host 127.0.0.1 --port "$port" --timeout-graceful-shutdown 3 &
api_pid=$!
# apps/web's `dev` script, minus its hard-coded port.
(cd apps/web && exec pnpm exec next dev --turbo --port "$web_port") &
(cd apps/web && exec node scripts/bundle-modules.mjs --watch) &

api="http://127.0.0.1:$port"
auth="Authorization: Bearer demo-access-token" # auth/dependencies.DEMO_ACCESS_TOKEN
# Named per file, so a later run that needs a bigger log seeds that one alongside.
sample_name="module-dev sample (${sample##*/})"

find_sample_log() {
  curl -fsS -H "$auth" "$api/api/v1/event-logs" | uv run python -c \
    'import json, sys; print(next((r["id"] for r in json.load(sys.stdin) if r["name"] == sys.argv[1]), ""))' \
    "$sample_name"
}

seed() {
  log_id=
  # Health answers only after every staged module is installed and loaded.
  tries=0
  until curl -fsS -o /dev/null "$api/health" 2>/dev/null; do
    tries=$((tries + 1))
    [ "$tries" -lt 600 ] && kill -0 "$api_pid" 2>/dev/null || return 1
    sleep 1
  done
  missing=$(curl -fsS -H "$auth" "$api/api/v1/modules" | uv run python -c \
    'import json, sys; got = {m["id"] for m in json.load(sys.stdin)}; print(*(i for i in sys.argv[1:] if i not in got))' \
    $staged_ids) || return 1
  [ -z "$missing" ] ||
    printf '%s\n' "module-dev: $missing did not load (see the API log) - fix it and rerun" >&2
  [ -n "$sample" ] || return 0
  log_id=$(find_sample_log) || return 1
  if [ -z "$log_id" ]; then
    curl -fsS -o /dev/null -H "$auth" -F "file=@$sample" -F "name=$sample_name" \
      "$api/api/v1/event-logs" || return 1
    log_id=$(find_sample_log) || return 1
  fi
}

if seed; then
  printf '\n%s\n' "module-dev: $staged_ids staged from $module"
  printf '  %-7s %s\n' web "http://localhost:$web_port" api "$api/docs"
  [ -z "$log_id" ] ||
    printf '  %-7s %s\n' module "http://localhost:$web_port/processes/$log_id/modules/$module_id"
  printf '  %-7s %s\n' data "$dev_root (delete it to start over)"
else
  printf '%s\n' "module-dev: API not reachable or seeding failed - see the uvicorn log above" >&2
fi
wait
