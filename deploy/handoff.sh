#!/usr/bin/env bash
#
# Runs on the bastion host (barelysmash).
# Ships the release to guildenstern, sudo-extracts it, chowns to ocelia,
# then triggers remote_install.sh as ocelia.
#

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
# shellcheck source=deploy.config
source "${SCRIPT_DIR}/deploy.config"

RELEASE_NAME="${1:?Release name required}"
TARBALL="${SCRIPT_DIR}/${RELEASE_NAME}.tar.gz"

GUILD_STAGING="/tmp/jarvis-handoff-${RELEASE_NAME}"
RELEASE_PATH="${TARGET_RELEASES}/${RELEASE_NAME}"

echo "[handoff] Shipping ${RELEASE_NAME} from bastion to ${TARGET_HOST}"

# ─── Step 1: Ship public deploy artifacts to Guildenstern ───────
ssh "${TARGET_HOST}" "mkdir -p ${GUILD_STAGING}"

scp "${TARBALL}" \
    "${SCRIPT_DIR}/remote_install.sh" \
    "${SCRIPT_DIR}/rollback.sh" \
    "${SCRIPT_DIR}/deploy.config" \
    "${TARGET_HOST}:${GUILD_STAGING}/"

scp -r "${SCRIPT_DIR}/systemd" "${TARGET_HOST}:${GUILD_STAGING}/"

echo "[handoff] Files staged on ${TARGET_HOST} at ${GUILD_STAGING}"

# ─── Step 2: Sudo-extract and hand off to ocelia ────────────────
ssh -t "${TARGET_HOST}" "bash -s" <<REMOTE_SCRIPT
set -euo pipefail

echo "[guild] Preparing release directory..."
sudo mkdir -p "${TARGET_RELEASES}"
sudo chown ${TARGET_USER}:${TARGET_USER} "${TARGET_RELEASES}"

# Extract tarball into release-specific dir
sudo mkdir -p "${RELEASE_PATH}"
sudo tar -xzf "${GUILD_STAGING}/${RELEASE_NAME}.tar.gz" \
    -C "${RELEASE_PATH}" --strip-components=1

# Copy public deploy artifacts into the release. Private runtime configuration
# remains on Guildenstern outside release directories and is never staged here.
sudo cp -r "${GUILD_STAGING}/systemd" "${RELEASE_PATH}/deploy/"
sudo cp "${GUILD_STAGING}/deploy.config" "${RELEASE_PATH}/deploy/"
sudo cp "${GUILD_STAGING}/remote_install.sh" "${RELEASE_PATH}/deploy/"
sudo cp "${GUILD_STAGING}/rollback.sh" "${RELEASE_PATH}/deploy/"
sudo chmod +x "${RELEASE_PATH}/deploy/remote_install.sh"
sudo chmod +x "${RELEASE_PATH}/deploy/rollback.sh"

# Hand ownership to ocelia (the critical step)
sudo chown -R ${TARGET_USER}:${TARGET_USER} "${RELEASE_PATH}"

echo "[guild] Release extracted and owned by ${TARGET_USER}"

# Step 3: Run install as ocelia (no sudo needed in homedir)
sudo -u ${TARGET_USER} -H bash -c "
    export XDG_RUNTIME_DIR=/run/user/\\\$(id -u)
    cd ${RELEASE_PATH}
    ./deploy/remote_install.sh ${RELEASE_NAME}
"

# Step 4: Clean up staging
rm -rf "${GUILD_STAGING}"
echo "[guild] Cleanup done"
REMOTE_SCRIPT

echo "[handoff] Install complete"
