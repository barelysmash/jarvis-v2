#!/usr/bin/env bash
# install_routine_units.sh — sync the user's systemd timers with the
# routine units in this checkout. Runs ON guildenstern as ocelia.
#
#   bash scripts/install_routine_units.sh [checkout-dir]
#
# Called by both deploy paths (./deploy.sh after its git pull, and
# deploy/remote_install.sh after the release symlink swap), so the logic
# lives here once. Idempotent; safe to run by hand.
#
# For every deploy/systemd/jarvis-routine-*.{service,timer} in the checkout:
#   - copy it into ~/.config/systemd/user/
#   - enable --now the timer, unless the registry rendered it with
#     `# jarvis-routine-enabled: false` on line 1, in which case disable it
# Installed jarvis-routine-* timers with no unit in the checkout were
# removed from config/routines.yaml: disable and delete them.
#
# Override the unit directory with JARVIS_SYSTEMD_USER_DIR and the
# systemctl binary with JARVIS_SYSTEMCTL (tests use both).

set -euo pipefail

CHECKOUT="${1:-$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)}"
SOURCE_DIR="${CHECKOUT}/deploy/systemd"
UNIT_DIR="${JARVIS_SYSTEMD_USER_DIR:-${HOME}/.config/systemd/user}"
SYSTEMCTL="${JARVIS_SYSTEMCTL:-systemctl}"

sc() { "${SYSTEMCTL}" --user "$@"; }

[[ -d "${SOURCE_DIR}" ]] || { echo "[routines] no ${SOURCE_DIR}; nothing to install" >&2; exit 0; }
mkdir -p "${UNIT_DIR}"

shopt -s nullglob

installed=0
for unit in "${SOURCE_DIR}"/jarvis-routine-*.service "${SOURCE_DIR}"/jarvis-routine-*.timer; do
    cp "${unit}" "${UNIT_DIR}/"
    installed=$((installed + 1))
done

removed=0
for stale in "${UNIT_DIR}"/jarvis-routine-*.timer; do
    name="$(basename "${stale}")"
    if [[ ! -f "${SOURCE_DIR}/${name}" ]]; then
        echo "[routines] ${name}: removed from routines.yaml — disabling"
        sc disable --now "${name}" 2>/dev/null || true
        rm -f "${stale}" "${UNIT_DIR}/${name%.timer}.service"
        removed=$((removed + 1))
    fi
done

sc daemon-reload

for timer in "${SOURCE_DIR}"/jarvis-routine-*.timer; do
    name="$(basename "${timer}")"
    if head -n 1 "${timer}" | grep -q '^# jarvis-routine-enabled: false'; then
        echo "[routines] ${name}: disabled in routines.yaml"
        sc disable --now "${name}" 2>/dev/null || true
    else
        echo "[routines] ${name}: enabled"
        sc enable --now "${name}"
    fi
done

echo "[routines] ${installed} unit file(s) installed, ${removed} orphan(s) removed"
