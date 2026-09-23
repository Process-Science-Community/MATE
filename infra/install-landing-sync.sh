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
# Afterwards, point the proxy at the synced copy by adding this to .env and
# restarting the stack:
#
#   LANDING_DIR=/srv/mate-landing/current
set -euo pipefail

MATE_DIR="${MATE_DIR:-$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)}"
UNIT_DIR=/etc/systemd/system

[[ $EUID -eq 0 ]] || { echo "✗ run me as root (systemd units live in $UNIT_DIR)" >&2; exit 1; }
[[ -x "$MATE_DIR/scripts/sync-landing.sh" ]] || { echo "✗ $MATE_DIR/scripts/sync-landing.sh not found or not executable" >&2; exit 1; }

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
echo "✔ installed. Next steps:"
echo "    1. add  LANDING_DIR=/srv/mate-landing/current  to .env"
echo "    2. docker compose -f docker-compose.yml -f docker-compose.prod.yml up -d proxy"
echo
echo "  Check on it later with:"
echo "    systemctl list-timers mate-landing-sync.timer"
echo "    journalctl -u mate-landing-sync.service -n 20"
