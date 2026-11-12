# Jarvis v2 — Task Board

*Updated 2026-11-10 (HUD v2.18) · Architecture source of truth: [JAM](https://github.com/barelysmash/JAM)*

## Ecosystem (from JAM)

```
                     Barry
                       │
                    JARVIS ── Executive Orchestrator (this repo)
                       │
        ┌──────────────┴──────────────┐
     Atlas                         Friday
Operational Intelligence      Trading Intelligence
        │                             │
 RestaurantOS                   BarelyTrade /
 (Fonda San Miguel)             BarelySwingTrade
```

> Intelligence Systems produce decisions. Applications present decisions.
> JARVIS coordinates decisions. — JAM Core Principle

## Up next — HUD / UI
- [x] Expand chat window (more real estate for conversation pane) — DONE 2026-07-12 (c333842)
- [ ] Response pop-up/modal — surface JARVIS replies in a readable overlay
- [x] Shrink tool activity panel to accommodate the above — DONE v2.16 (collapsible ToolFeedPanel)
- [x] Status color system — thinking / responding / idle / very idle (port from JarvisV1) — DONE v2.16 (iris state colouring)
- [x] Cockpit: second-screen full-detail view — DONE v2.17 (docs/COCKPIT.md)
- [x] Subagent constellation under the lens — DONE v2.18
- [ ] Redo the core JARVIS animation (the orb itself; v2.16/v2.18 only added
      state colouring and the constellation)
- [ ] Phone test for the VoiceOrb / AmbientLayer crash (WebGL / memory pressure
      suspected on mobile; both components untouched since the July import)
- [ ] Very-idle arcade mode — self-playing tic-tac-toe / Tetris when nothing's happening.
      Pure-frontend minimax self-play to start; any input or ws activity ends the game.

## Roadmap — Autonomy (order matters)
- [x] `brain.py` prerequisite patch (repair / trim / armor) — DONE 2026-07-08
- [ ] Build out **Atlas** — https://github.com/barelysmash/atlas
      Decision-infrastructure engine + restaurant plugin + RestaurantOS.
      First deployment: Fonda San Miguel. (5 open issues on the repo.)
- [ ] Build out **Friday** — Trading Intelligence subagent overseeing
      BarelyTrade / BarelySwingTrade / BarelyDayTrade engines.
- [x] Agents registry (`config/agents.yaml`, adapter + mcp-stdio transports) —
      DONE 2026-09-10 (1b70c43). Registered: tavily, barelyswing, friday, muse,
      sparrow.
- [x] Register **Atlas** in `agents.yaml` — DONE 2026-11-11: `atlas` adapter
      over Atlas's RestaurantOS peer API (127.0.0.1:8790). Read-only, relative
      figures only; two tools, `atlas_operating_summary` and `atlas_freshness`.
      (star-pattern rule: no new domain skills inside Jarvis itself)
- [x] Atlas staleness alarm in the morning briefing — DONE 2026-11-11
      (alarm past `ATLAS_STALE_DAYS`, default 3, or when Atlas does not answer)
- [ ] Atlas: emit JAM Decisions from the Fonda ledger and expose them (the
      peer API serves period comparisons only; atlas-core's decision engine is
      not run on nightly data yet)
- [ ] Task-specific agents under JARVIS as orchestrator (beyond the five above)
- [ ] Consigliere: an advisor for JARVIS that runs on a later model
- [ ] Expand tools
      - [x] Spotify by voice — DONE 2026-11-11: six `spotify_` tools over the
        client the HUD miniplayer uses (now playing, transport, search-and-play,
        volume, devices, transfer)
      - [ ] Reminders and timers
      - [ ] Memory, routines, weather and system status as callable tools
- [ ] **Loop automation** — Jarvis runs tasks unattended:
      - Session isolation per routine run (fresh conversation, never the shared
        singleton — non-negotiable for unattended operation). Shell and goal
        routines already run with no brain and no shared session; a
        brain-driven routine kind would still need it.
      - [x] GoalRunner — built per docs/adr/ADR-0002-goal-runner.md
        (`orchestrator/goal_runner.py`, `kind: goal`) — DONE 2026-10-21
      - [x] Probe `jarvis-memory-mypy` on guildenstern, then flip
        `enabled: true` in routines.yaml for the first scheduled run — DONE
        2026-10-22; first run went green 2026-10-24 and landed as PR #22
        (semantic.py strict-clean). Now a weekly guard (already_green).
      - [x] `jarvis-brain-mypy`: second goal routine, Sat 07:00 — the 122
        brain.py union-attr findings, condition = mypy --strict orchestrator
        + the brain tests via `scripts/goal_check.py` (own goal_runner.py debt
        fixed by hand first, TypeGuards) — DONE 2026-10-24. Probe, then read
        ~/jarvis-data/routines/jarvis-brain-mypy/runs/ after the first run.
      - [x] Loop 4: backlog drain — docs/adr/ADR-0003-backlog-drain.md (Accepted).
        `config/backlog.yaml` of goal specs → `backlog-drain` routine, Sun 06:00,
        one entry per fire; TASKS.md is never parsed, but a green run ticks the
        entry's `task:` line below — DONE 2026-10-25
      - [x] `server/api.py` strict (4 findings) — backlog entry server-api-mypy — DONE 2026-11-03 (backlog-drain)
      - [ ] `load_dotenv` conditional-def strict findings in main.py / scripts — backlog entry dotenv-shim-mypy
      - [x] `routines.yaml` scheduled via systemd timers — DONE 2026-10-20
        (`config/routines.yaml` → generated `jarvis-routine-*` units,
        `scripts/run_routine.py`, `docs/ROUTINES.md`). First routine:
        `conformance-sweep`, nightly `jam validate` across atlas + jarvis-v2
        with drift events into the cockpit.
      - [x] Contract propagation: JAM release → `chore/jam-vX.Y.Z` branch +
        patch prepared in every conforming repo behind it (stops before
        push) — DONE 2026-10-20, in the sweep; see docs/ROUTINES.md
      - [ ] Decide whether the sweep gets write creds on guildenstern
        (`--push`: pushes the branch and opens the PR; merge stays manual)
      - [x] `/api/events` endpoint for proactive triggers (cockpit, v2.17)
      - [ ] Feed `/api/events` from Friday/BarelyTrade Events and OpenClaw

## Quality of life
- [x] ssh-agent setup on rosencrantz (kill the 5x passphrase prompts per deploy) — DONE 2026-10-21 (~/.bashrc agent block + AddKeysToAgent in ~/.ssh/config; ControlMaster is unreliable on MSYS)
- [x] Find where uvicorn logs actually go — `~/jarvis-data/logs/api.log` and
      `api.err.log`; unit runs with `PYTHONUNBUFFERED=1` since b8b8d77 (2026-09-11)
- [ ] Rename `tools/integrations/calendar.py` → `gcal.py` (stdlib shadow;
      caused the reauth-script import crash). Touches imports in tools.py.
- [x] `.gitattributes` with `* text=auto eol=lf` (silence CRLF warnings) — DONE 2026-10-21 (#18)
- [ ] Barge-in (`BARGE_ENABLED`, off by default) — blocked on separate mic and
      speaker hardware; one Bluetooth speaker as both self-triggers
- [ ] Market data collector: consolidate from rosencrantz to guildenstern
      (yfinance is rate-limited from the Azure IP; not urgent)
- [ ] Sparrow: Openverse calls time out from guildenstern — register for
      Openverse API credentials and/or configure Unsplash / Pexels keys
- [ ] Sparrow: migrate to mcp 2.x, then drop the `mcp>=1.0,<2` pin in
      requirements.txt
- [ ] SPA catch-all for StaticFiles if HUD ever gets client-side routes
- [x] JAM: fill in Standards "coming soon" sections — DONE 2026-10-26: JAM #34
      (Unattended Automation) and #35 (Deployment, Secrets, Agent Registration),
      written from what jarvis-v2 and Atlas already do. Glossary has no
      "coming soon" left; RFCs and Roadmap in the JAM README still are.

## Done (July 7–8 session)
- [x] Google OAuth reauth — fresh token, `google_reauth.py` committed
- [x] OAuth consent screen published to Production (7-day token expiry eliminated)
- [x] HUD static-served from uvicorn (`StaticFiles` mount, same-origin ws)
- [x] `crypto.randomUUID` insecure-context crash — `uid()` fallback
- [x] `deploy.sh` — build/push/pull/dist/restart/health-check, `--no-build` flag
- [x] GitHub deploy key on guildenstern (read-only pull)
- [x] `brain.py` patch — `_repair_dangling_tool_use`, `_trim_history`, armored tool loop
