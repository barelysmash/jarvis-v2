# JARVIS HUD

The desktop visual interface — Electron + React, streams live state from the
JARVIS API server over WebSocket.

## Setup

```bash
npm install
```

## Development

```bash
npm run start
```

This launches Vite (dev server on port 5173) and Electron together. The app
will connect to `ws://127.0.0.1:8765/ws` by default — make sure the JARVIS
backend is running first.

## Build

```bash
npm run build
```

Output goes to `dist/`. Electron will load from `dist/index.html` in
non-development mode.

## Configuration

WebSocket URL and API base can be overridden via env vars at build time:

```bash
VITE_WS_URL=ws://my-jarvis-host:8765/ws npm run build
VITE_API_BASE=http://my-jarvis-host:8765 npm run build
```

## Components

- `VoiceOrb` — animated icosahedron with shader displacement, color-shifts by state
- `StatusBar` — uptime, latency, memory size, link status
- `ConversationLog` — scrolling transcript
- `ToolFeed` — live tool execution feed
- `AmbientLayer` — particle field + scan line background
- `DataWidget` — right-side panels (calendar, weather, home, messages)

## State

All state lives in `useJarvisState` — a hook that connects to the WebSocket
and dispatches events into a single React state object. The hook auto-reconnects
on disconnect with a 2s backoff.
