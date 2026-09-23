#!/usr/bin/env bash
#
# Keep the public landing page on the VM in step with origin/main's landing/.
#
# The VM serves the landing page from a directory bind-mounted into the Caddy
# container (LANDING_DIR). Without this, that directory only advances on a full
# `scripts/deploy.sh` run, so the site drifts behind GitHub Pages - which
# redeploys on every push to main.
#
# Pull, not push: the VM is only reachable through the department VPN, so a
# GitHub-hosted runner cannot reach it. A systemd timer runs this instead; see
# infra/install-landing-sync.sh.
#
# Only landing/ is synced. App code is deliberately untouched: advancing it
# without rebuilding the containers would leave the checkout ahead of what is
# actually running.
set -euo pipefail

REPO_URL="${LANDING_REPO_URL:-https://github.com/Process-Science-Community/MATE.git}"
BRANCH="${LANDING_BRANCH:-main}"
ROOT="${LANDING_SYNC_ROOT:-/srv/mate-landing}"

SRC="$ROOT/repo"      # sparse clone - landing/ only
LIVE="$ROOT/current"  # what Caddy serves

for cmd in git rsync; do
  command -v "$cmd" >/dev/null || { echo "✗ $cmd is required but not installed" >&2; exit 1; }
done

mkdir -p "$ROOT" "$LIVE"

# Blobless + sparse: the clone carries landing/ and nothing else, so the
# screenshots are the only real payload.
if [[ ! -d "$SRC/.git" ]]; then
  echo "• first run - cloning $REPO_URL"
  git clone --quiet --no-checkout --filter=blob:none --branch "$BRANCH" "$REPO_URL" "$SRC"
  git -C "$SRC" sparse-checkout init --cone
  git -C "$SRC" sparse-checkout set landing
  git -C "$SRC" checkout --quiet "$BRANCH"
fi

before="$(git -C "$SRC" rev-parse HEAD)"
git -C "$SRC" fetch --quiet origin "$BRANCH"
git -C "$SRC" reset --quiet --hard "origin/$BRANCH"
after="$(git -C "$SRC" rev-parse HEAD)"

# rsync renames each file into place, so a request in flight never sees a
# half-written page. --delete drops assets that main removed.
rsync -a --delete "$SRC/landing/" "$LIVE/"

if [[ "$before" == "$after" ]]; then
  echo "✔ landing already current at ${after:0:8}"
else
  echo "✔ landing synced ${before:0:8} → ${after:0:8}"
  git -C "$SRC" --no-pager log --oneline "$before..$after" -- landing | sed 's/^/    /'
fi
