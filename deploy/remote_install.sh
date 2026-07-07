#!/usr/bin/env bash
#
# Runs on guildenstern as ocelia (after the bastion has chown'd the release).
# Sets up venv, installs deps, atomic-swaps the symlink, restarts services.
#

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
# shellcheck source=deploy.config
source "${SCRIPT_DIR}/deploy.config"

RELEASE_NAME="${1:?Release name required}"
RELEASE_PATH="${TARGET_RELEASES}/${RELEASE_NAME}"
CURRENT_LINK="${TARGET_INSTALL}"
PREVIOUS_LINK="${TARGET_HOME}/jarvis-previous"

echo "[install] Installing ${RELEASE_NAME} as $(whoami)"

# ─── Linger check (services need this to survive logout) ────────
if ! loginctl show-user "$(whoami)" 2>/dev/null | grep -q "Linger=yes"; then
    echo "[install] WARNING: linger not enabled for $(whoami)"
    echo "[install] Services will stop when you log out unless an admin runs:"
    echo "[install]     sudo loginctl enable-linger $(whoami)"
fi

# ─── Set up Python venv ─────────────────────────────────────────
if [[ ! -d "${TARGET_VENV}" ]]; then
    echo "[install] Creating Python venv..."
    ${PYTHON_VERSION} -m venv "${TARGET_VENV}"
fi

echo "[install] Installing Python dependencies..."
"${TARGET_VENV}/bin/pip" install --upgrade pip wheel --quiet

if [[ -f "${RELEASE_PATH}/requirements.txt" ]]; then
    "${TARGET_VENV}/bin/pip" install -r "${RELEASE_PATH}/requirements.txt" --quiet
else
    echo "[install] WARNING: no requirements.txt found in release"
fi
echo "[install] Dependencies installed"

# ─── Persistent data directories ────────────────────────────────
# Keep these OUTSIDE the release dir so deploys don't blow away state
mkdir -p "${TARGET_DATA}/chroma"
mkdir -p "${TARGET_DATA}/sleep_logs"
mkdir -p "${TARGET_DATA}/google"
mkdir -p "${TARGET_DATA}/logs"

# Symlink data into the release so the app finds it at relative paths
ln -sfn "${TARGET_DATA}" "${RELEASE_PATH}/data"

# Symlink Google credentials if they exist
if [[ -d "${TARGET_DATA}/google" ]]; then
    rm -rf "${RELEASE_PATH}/config/google"
    ln -sfn "${TARGET_DATA}/google" "${RELEASE_PATH}/config/google"
fi

# ─── Atomic symlink swap ────────────────────────────────────────
# This is what makes deploys instant and rollback trivial.
# ~/jarvis is always a symlink to a release dir. We never modify a running install.
if [[ -L "${CURRENT_LINK}" ]]; then
    rm -f "${PREVIOUS_LINK}"
    mv "${CURRENT_LINK}" "${PREVIOUS_LINK}"
fi

ln -sfn "${RELEASE_PATH}" "${CURRENT_LINK}"
echo "[install] Symlink: ${CURRENT_LINK} -> ${RELEASE_PATH}"

# ─── Install systemd user units ─────────────────────────────────
SYSTEMD_USER_DIR="${HOME}/.config/systemd/user"
mkdir -p "${SYSTEMD_USER_DIR}"

shopt -s nullglob
for unit in "${RELEASE_PATH}/deploy/systemd"/*.service "${RELEASE_PATH}/deploy/systemd"/*.timer; do
    cp "${unit}" "${SYSTEMD_USER_DIR}/"
done
shopt -u nullglob
echo "[install] Systemd units installed to ${SYSTEMD_USER_DIR}"

# ─── Reload + start services ────────────────────────────────────
export XDG_RUNTIME_DIR="/run/user/$(id -u)"

systemctl --user daemon-reload

for svc in "${SERVICES[@]}"; do
    if systemctl --user is-enabled "${svc}" &>/dev/null; then
        echo "[install] Restarting ${svc}..."
        systemctl --user restart "${svc}"
    else
        echo "[install] Enabling + starting ${svc}..."
        systemctl --user enable --now "${svc}"
    fi
done

for timer in "${TIMERS[@]}"; do
    if [[ -f "${SYSTEMD_USER_DIR}/${timer}" ]]; then
        echo "[install] Enabling timer ${timer}..."
        systemctl --user enable --now "${timer}" || true
    fi
done

# ─── Verify ─────────────────────────────────────────────────────
sleep 2
echo ""
echo "[install] Service status:"
for svc in "${SERVICES[@]}"; do
    state=$(systemctl --user is-active "${svc}" 2>&1 || echo "FAILED")
    echo "  ${svc}: ${state}"
done

# ─── Prune old releases ─────────────────────────────────────────
cd "${TARGET_RELEASES}"
ls -1t | tail -n +$((KEEP_RELEASES + 1)) | xargs -r rm -rf
echo "[install] Pruned to ${KEEP_RELEASES} most recent releases"

echo "[install] Done - ${RELEASE_NAME} is live"
