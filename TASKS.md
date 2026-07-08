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
      - GoalRunner with evaluator model
      - `routines.yaml` scheduled via systemd timers
      - `/api/events` endpoint for proactive triggers
        (fed by Friday/BarelyTrade Events and OpenClaw)

## Quality of life
- [ ] ssh-agent setup on rosencrantz (kill the 5x passphrase prompts per deploy)
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
