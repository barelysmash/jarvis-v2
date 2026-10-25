# Jarvis v2 — Task Board

*Updated 2026-07-08 · Architecture source of truth: [JAM](https://github.com/barelysmash/JAM)*

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
- [ ] Expand chat window (more real estate for conversation pane)
- [ ] Response pop-up/modal — surface JARVIS replies in a readable overlay
- [ ] Shrink tool activity panel to accommodate the above
- [ ] Status color system — thinking / responding / idle / very idle (port from JarvisV1)
- [ ] Very-idle arcade mode — self-playing tic-tac-toe / Tetris when nothing's happening.
      Pure-frontend minimax self-play to start; any input or ws activity ends the game.

## Roadmap — Autonomy (order matters)
- [x] `brain.py` prerequisite patch (repair / trim / armor) — DONE 2026-07-08
- [ ] Build out **Atlas** — https://github.com/barelysmash/atlas
      Decision-infrastructure engine + restaurant plugin + RestaurantOS.
      First deployment: Fonda San Miguel. (5 open issues on the repo.)
- [ ] Build out **Friday** — Trading Intelligence subagent overseeing
      BarelyTrade / BarelySwingTrade / BarelyDayTrade engines.
- [ ] Register Atlas + Friday as MCP-server workers in `agents.yaml`
      (star-pattern rule: no new domain skills inside Jarvis itself)
- [ ] **Loop automation** — Jarvis runs tasks unattended:
      - Session isolation per routine run (fresh conversation, never the shared
        singleton — non-negotiable for unattended operation)
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
      - [ ] Remaining strict debt for later goal routines: `load_dotenv`
        conditional-def in main.py / scripts (1 each), server/api.py (4)
      - [ ] Loop 4: backlog drain — designed in docs/adr/ADR-0003-backlog-drain.md
        (`config/backlog.yaml` of goal specs → `backlog-drain` routine, Sun 06:00,
        one entry per fire; TASKS.md is never parsed). Build next.
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
- [ ] Find where uvicorn logs actually go (not journald; check ~/jarvis-data/logs/)
- [ ] Rename `tools/integrations/calendar.py` → `gcal.py` (stdlib shadow;
      caused the reauth-script import crash). Touches imports in tools.py.
- [ ] `.gitattributes` with `* text=auto eol=lf` (silence CRLF warnings)
- [ ] SPA catch-all for StaticFiles if HUD ever gets client-side routes
- [ ] JAM: fill in Standards + Glossary "coming soon" sections as conventions
      solidify (secrets pattern, deploy pattern, agent registration are all
      already de-facto standards — write them down)

## Done (July 7–8 session)
- [x] Google OAuth reauth — fresh token, `google_reauth.py` committed
- [x] OAuth consent screen published to Production (7-day token expiry eliminated)
- [x] HUD static-served from uvicorn (`StaticFiles` mount, same-origin ws)
- [x] `crypto.randomUUID` insecure-context crash — `uid()` fallback
- [x] `deploy.sh` — build/push/pull/dist/restart/health-check, `--no-build` flag
- [x] GitHub deploy key on guildenstern (read-only pull)
- [x] `brain.py` patch — `_repair_dangling_tool_use`, `_trim_history`, armored tool loop
