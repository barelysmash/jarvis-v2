#!/usr/bin/env bash
#
# JARVIS deployment orchestrator.
# Runs locally. Ships a release through the bastion to the target user.
#
# Usage:
#   ./deploy.sh             - full deploy
#   ./deploy.sh status      - check service health
#   ./deploy.sh logs <svc>  - tail logs for a service
#   ./deploy.sh rollback    - swap symlink back to previous release
#

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
# shellcheck source=deploy.config
source "${SCRIPT_DIR}/deploy.config"

# ─── Output helpers ─────────────────────────────────────────────
C_BLUE="\033[0;34m"
C_GREEN="\033[0;32m"
C_YELLOW="\033[0;33m"
C_RED="\033[0;31m"
C_RESET="\033[0m"

log()  { echo -e "${C_BLUE}[deploy]${C_RESET} $*"; }
ok()   { echo -e "${C_GREEN}[ ok ]${C_RESET} $*"; }
warn() { echo -e "${C_YELLOW}[warn]${C_RESET} $*"; }
fail() { echo -e "${C_RED}[fail]${C_RESET} $*" >&2; exit 1; }

# ─── Pre-flight checks ──────────────────────────────────────────
preflight() {
    log "Pre-flight checks..."

    [[ -d "${LOCAL_SOURCE}" ]] || fail "Source dir not found: ${LOCAL_SOURCE}"
    [[ -f "${LOCAL_SOURCE}/orchestrator/brain.py" ]] || \
        fail "Doesn't look like the JARVIS source tree"

    # Test bastion SSH
    if ! ssh -o ConnectTimeout=5 -o BatchMode=yes \
            "${BASTION_HOST}" "echo connected" &>/dev/null; then
        fail "Cannot SSH to ${BASTION_HOST} (check keys/agent)"
    fi
    ok "SSH to bastion works"

    # Test bastion → target
    if ! ssh "${BASTION_HOST}" \
            "ssh -o ConnectTimeout=5 -o BatchMode=yes ${TARGET_HOST} echo connected" \
            &>/dev/null; then
        fail "Bastion cannot reach ${TARGET_HOST}"
    fi
    ok "Bastion -> ${TARGET_HOST} reachable"

    # Confirm sudo on target via bastion
    if ! ssh "${BASTION_HOST}" \
            "ssh ${TARGET_HOST} sudo -n true" &>/dev/null; then
        warn "Sudo on ${TARGET_HOST} via bastion may prompt for password"
    else
        ok "Passwordless sudo confirmed on ${TARGET_HOST}"
    fi

    # Private runtime configuration belongs to the target host. During the
    # first deploy after this migration, remote_install.sh adopts the legacy
    # release-local env into persistent target state before changing symlinks.
    # Use direct remote test invocations here: nesting an additional `sh -c`
    # introduces a second shell-parsing layer and can corrupt the condition.
    if ssh "${BASTION_HOST}" \
            "ssh ${TARGET_HOST} sudo -u ${TARGET_USER} -H test -f '${TARGET_ENV}'" \
            &>/dev/null; then
        ok "Persistent target environment present"
    elif ssh "${BASTION_HOST}" \
            "ssh ${TARGET_HOST} sudo -u ${TARGET_USER} -H test -f '${TARGET_INSTALL}/deploy/.env'" \
            &>/dev/null; then
        ok "Legacy target environment present; deploy will adopt it"
    else
        fail "No private JARVIS environment found on ${TARGET_HOST}"
    fi
}

# ─── Build the release tarball ──────────────────────────────────
build_release() {
    log "Building release tarball..."

    local exclude_args=(
        --exclude='.git'
        --exclude='__pycache__'
        --exclude='*.pyc'
        --exclude='node_modules'
        --exclude='hud/dist'
        --exclude='hud/.vite'
        --exclude='data/chroma'
        --exclude='data/*.db'
        --exclude='data/logs/*.log'
        --exclude='data/sleep_logs/*.json'
        --exclude='*.log'
        --exclude='deploy/.env'
        --exclude='.venv'
        --exclude='venv'
        --exclude='.env'
    )

    tar -czf "${RELEASE_TARBALL}" \
        "${exclude_args[@]}" \
        -C "$(dirname "${LOCAL_SOURCE}")" \
        "$(basename "${LOCAL_SOURCE}")"

    local size
    size=$(du -h "${RELEASE_TARBALL}" | cut -f1)
    ok "Built ${RELEASE_NAME} (${size})"
}

