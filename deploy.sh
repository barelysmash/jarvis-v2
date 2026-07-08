#!/usr/bin/env bash
# deploy.sh — build the HUD and ship everything to guildenstern.
#
# Usage:
#   ./deploy.sh              # build + push + deploy dist + pull + restart
#   ./deploy.sh --no-build   # skip the HUD build (server-only changes)
#
# Flow: git push (code) -> scp hud/dist (build artifact) -> git pull on
# guildenstern -> restart jarvis-api.service. Order matters: pull before
# scp'ing dist is safe now that hud/dist is untracked, but we pull first
# anyway so a tracked-file conflict never eats the fresh dist again
# (see: the Great Dist Deletion of July 8, 2026).

set -euo pipefail
cd "$(dirname "$0")"

HOST="ocelia@100.113.110.44"
REMOTE_DIR="/home/ocelia/jarvis"
SERVICE="jarvis-api.service"

BUILD=1
[[ "${1:-}" == "--no-build" ]] && BUILD=0

# --- Preflight: refuse to deploy uncommitted work ---
if [[ -n "$(git status --porcelain)" ]]; then
  echo "ERROR: uncommitted changes. Commit (or stash) before deploying:" >&2
  git status --short >&2
  exit 1
fi

# --- Build ---
if [[ $BUILD -eq 1 ]]; then
  echo "==> Building HUD..."
  (cd hud && npm run build)
else
  echo "==> Skipping HUD build (--no-build)"
fi

# --- Push code ---
echo "==> Pushing to GitHub..."
git push

# --- Pull code on guildenstern ---
echo "==> Pulling on guildenstern..."
ssh "$HOST" "cd $REMOTE_DIR && git pull --ff-only"

# --- Ship the built dist ---
if [[ $BUILD -eq 1 ]]; then
  echo "==> Deploying hud/dist..."
  ssh "$HOST" "mkdir -p $REMOTE_DIR/hud/dist"
  scp -r hud/dist/* "$HOST:$REMOTE_DIR/hud/dist/"
fi

# --- Restart & verify ---
echo "==> Restarting $SERVICE..."
ssh "$HOST" "systemctl --user restart $SERVICE"

echo "==> Waiting for health check..."
sleep 2
CODE=$(curl -s -o /dev/null -w "%{http_code}" "http://100.113.110.44:8765/api/health" || echo "000")
if [[ "$CODE" == "200" ]]; then
  echo "==> DEPLOYED. Health check OK. Hard-refresh the HUD (Ctrl+Shift+R)."
else
  echo "WARNING: health check returned $CODE — check the service:" >&2
  echo "  ssh $HOST 'systemctl --user status $SERVICE'" >&2
  exit 1
fi
