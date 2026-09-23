#!/usr/bin/env bash
#
# Install the landing-page auto-sync on the VM. Run once, on the VM, as root:
#
#   sudo ./infra/install-landing-sync.sh
#
# It installs a systemd timer that runs scripts/sync-landing.sh every 5 minutes,
# so the public landing page tracks origin/main without a full deploy. Re-run it
# after the units change; it is idempotent.
#
# By default it syncs into /srv/mate-landing/current, which the proxy reaches
# via LANDING_DIR in .env (needs the container recreated once).
#
# On a stack you do not want to recreate, point it straight at a directory the
# proxy ALREADY mounts - then nothing about docker-compose changes and a
# `restart proxy` is enough:
#
#   sudo LANDING_LIVE_DIR="$PWD/landing" ./infra/install-landing-sync.sh
set -euo pipefail

MATE_DIR="${MATE_DIR:-$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)}"
UNIT_DIR=/etc/systemd/system

[[ $EUID -eq 0 ]] || { echo "✗ run me as root (systemd units live in $UNIT_DIR)" >&2; exit 1; }
[[ -x "$MATE_DIR/scripts/sync-landing.sh" ]] || { echo "✗ $MATE_DIR/scripts/sync-landing.sh not found or not executable" >&2; exit 1; }

# Any override the caller passed has to survive into the systemd unit, which
# does not inherit this shell's environment.
DEFAULTS=/etc/default/mate-landing-sync
: > "$DEFAULTS"
for var in LANDING_LIVE_DIR LANDING_SYNC_ROOT LANDING_REPO_URL LANDING_BRANCH; do
  if [[ -n "${!var:-}" ]]; then
    echo "$var=${!var}" >> "$DEFAULTS"
    echo "• $var=${!var}"
  fi
done
[[ -s "$DEFAULTS" ]] || rm -f "$DEFAULTS"

echo "• installing units (MATE_DIR=$MATE_DIR)"
for unit in mate-landing-sync.service mate-landing-sync.timer; do
  sed "s|__MATE_DIR__|$MATE_DIR|g" "$MATE_DIR/infra/systemd/$unit" > "$UNIT_DIR/$unit"
done

systemctl daemon-reload
systemctl enable --now mate-landing-sync.timer

echo "• priming the first sync"
systemctl start mate-landing-sync.service

echo
systemctl --no-pager --full status mate-landing-sync.service | tail -n 12 || true
echo
if [[ -n "${LANDING_LIVE_DIR:-}" ]]; then
  echo "✔ installed - syncing into $LANDING_LIVE_DIR (already mounted by the proxy)."
  echo "  Nothing to change in docker-compose. To pick up a new Caddyfile:"
  echo "    docker compose -f docker-compose.yml -f docker-compose.prod.yml restart proxy"
else
  echo "✔ installed. Next steps:"
  echo "    1. add  LANDING_DIR=/srv/mate-landing/current  to .env"
  echo "    2. docker compose -f docker-compose.yml -f docker-compose.prod.yml up -d --no-deps proxy"
fi
echo
echo "  Check on it later with:"
echo "    systemctl list-timers mate-landing-sync.timer"
echo "    journalctl -u mate-landing-sync.service -n 20"
