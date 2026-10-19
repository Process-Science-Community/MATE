#!/bin/sh
set -eu

root_dir=$(CDPATH= cd -- "$(dirname -- "$0")/.." && pwd)
root_env="$root_dir/.env"
docker_mode=${1:-false}

if [ ! -f "$root_env" ]; then
  printf '%s\n' "error: missing $root_env" >&2
  printf '%s\n' "Copy .env.example to .env before starting the platform." >&2
  exit 1
fi

if [ "$docker_mode" = "true" ]; then
  exit 0
fi

demo_mode=$(sed -n 's/^DEMO_MODE=//p' "$root_env" | tail -n 1 | tr '[:upper:]' '[:lower:]')
auth_secret=$(sed -n 's/^AUTH_SECRET=//p' "$root_env" | tail -n 1)
if [ "$demo_mode" = "true" ] || [ "$demo_mode" = "1" ] || [ "$demo_mode" = "yes" ] || [ "$demo_mode" = "on" ]; then
  exit 0
fi
if [ -z "$auth_secret" ] || printf '%s' "$auth_secret" | grep -Eq '^(replace-me|dev-only-change-me)'; then
  printf '%s\n' "error: .env must define a non-placeholder AUTH_SECRET" >&2
  printf '%s\n' "Set DEMO_MODE=true for local plugin development, or provide AUTH_SECRET." >&2
  exit 1
fi
