# JARVIS Deployment

Two-hop deploy through `barelysmash` (bastion) to `ocelia@guildenstern`. The
bastion does the sudo work; ocelia runs services as a regular user via
`systemctl --user`.

## How It Works

```
┌────────────────┐  rsync  ┌──────────────┐  ssh    ┌────────────────────┐
│ Local machine  │────────▶│ barelysmash  │────────▶│ guildenstern       │
│ (you)          │         │ (bastion)    │  sudo   │ /home/ocelia/...   │
└────────────────┘         └──────────────┘  chown  └────────────────────┘
                                                            │
                                                            ▼
                                                   systemctl --user
                                                   (no sudo needed)
```

Each deploy creates a timestamped release directory under `~/jarvis-releases/`
and atomically swaps the `~/jarvis` symlink to point at it. Old releases stay
on disk (default: 5 most recent) so rollback is instant.

Persistent state (ChromaDB, SQLite, logs, Google tokens) lives in
`~/jarvis-data/` outside the release tree, so deploys never destroy memory.

## One-Time Setup

These steps require admin access on `guildenstern` and only need to happen
once. After this, every subsequent deploy needs zero sudo.

```bash
# 1. Enable linger (services survive ocelia logging out)
sudo loginctl enable-linger ocelia

# 2. Audio group (only if deploying voice service)
sudo usermod -a -G audio ocelia

# 3. Verify runtime dir exists at boot
ls /run/user/$(id -u ocelia)
```

## Deploy

```bash
# 1. Copy env template, fill in secrets
cp deploy/env.template deploy/.env
chmod 600 deploy/.env
# Edit deploy/.env

# 2. Deploy
./deploy/deploy.sh

# 3. Watch it come up
./deploy/deploy.sh status
./deploy/deploy.sh logs jarvis-api
```

## Rollback

```bash
./deploy/deploy.sh rollback
```

The `~/jarvis-previous` symlink always points at the last release. Rollback
swaps the two symlinks and restarts services.

## Configuration

`deploy.config` is the single source of truth. You can override any value by
exporting env vars before running deploy.sh:

```bash
TARGET_HOST=other-host ./deploy/deploy.sh
```

## Services Deployed

| Unit | Purpose | Type |
|---|---|---|
| `jarvis-api.service` | FastAPI + WebSocket backend | long-running |
| `jarvis-voice.service` | Wake word + STT + TTS pipeline | long-running (opt-in) |
| `jarvis-briefing.service` | Morning briefing | oneshot |
| `jarvis-briefing.timer` | Triggers briefing weekdays at 6:45 AM | timer |
| `jarvis-sleep.service` | Memory maintenance cycle | oneshot |
| `jarvis-sleep.timer` | Triggers sleep cycle nightly at 3 AM | timer |

By default the voice service is **not** enabled (most VMs don't have audio).
To enable it, uncomment the line in `deploy.config`:

```bash
SERVICES+=("jarvis-voice")
```

## Common Operations

```bash
# Service status
./deploy/deploy.sh status

# Tail logs (any service)
./deploy/deploy.sh logs jarvis-api
./deploy/deploy.sh logs jarvis-briefing

# Manual one-off briefing
ssh barelysmash ssh guildenstern sudo -u ocelia \
  systemctl --user start jarvis-briefing

# List recent releases
ssh barelysmash ssh guildenstern sudo -u ocelia \
  ls -lt /home/ocelia/jarvis-releases
```

## Troubleshooting

**"Cannot SSH to bastion"**
SSH agent isn't loaded. Run `ssh-add ~/.ssh/id_ed25519` (or whatever your key is).

**"Bastion cannot reach guildenstern"**
Check that guildenstern is in `/etc/hosts` or DNS on the bastion. Override
`TARGET_HOST` in `deploy.config` to use IP if needed.

**Services fail to start after deploy**
SSH in and check logs:
```bash
./deploy/deploy.sh logs jarvis-api
```
Most common cause: missing env var in `deploy/.env` (especially
`ANTHROPIC_API_KEY`).

**Linger not enabled**
Services will die when ocelia logs out. The install script warns about this
but can't fix it without sudo. Have an admin run:
```bash
sudo loginctl enable-linger ocelia
```

**Need a clean state on target**
```bash
ssh barelysmash ssh guildenstern sudo -u ocelia bash -c "
  systemctl --user stop jarvis-api jarvis-voice
  systemctl --user disable jarvis-api jarvis-voice jarvis-briefing.timer jarvis-sleep.timer
  rm -rf ~/jarvis ~/jarvis-previous ~/jarvis-releases ~/jarvis-venv
  rm -rf ~/.config/systemd/user/jarvis-*
  systemctl --user daemon-reload
"
```
This nukes everything except `~/jarvis-data` (your memory). Add that to the
list if you want a truly clean slate.