# ─── Ship to bastion ────────────────────────────────────────────
ship_to_bastion() {
    log "Shipping to bastion..."

    ssh "${BASTION_HOST}" "mkdir -p ${BASTION_STAGING}"

    scp -q \
        "${RELEASE_TARBALL}" \
        "${SCRIPT_DIR}/handoff.sh" \
        "${SCRIPT_DIR}/remote_install.sh" \
        "${SCRIPT_DIR}/rollback.sh" \
        "${SCRIPT_DIR}/deploy.config" \
        "${BASTION_HOST}:${BASTION_STAGING}/"

    scp -q -r "${SCRIPT_DIR}/systemd" "${BASTION_HOST}:${BASTION_STAGING}/"

    ssh "${BASTION_HOST}" "chmod +x ${BASTION_STAGING}/*.sh"
    ok "Files staged on bastion"
}

# ─── Trigger the handoff ────────────────────────────────────────
run_handoff() {
    log "Running handoff: bastion -> ${TARGET_HOST} -> ${TARGET_USER}..."

    ssh -t "${BASTION_HOST}" \
        "cd ${BASTION_STAGING} && ./handoff.sh ${RELEASE_NAME}"

    ok "Handoff complete"
}

# ─── Cleanup ────────────────────────────────────────────────────
cleanup_local() {
    log "Cleaning up local tarball..."
    rm -f "${RELEASE_TARBALL}"
    ok "Local cleanup done"
}

cleanup_bastion() {
    log "Cleaning up bastion staging..."
    ssh "${BASTION_HOST}" "rm -rf ${BASTION_STAGING}" || warn "Bastion cleanup failed"
}

# ─── Health check ───────────────────────────────────────────────
health_check() {
    log "Verifying services on ${TARGET_HOST}..."

    local services_list="${SERVICES[*]}"
    local target_uid
    target_uid=$(ssh "${BASTION_HOST}" "ssh ${TARGET_HOST} id -u ${TARGET_USER}" 2>/dev/null)

    ssh "${BASTION_HOST}" "ssh ${TARGET_HOST} sudo -u ${TARGET_USER} bash" <<EOF
export XDG_RUNTIME_DIR=/run/user/${target_uid}
export DBUS_SESSION_BUS_ADDRESS=unix:path=/run/user/${target_uid}/bus
for svc in ${services_list}; do
    status=\$(systemctl --user is-active "\$svc" 2>&1 || echo failed)
    echo "  \$svc: \$status"
done
EOF
}
# ─── Logs ───────────────────────────────────────────────────────
follow_logs() {
    local svc="${1:-jarvis-api}"
    log "Tailing logs for ${svc} (Ctrl-C to stop)..."

    ssh -t "${BASTION_HOST}" \
        "ssh -t ${TARGET_HOST} sudo -u ${TARGET_USER} \
         XDG_RUNTIME_DIR=/run/user/\$(id -u ${TARGET_USER}) \
         journalctl --user -u ${svc} -f"
}

# ─── Rollback ───────────────────────────────────────────────────
do_rollback() {
    log "Rolling back to previous release on ${TARGET_HOST}..."

    ssh -t "${BASTION_HOST}" \
        "ssh -t ${TARGET_HOST} sudo -u ${TARGET_USER} \
         bash ${TARGET_INSTALL}/deploy/rollback.sh"

    ok "Rollback initiated"
    health_check
}

# ─── Main flow ──────────────────────────────────────────────────
main() {
    local command="${1:-deploy}"

    case "${command}" in
        deploy)
            preflight
            build_release
            ship_to_bastion
            run_handoff
            cleanup_local
            cleanup_bastion
            health_check
            ok "Deployment complete: ${RELEASE_NAME}"
            ;;
        rollback)
            do_rollback
            ;;
        status)
            health_check
            ;;
        logs)
            follow_logs "${2:-jarvis-api}"
            ;;
        *)
            cat <<EOF
Usage: $0 [command] [args]

Commands:
  deploy             Full deployment (default)
  status             Check service health on target
  logs <service>     Tail logs for a service
  rollback           Roll back to previous release

Examples:
  $0
  $0 status
  $0 logs jarvis-api
  $0 rollback
EOF
            exit 1
            ;;
    esac
}

main "$@"
