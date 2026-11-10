#!/usr/bin/env bash
#
# Install the reviewer-account expiry on the VM. Run once, on the VM, as root:
#
#   sudo ./infra/install-reviewer-expiry.sh
#
# It installs a systemd timer that runs
# `infra/keycloak/create-reviewer-accounts.sh expire` daily. That command
# disables every reviewer account (and ends its sessions) once today is on or
# after REVIEWER_EXPIRES (default 2026-12-15), and does nothing before. Re-run
# it after the units change; it is idempotent. Remove it after the review with:
#
#   sudo systemctl disable --now mate-reviewer-expiry.timer
#
# Overrides (persisted to /etc/default/mate-reviewer-expiry):
#   KC_SERVER         default http://localhost:8080/auth (Keycloak under /auth on the VM)
#   REVIEWER_EXPIRES  YYYY-MM-DD, default 2026-12-15
#   REVIEWER_PREFIX / REVIEWER_COUNT / KC_CONTAINER / REALM, as for the script
set -euo pipefail

MATE_DIR="${MATE_DIR:-$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)}"
UNIT_DIR=/etc/systemd/system
SCRIPT="$MATE_DIR/infra/keycloak/create-reviewer-accounts.sh"

[[ $EUID -eq 0 ]] || { echo "✗ run me as root (systemd units live in $UNIT_DIR)" >&2; exit 1; }
[[ -x "$SCRIPT" ]] || { echo "✗ $SCRIPT not found or not executable" >&2; exit 1; }
[[ -f "$MATE_DIR/.env" ]] || echo "! $MATE_DIR/.env not found – the script falls back to admin/admin for Keycloak" >&2

KC_SERVER="${KC_SERVER:-http://localhost:8080/auth}"
DEFAULTS=/etc/default/mate-reviewer-expiry
: > "$DEFAULTS"
chmod 644 "$DEFAULTS"
for var in KC_SERVER REVIEWER_EXPIRES REVIEWER_PREFIX REVIEWER_COUNT KC_CONTAINER REALM; do
  if [[ -n "${!var:-}" ]]; then
    echo "$var=${!var}" >> "$DEFAULTS"
    echo "• $var=${!var}"
  fi
done

echo "• installing units (MATE_DIR=$MATE_DIR)"
for unit in mate-reviewer-expiry.service mate-reviewer-expiry.timer; do
  sed "s|__MATE_DIR__|$MATE_DIR|g" "$MATE_DIR/infra/systemd/$unit" > "$UNIT_DIR/$unit"
done

systemctl daemon-reload
systemctl enable --now mate-reviewer-expiry.timer

echo "• first check (a no-op before the expiry date)"
systemctl start mate-reviewer-expiry.service || true
journalctl -u mate-reviewer-expiry.service -n 3 --no-pager || true

echo
echo "✔ installed. Check on it with:"
echo "    systemctl list-timers mate-reviewer-expiry.timer"
echo "    journalctl -u mate-reviewer-expiry.service -n 20"
