#!/usr/bin/env bash
# Nightly database backup for a hosted MATE instance (docs/DEPLOY.md, "Backup").
#
# Dumps both Postgres databases - the app metadata (users, logs, dashboards,
# usage tracking) and Keycloak (accounts, password hashes) - into BACKUP_DIR,
# readable by the owner only, and deletes dumps older than KEEP_DAYS.
# The dumps contain personal data: keep the directory private and off the repo.
#
#   crontab -e   →   17 3 * * * $HOME/mate/scripts/backup-db.sh >> $HOME/backups/backup.log 2>&1
set -euo pipefail

BACKUP_DIR="${BACKUP_DIR:-$HOME/backups}"
KEEP_DAYS="${KEEP_DAYS:-14}"
STAMP="$(date +%F-%H%M)"

umask 077
mkdir -p "$BACKUP_DIR"

dump() { # dump <container> <db user> <db name> <file prefix>
  local out="$BACKUP_DIR/$4-$STAMP.sql.gz"
  docker exec "$1" pg_dump -U "$2" "$3" | gzip > "$out.tmp"
  # An empty or truncated dump must not replace nothing with nothing silently.
  [ "$(stat -c %s "$out.tmp")" -gt 1024 ] || { echo "$(date -Is) FAILED: $4 dump is empty" >&2; rm -f "$out.tmp"; return 1; }
  mv "$out.tmp" "$out"
  echo "$(date -Is) ok $out ($(du -h "$out" | cut -f1))"
}

dump mate-app-db mate mate mate
dump mate-keycloak-db keycloak keycloak keycloak

find "$BACKUP_DIR" -maxdepth 1 -name '*.sql.gz' -mtime +"$KEEP_DAYS" -delete
