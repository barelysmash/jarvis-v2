#!/usr/bin/env bash
#
# Rolls back to the previous release by swapping the jarvis symlink.
# Runs as ocelia on guildenstern.
#

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
# shellcheck source=deploy.config
source "${SCRIPT_DIR}/deploy.config"

PREVIOUS_LINK="${TARGET_HOME}/jarvis-previous"
CURRENT_LINK="${TARGET_INSTALL}"

if [[ ! -L "${PREVIOUS_LINK}" ]]; then
    echo "[rollback] No previous release to roll back to"
    exit 1
fi

PREVIOUS_TARGET="$(readlink "${PREVIOUS_LINK}")"
CURRENT_TARGET="$(readlink "${CURRENT_LINK}")"

echo "[rollback] Rolling back from $(basename "${CURRENT_TARGET}") to $(basename "${PREVIOUS_TARGET}")"

# Swap symlinks
ln -sfn "${PREVIOUS_TARGET}" "${CURRENT_LINK}"
ln -sfn "${CURRENT_TARGET}" "${PREVIOUS_LINK}"

# Restart services
export XDG_RUNTIME_DIR="/run/user/$(id -u)"
for svc in "${SERVICES[@]}"; do
    echo "[rollback] Restarting ${svc}..."
    systemctl --user restart "${svc}"
done

echo "[rollback] Complete"
echo ""
echo "Service status:"
for svc in "${SERVICES[@]}"; do
    state=$(systemctl --user is-active "${svc}" 2>&1 || echo "FAILED")
    echo "  ${svc}: ${state}"
done
