# Jarvis v2 — Task Board

*Updated 2026-07-08*

## Up next — HUD / UI
- [ ] Expand chat window (more real estate for conversation pane)
- [ ] Response pop-up/modal — surface JARVIS replies in a readable overlay
- [ ] Shrink tool activity panel to accommodate the above
- [ ] Status color system — visual state for thinking / responding / idle / very idle (port from JarvisV1)
- [ ] Very-idle arcade mode — self-playing tic-tac-toe / Tetris / similar when nothing's happening.
      Start pure-frontend (minimax self-play), zero API cost. Any input or ws activity kills the game.

## Roadmap — Autonomy
- [ ] **Loop automation** (after Atlas + Friday are built): Jarvis runs its own tasks on loops.
      Prior design (Claude Code loop primitives): session isolation, GoalRunner with evaluator
      model, `routines.yaml` scheduled via systemd timers, `/api/events` endpoint for proactive
      triggers fed by BarelyTrade Events and OpenClaw.
- [ ] Build **Atlas** (define role/domain — new capability = new MCP-server agent per the
      orchestration rule: no new domain skills inside Jarvis itself; register in `agents.yaml`)
- [ ] Build **Friday** (same rule applies)

## Quality of life
- [ ] ssh-agent setup on rosencrantz (kill the 5x passphrase prompts per deploy)
- [ ] Find where uvicorn logs actually go (not in journald; no ~/jarvis/logs/)
- [ ] Rename `tools/integrations/calendar.py` → `gcal.py` (stdlib shadow; caused the
      reauth-script import crash). Touches imports in tools.py.
- [ ] `.gitattributes` with `* text=auto eol=lf` (silence CRLF warnings)
- [ ] SPA catch-all for StaticFiles if HUD ever gets client-side routes (hard refresh
      on a route would 404 with plain StaticFiles)

## Done (July 7–8 session)
- [x] Google OAuth reauth — fresh token, `google_reauth.py` committed
- [x] OAuth consent screen published to Production (7-day token expiry eliminated)
- [x] HUD static-served from uvicorn (`StaticFiles` mount, same-origin ws)
- [x] `crypto.randomUUID` insecure-context crash — `uid()` fallback
- [x] `deploy.sh` — build/push/pull/dist/restart/health-check, `--no-build` flag
- [x] GitHub deploy key on guildenstern (read-only pull)
- [x] `brain.py` patch — `_repair_dangling_tool_use`, `_trim_history`, armored tool loop
