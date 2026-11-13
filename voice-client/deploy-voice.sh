#!/usr/bin/env bash
# deploy-voice.sh
# Copy the repo's voice client over the live one on rosencrantz.
#
# The voice client does not run from this repo. The live copy sits in
# ~/jarvis-voice/ next to its own .env (API keys, audio device), so a
# `git pull` alone changes nothing that is running. This script is the
# missing step.
#
# What it does:
#   1. Checks the live folder and its .env exist.
#   2. Stops if the live copy already matches the repo copy.
#   3. Backs up the live copy with a timestamp.
#   4. Copies the repo copy over it and verifies the two are identical.
# It never touches .env, and it does not restart the client: relaunch it
# yourself afterwards.
#
# Run from Git Bash, from anywhere:
#   bash /c/bSmash-dev/jarvis-v2/voice-client/deploy-voice.sh

set -euo pipefail

SOURCE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)/voice_client.py"
LIVE_DIR="${JARVIS_VOICE_DIR:-$HOME/jarvis-voice}"
LIVE="${LIVE_DIR}/voice_client.py"

echo "== Voice client deploy"
echo "    from: ${SOURCE}"
echo "    to:   ${LIVE}"

if [ ! -d "${LIVE_DIR}" ]; then
  echo "    ERROR: ${LIVE_DIR} does not exist. Set JARVIS_VOICE_DIR if the live copy is elsewhere." >&2
  exit 1
fi
if [ ! -f "${LIVE_DIR}/.env" ]; then
  echo "    ERROR: ${LIVE_DIR}/.env is missing. The client cannot start without its keys; not deploying." >&2
  exit 1
fi

if [ -f "${LIVE}" ] && cmp -s "${SOURCE}" "${LIVE}"; then
  echo "    Live copy already matches the repo. Nothing to do."
  exit 0
fi

if [ -f "${LIVE}" ]; then
  BACKUP="${LIVE}.bak-$(date +%Y%m%d-%H%M%S)"
  cp -p "${LIVE}" "${BACKUP}"
  echo "    backup: ${BACKUP}"
fi

cp "${SOURCE}" "${LIVE}"
if ! cmp -s "${SOURCE}" "${LIVE}"; then
  echo "    ERROR: the copy does not match the source." >&2
  exit 1
fi

echo "    Deployed. Now close and relaunch the voice client."
echo "    To undo: copy the backup back over voice_client.py."
