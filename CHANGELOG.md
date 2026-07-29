# JARVIS HUD -- Changelog

Format: `vMAJOR.MINOR`. Bump **MINOR** for tweaks, additions, or layout
shifts. Bump **MAJOR** for redesigns. The wordmark on the HUD always
reflects the current version (`hud/src/components/JarvisHud/version.ts`).
Most recent at top.

> Entries before v2.16 were tracked in conversation rather than in-repo;
> `version.ts` referenced this file but it had never been created.

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
