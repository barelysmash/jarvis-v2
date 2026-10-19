# JARVIS HUD -- Changelog

Format: `vMAJOR.MINOR`. Bump **MINOR** for tweaks, additions, or layout
shifts. Bump **MAJOR** for redesigns. The wordmark on the HUD always
reflects the current version (`hud/src/components/JarvisHud/version.ts`).
Most recent at top.

> Entries before v2.16 were tracked in conversation rather than in-repo;
> `version.ts` referenced this file but it had never been created.

## v2.17 -- 2026-10-19

**Subagent constellation.**
The JAM star pattern, drawn literally. Every agent declared in
`config/agents.yaml` is a satellite node on an arc beneath the lens (r=272
on the SVG stage, between SPOTIFY and CONVERSATION, above the wordmark),
spoked to the iris. Declaration order reads left to right.

- Node states, all backed by real events: `offline` (dashed ring, dim --
  registry reported skipped/disabled/failed; caption says NO KEY / PARKED /
  FAILED), `idle` (steady; caption READY or `N CALLS`), `active` (spoke dash
  flows hub -> node, halo breathes; caption is the running tool with the
  agent prefix stripped), `error` (red, 4s hold, same as the iris).
- `mcp-stdio` agents (Sparrow) carry a dotted outer ring; in-process
  adapters don't.
- Hover a node for description, transport, load reason, and tool list.
- `prefers-reduced-motion` disables the flow/pulse animations.

Server side: `orchestrator/agents.py` now records per-agent load outcome
and the tools each registered (`registry_snapshot()`, `agent_for_tool()`);
the WS `snapshot` and `/api/status` carry `agents`; `tool` events carry an
`agent` field (null for JARVIS-native tools). The HUD falls back to
matching tool names against the snapshot when the field is absent, so
replayed events from the SQLite log still light the right node.

New: `hooks/useAgentActivity.ts`, `panels/AgentConstellation.tsx`,
`tests/test_agents_registry.py`.

## v2.16 -- 2026-07-28

**TOOL ACTIVITY is collapsible.**
The panel sits at `bottom-[42px]` and grows upward; at full height its top
edge reached y=435 on the 1180x664 stage, overlapping SCHEDULE (top y=358)
and, because it renders later in `index.tsx`, painting over it and stealing
its clicks. Collapsed it is a ~34px header strip with its top edge at y=588.

- Collapsed by default; auto-expands while tools fire, folds back after 4s.
- Click the header to pin open/shut; persisted in `localStorage`.
- Collapsed strip shows `N ACTIVE`, else the most recent tool name, else IDLE.
- Toggle calls `stopPropagation()` so it no longer triggers the shutter
  animation on the `.jhud` root.

**SCHEDULE height capped.**
Five events with location lines render ~253px, which would have re-crossed
into the collapsed strip. The body is now `max-h-[186px] overflow-y-auto`.

**Iris state colouring.**
The lens now reflects what JARVIS is actually doing. All six states are
derived client-side in `JarvisHud/hooks/useLensState.ts` from data already
on the bus -- no server change:

| state      | source                                            |
|------------|---------------------------------------------------|
| `offline`  | `status.online === false`                         |
| `error`    | newest tool event failed, 4s decay                |
| `tool`     | newest tool event is `running`                    |
| `speaking` | server `state`                                    |
| `thinking` | server `state`, 500ms minimum dwell               |
| `idle`     | server `state`                                    |

`listening` was dropped from the orb's state union: nothing can emit it, and
a state that never fires is the same lie as a fake fallback.

Implemented as a `hue-rotate`/`saturate` filter on a new `<g class="jhud-lens">`
wrapper, with per-state spin-rate multipliers. Delete the v2.16 block in
`JarvisHud.css` to revert.
