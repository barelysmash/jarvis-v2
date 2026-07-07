# JARVIS

A personal AI agent framework inspired by Tony Stark's assistant. Modular,
extensible, and built around Claude as the reasoning core.

## What's Inside

- **Orchestrator** — reasoning brain with tool use, plus a four-layer memory
  system (working / semantic / episodic / procedural) and a nightly maintenance
  cycle (decay, consolidation, summarization).
- **Workflows** — multi-step task pipelines. Includes a fully-built morning
  briefing workflow that exercises every layer.
- **Voice** — wake word detection, streaming STT, streaming TTS with
  interruption support.
- **Server** — FastAPI + WebSocket bridge that exposes JARVIS state to clients.
- **HUD** — Electron + React desktop interface with a live voice orb,
  conversation log, tool activity feed, and ambient particle field.
- **Integrations** — Google Calendar with full CRUD plus natural-language time
  parsing. Pattern is reusable for Gmail, Home Assistant, etc.
- **Deploy** — two-hop deployment pipeline (local → bastion → target user) with
  atomic symlink swaps, rollback, systemd user services, and scheduled
  workflows.

## Quick Start

**Requires Python 3.11 or 3.12.** Python 3.14 doesn't have prebuilt wheels for
several C extensions yet — if you're on 3.14, install an older version
alongside (e.g., via `pyenv` or python.org installer) before running this.

```bash
# 1. Create a virtual environment (recommended)
python -m venv .venv
# Linux/macOS:
source .venv/bin/activate
# Windows (Git Bash):
source .venv/Scripts/activate
# Windows (cmd):
.venv\Scripts\activate.bat

# 2. Install core dependencies (works on every platform)
pip install -r requirements.txt

# 3. Set up environment
cp .env.template .env
# edit .env — at minimum set ANTHROPIC_API_KEY

# 4. Run JARVIS in CLI mode (no audio, no Google needed)
python main.py
```

Optional next steps:

```bash
# Authorize Google Calendar (first time only, requires credentials.json)
python scripts/auth_google.py

# Start the API + HUD
python -m uvicorn server.api:app --port 8765 &
cd hud && npm install && npm run start

# Voice pipeline (only on machines with mic/speakers — see PLATFORM_NOTES.md)
pip install -r requirements-voice.txt
python scripts/run_voice.py
```

## Directory Map

```
jarvis/
├── orchestrator/        # Brain + memory
│   ├── brain.py
│   ├── tools.py
│   ├── personality.py
│   └── memory/
├── tools/integrations/  # External service adapters
├── workflows/           # Multi-step task pipelines
├── voice/               # Wake word + STT + TTS
├── server/              # FastAPI WebSocket server
├── hud/                 # Electron + React frontend
├── scripts/             # Entry points & utilities
├── deploy/              # Deployment pipeline
└── data/                # Persistent state (gitignored)
```

## Deployment

See `deploy/README.md` for the two-hop deploy through a bastion host to a
sudoless target user. TL;DR:

```bash
cp deploy/env.template deploy/.env
# fill in deploy/.env
./deploy/deploy.sh
```

## Architecture

The system runs as a layered stack:

```
┌─────────────────────────────────────────────────┐
│  HUD (Electron + React)                         │
└──────────────────┬──────────────────────────────┘
                   │ WebSocket
┌──────────────────▼──────────────────────────────┐
│  Server (FastAPI)                               │
│  ├── Event Bus (pub/sub for HUD updates)        │
│  └── REST endpoints                             │
└──────────────────┬──────────────────────────────┘
                   │
┌──────────────────▼──────────────────────────────┐
│  Brain (Claude + ReAct loop)                    │
│  ├── Tool Registry (extensible)                 │
│  ├── Memory Store (4 layers)                    │
│  └── Personality (system prompt)                │
└──────────┬─────────────┬────────────────────────┘
           │             │
┌──────────▼──┐   ┌──────▼──────────────────────┐
│  Voice      │   │  Tools (Calendar, etc.)     │
│  Pipeline   │   │                             │
└─────────────┘   └─────────────────────────────┘
```

## Requirements

- Python 3.11+
- Node 18+ (for HUD)
- Linux/macOS for voice pipeline (PortAudio)
- API keys: Anthropic (required), plus optional ElevenLabs, Deepgram,
  Picovoice, Google Cloud
