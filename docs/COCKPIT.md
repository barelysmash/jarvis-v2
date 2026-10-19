# JARVIS Cockpit

The HUD is the glanceable face. The cockpit is the second screen: every
turn, every model call, every tool call, every scheduled job and every key
change a sub-agent reports, live and in order.

Open it at `http://100.113.110.44:8765/cockpit.html` (or
`http://guildenstern:8765/cockpit.html` over MagicDNS). It is served by the
same FastAPI static mount as the HUD, so there is nothing extra to deploy.

To open it as a native window on the second monitor from Rosencrantz:

    cd C:\bSmash-dev\jarvis-v2\hud && npm run electron:cockpit

`electron:cockpit` opens the HUD on the primary display and the cockpit
maximized on the next display. `electron:cockpit-only` opens just the
cockpit. Set `JARVIS_URL` to point at a different server.

## Panes

| Pane | Shows | Source |
|---|---|---|
| Conversation | Each turn as a card: your input, every model call (model, latency, tokens, stop reason, interim text), every tool call nested inline (click for full args + result), the final reply. Filter by origin (hud, voice, briefing). | `turn.start`, `llm`, `tool`, `turn.end` events |
| Tool activity | Live list; running calls pinned with a ticking timer. `stats` tab: calls, errors, avg/p95 per tool. `raw` tab: the raw event tail with a text filter. | `tool` events |
| Scheduled | Every `systemctl --user` timer: schedule expression, next run countdown, last run + result, last job summary, **run now** (two clicks). | `/api/cockpit/schedule` + `job` events |
| Agents & changes | Agent roster with load status and tool counts (click to filter). Feed of lifecycle events, job start/end, and anything sub-agents POST to `/api/events`. | `agent`, `job` events |

## How it works

All panes read one stream: the cross-process SQLite event log
(`$JARVIS_DATA_DIR/events.db`, `orchestrator/event_log.py`). Every JARVIS
process writes to it — the API, the briefing job, the sleep cycle, voice —
and `/ws/cockpit` tails it by row id. On reconnect the page resumes from the
last id it saw, so nothing is dropped or duplicated.

History is kept for `JARVIS_EVENT_RETENTION_DAYS` (default 7) and pruned by
the nightly sleep cycle.

## Sub-agents: reporting key changes

Any sub-agent or script can push a change into the cockpit:

    POST /api/events
    Authorization: Bearer $JARVIS_EVENTS_TOKEN
    {"source": "friday", "kind": "signal", "severity": "notice",
     "summary": "NVDA crossed scan threshold (0.82)", "data": {"ticker": "NVDA"}}

- `source` and `summary` are required.
- `severity`: `debug | info | notice | warning | error | critical` (anything
  else becomes `info`). The cockpit can filter to notice+, warning+, error.
- `data` is optional, shown expandable, clipped at ~4 KB.
- Ingest is disabled (503) until `JARVIS_EVENTS_TOKEN` is set in
  `~/jarvis-data/env/jarvis.env`.

One-line test from Guildenstern (reads the token from the env file):

    TOKEN=$(grep ^JARVIS_EVENTS_TOKEN= ~/jarvis-data/env/jarvis.env | cut -d= -f2) && curl -s -X POST http://100.113.110.44:8765/api/events -H "Authorization: Bearer $TOKEN" -H "Content-Type: application/json" -d '{"source":"test","kind":"ping","summary":"hello cockpit"}'

Same-host Python services can skip HTTP entirely and write the event log
directly if they share `JARVIS_DATA_DIR`:

    from orchestrator import event_log
    event_log.emit("friday", "agent", {"agent": "friday", "kind": "signal",
                   "severity": "notice", "summary": "..."})

## Scheduled jobs

Wrap any scheduled script in `event_log.job(...)` and it shows up as
start/end entries with duration and a summary line:

    with event_log.job("briefing", style="standard") as rec:
        ...
        rec["summary"] = "3 events, 2 headlines"

`scripts/trigger_briefing.py` and `scripts/run_sleep_cycle.py` already do.

Which timers the Scheduled pane lists is set by `JARVIS_COCKPIT_TIMERS`
(comma-separated globs, default `*.timer`, i.e. every user timer under
`ocelia`). **Run now** only starts the service behind a timer the pane
already lists.

## Security

The cockpit inherits the API's trust model: bound to the Tailscale IP, no
per-request auth. That now includes **run now** on scheduled jobs. Event
ingest is the exception and always requires the bearer token.
