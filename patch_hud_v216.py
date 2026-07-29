#!/usr/bin/env python3
"""
patch_hud_v216.py  --  JARVIS HUD  v2.15 -> v2.16

WHAT THIS DOES
  1. TOOL ACTIVITY becomes collapsible (collapsed by default, auto-expands
     while tools are firing, click the header to pin it open). This frees the
     vertical space it was stealing from SCHEDULE.
  2. SCHEDULE body gets a hard max-height so a busy calendar day can never
     grow back down into the collapsed tool-feed strip.
  3. The centre iris gains state colouring, derived entirely from data
     already on the WebSocket bus. NO SERVER CHANGE REQUIRED.

WHERE TO RUN
  rosencrantz, Git Bash, as bgama, from the repo root:

      cd ~/jarvis-v2
      python patch_hud_v216.py

ANCHORED-PATCH CONTRACT
  * Every anchor is verified to appear EXACTLY ONCE before anything is written.
  * A timestamped .bak is taken immediately before each file is modified.
  * On any mismatch the script aborts and NO file is touched.
"""

from __future__ import annotations

import shutil
import sys
from datetime import datetime
from pathlib import Path

STAMP = datetime.now().strftime("%Y%m%d-%H%M%S")
ROOT = Path(__file__).resolve().parent
HUD = ROOT / "hud" / "src" / "components"
JH = HUD / "JarvisHud"


# ---------------------------------------------------------------------------
# NEW FILE CONTENT
# ---------------------------------------------------------------------------

USE_LENS_STATE = '''import { useEffect, useState } from 'react';
import type { JarvisState } from '../../../hooks/useJarvisState';

export type LensState =
  | 'offline'
  | 'error'
  | 'tool'
  | 'thinking'
  | 'speaking'
  | 'idle';

/** Minimum time `thinking` is held, so a fast turn still reads as thinking. */
const THINK_DWELL_MS = 500;
/** How long the iris stays red after a tool reports failure. */
const ERROR_HOLD_MS = 4000;

/**
 * useLensState -- derives the iris state from data already on the bus.
 *
 * The server only ever emits three states (thinking / speaking / idle, see
 * server/api.py). Everything else here is derived client-side from the tool
 * feed and the socket status, so no backend change is needed and no state is
 * ever shown that isn't backed by a real event.
 *
 * `listening` is deliberately absent: nothing can emit it (the voice client
 * doesn't talk to the bus), and a state that can never fire is the same lie
 * as a fake fallback.
 */
export function useLensState(j: JarvisState): LensState {
  const last = j.toolEvents.length
    ? j.toolEvents[j.toolEvents.length - 1]
    : null;
  const lastId = last?.id ?? null;
  const lastStatus = last?.status ?? null;

  const [errorActive, setErrorActive] = useState(false);
  const [dwelling, setDwelling] = useState(false);

  // Red decay after a failed tool call.
  useEffect(() => {
    if (lastStatus !== 'error') return;
    setErrorActive(true);
    const t = window.setTimeout(() => setErrorActive(false), ERROR_HOLD_MS);
    return () => window.clearTimeout(t);
  }, [lastId, lastStatus]);

  // Minimum dwell on thinking.
  useEffect(() => {
    if (j.state !== 'thinking') return;
    setDwelling(true);
    const t = window.setTimeout(() => setDwelling(false), THINK_DWELL_MS);
    return () => window.clearTimeout(t);
  }, [j.state]);

  // Priority order matters. `offline` is the only honest state when the
  // socket is down, and a fresh failure outranks whatever the brain last said.
  if (!j.status.online) return 'offline';
  if (errorActive) return 'error';
  if (lastStatus === 'running') return 'tool';
  if (j.state === 'speaking') return 'speaking';
  if (j.state === 'thinking' || dwelling) return 'thinking';
  return 'idle';
}
'''


TOOL_FEED = '''import { useEffect, useRef, useState } from "react";
import type { MouseEvent as ReactMouseEvent } from "react";
import { motion, AnimatePresence } from "framer-motion";
import {
  CheckCircle2,
  ChevronDown,
  ChevronRight,
  Loader2,
  XCircle,
} from "lucide-react";
import type { ToolEvent } from "../hooks/useJarvisState";

const PIN_KEY = "jhud.toolfeed.pinned";
/** How long the feed stays open after the most recent tool event. */
const FRESH_MS = 4000;

/**
 * ToolFeed -- collapsible activity panel.
 *
 * Collapsed it is a ~34px header strip, which keeps it clear of the SCHEDULE
 * panel above it. It auto-expands while tools are firing, then folds itself
 * away again. Clicking the header pins it open (or shut) and that choice
 * persists across HUD reloads.
 */
export function ToolFeed({ events }: { events: ToolEvent[] }) {
  const [pinned, setPinned] = useState<boolean>(() => {
    try {
      return window.localStorage.getItem(PIN_KEY) === "1";
    } catch {
      return false;
    }
  });
  const [fresh, setFresh] = useState(false);

  const last = events.length ? events[events.length - 1] : null;
  const lastId = last?.id ?? null;
  const seen = useRef<string | null>(null);

  useEffect(() => {
    if (!lastId || seen.current === lastId) return;
    seen.current = lastId;
    setFresh(true);
    const t = window.setTimeout(() => setFresh(false), FRESH_MS);
    return () => window.clearTimeout(t);
  }, [lastId]);

  const expanded = pinned || fresh;
  const running = events.filter((e) => e.status === "running").length;

  const toggle = (e: ReactMouseEvent) => {
    // The .jhud root carries an onClick that fires the shutter animation.
    // Without this the panel would fire the shutter on every toggle.
    e.stopPropagation();
    const next = !pinned;
    setPinned(next);
    try {
      window.localStorage.setItem(PIN_KEY, next ? "1" : "0");
    } catch {
      /* private mode -- the pin just won't persist */
    }
  };

  return (
    <div className="bg-black/30 backdrop-blur-sm border border-cyan-500/20 rounded-sm">
      <button
        type="button"
        onClick={toggle}
        aria-expanded={expanded}
        className={`w-full px-4 py-2 flex items-center justify-between text-left
                    focus:outline-none focus-visible:ring-1 focus-visible:ring-cyan-400/60
                    ${expanded ? "border-b border-cyan-500/20" : ""}`}
      >
        <span className="flex items-center gap-1.5 text-cyan-400 text-xs font-mono tracking-[0.2em]">
          {expanded ? <ChevronDown size={11} /> : <ChevronRight size={11} />}
          TOOL ACTIVITY
        </span>
        <span className="text-cyan-700 text-[10px] font-mono truncate max-w-[130px]">
          {running > 0 ? `${running} ACTIVE` : last ? last.name : "IDLE"}
        </span>
      </button>

      <AnimatePresence initial={false}>
        {expanded && (
          <motion.div
            key="feed"
            initial={{ height: 0, opacity: 0 }}
            animate={{ height: "auto", opacity: 1 }}
            exit={{ height: 0, opacity: 0 }}
            transition={{ duration: 0.18, ease: "easeOut" }}
            className="overflow-hidden"
          >
            <div className="p-3 space-y-1.5 max-h-32 overflow-y-auto">
              {events.length === 0 && (
                <div className="text-cyan-700 text-[10px] font-mono italic">
                  No recent activity
                </div>
              )}
              <AnimatePresence>
                {events
                  .slice(-8)
                  .reverse()
                  .map((evt) => (
                    <motion.div
                      key={evt.id}
                      initial={{ opacity: 0, height: 0 }}
                      animate={{ opacity: 1, height: "auto" }}
                      exit={{ opacity: 0, height: 0 }}
                      className="flex items-center gap-2 text-xs font-mono"
                    >
                      <StatusIcon status={evt.status} />
                      <span className="text-cyan-300">{evt.name}</span>
                      <span className="text-cyan-700 truncate flex-1">
                        {Object.entries(evt.args)
                          .map(([k, v]) => `${k}=${v}`)
                          .join(" ")}
                      </span>
                      <span className="text-cyan-800 text-[10px]">
                        {evt.timestamp}
                      </span>
                    </motion.div>
                  ))}
              </AnimatePresence>
            </div>
          </motion.div>
        )}
      </AnimatePresence>
    </div>
  );
}

function StatusIcon({ status }: { status: ToolEvent["status"] }) {
  if (status === "running")
    return <Loader2 size={12} className="text-cyan-400 animate-spin" />;
  if (status === "success")
    return <CheckCircle2 size={12} className="text-green-400" />;
  return <XCircle size={12} className="text-red-400" />;
}
'''


CSS_BLOCK = '''
/* ===== v2.16 -- lens state tinting =====
   The iris colour is derived client-side from data already on the bus
   (see JarvisHud/hooks/useLensState.ts) and surfaced as data-lens on .jhud.

   A hue rotation is used instead of recolouring the ~40 hardcoded stops in
   Lens.tsx: it shifts every chromatic element coherently while leaving the
   white speculars and the black barrel untouched, and `filter` interpolates
   smoothly so transitions crossfade rather than snap.

   TO REVERT the colouring entirely, delete this block. Lens.tsx keeps its
   wrapper <g> harmlessly. */

.jhud .jhud-lens {
  filter: hue-rotate(var(--lens-hue, 0deg))
          saturate(var(--lens-sat, 1))
          brightness(var(--lens-bri, 1));
  transition: filter 320ms linear;
}

/* Spin rate scales with state. Larger multiplier = slower. */
.jhud .jhud-lens .r-slow     { animation-duration: calc(70s * var(--lens-spin, 1)); }
.jhud .jhud-lens .r-med-cc   { animation-duration: calc(30s * var(--lens-spin, 1)); }
.jhud .jhud-lens .r-fast     { animation-duration: calc(14s * var(--lens-spin, 1)); }
.jhud .jhud-lens .r-vfast-cc { animation-duration: calc( 9s * var(--lens-spin, 1)); }

/* Base cyan (#00e5ff) sits at ~186deg; offsets below are relative to that.
     thinking -> violet ~265    tool     -> green ~150
     speaking -> amber  ~40     error    -> red   ~0                        */
.jhud[data-lens="idle"]     { --lens-hue:    0deg; --lens-spin: 1;    }
.jhud[data-lens="thinking"] { --lens-hue:   78deg; --lens-spin: 0.55; }
.jhud[data-lens="tool"]     { --lens-hue:  -37deg; --lens-spin: 0.70; }
.jhud[data-lens="speaking"] { --lens-hue:  213deg; --lens-spin: 0.85; --lens-bri: 1.10; }
.jhud[data-lens="error"]    { --lens-hue:  173deg; --lens-spin: 1.30; --lens-sat: 1.60; }
.jhud[data-lens="offline"]  { --lens-hue:    0deg; --lens-spin: 4;    --lens-sat: 0.12; --lens-bri: 0.55; }

@media (prefers-reduced-motion: reduce) {
  .jhud .jhud-lens { transition: none; }
}
'''


CHANGELOG = f'''# JARVIS HUD -- Changelog

Format: `vMAJOR.MINOR`. Bump **MINOR** for tweaks, additions, or layout
shifts. Bump **MAJOR** for redesigns. The wordmark on the HUD always
reflects the current version (`hud/src/components/JarvisHud/version.ts`).
Most recent at top.

> Entries before v2.16 were tracked in conversation rather than in-repo;
> `version.ts` referenced this file but it had never been created.

## v2.16 -- {datetime.now().strftime('%Y-%m-%d')}

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
'''


# ---------------------------------------------------------------------------
# ANCHORED EDITS  --  (path, [(old, new), ...])
# ---------------------------------------------------------------------------

EDITS: list[tuple[Path, list[tuple[str, str]]]] = [
    # ---- Lens.tsx : single wrapper element, nothing else touched ----------
    (
        JH / "Lens.tsx",
        [
            (
                "  return (\n    <>\n      <defs>",
                '  return (\n    <g className="jhud-lens">\n      <defs>',
            ),
            (
                "      </g>\n    </>\n  );\n}",
                "      </g>\n    </g>\n  );\n}",
            ),
        ],
    ),
    # ---- index.tsx : import hook, call it, surface it as data-lens -------
    (
        JH / "index.tsx",
        [
            (
                "import { useViewportScale } from './hooks/useViewportScale';",
                "import { useViewportScale } from './hooks/useViewportScale';\n"
                "import { useLensState } from './hooks/useLensState';",
            ),
            (
                "  const j = useJarvisState();",
                "  const j = useJarvisState();\n"
                "  const lens = useLensState(j);",
            ),
            (
                "        data-version={JARVIS_VERSION}",
                "        data-version={JARVIS_VERSION}\n"
                "        data-lens={lens}",
            ),
        ],
    ),
    # ---- ScheduleWidget.tsx : bound the body height ----------------------
    (
        HUD / "ScheduleWidget.tsx",
        [
            (
                '      <div className="p-4 text-xs font-mono">',
                '      <div className="p-4 text-xs font-mono max-h-[186px] overflow-y-auto">',
            ),
        ],
    ),
    # ---- version.ts -------------------------------------------------------
    (
        JH / "version.ts",
        [
            ("export const JARVIS_VERSION = 'v2.15';",
             "export const JARVIS_VERSION = 'v2.16';"),
        ],
    ),
    # ---- JarvisHud.css : append the state block ---------------------------
    (
        JH / "JarvisHud.css",
        [
            (
                "@keyframes jhud-scroll-left {\n"
                "  from { transform: translateX(0);    }\n"
                "  to   { transform: translateX(-50%); }\n"
                "}",
                "@keyframes jhud-scroll-left {\n"
                "  from { transform: translateX(0);    }\n"
                "  to   { transform: translateX(-50%); }\n"
                "}\n" + CSS_BLOCK,
            ),
        ],
    ),
]

# Files written wholesale. Each carries a sentinel that must be present in the
# current file (or None if the file is expected not to exist yet).
REWRITES: list[tuple[Path, str, str | None]] = [
    (HUD / "ToolFeed.tsx", TOOL_FEED,
     "export function ToolFeed({ events }: { events: ToolEvent[] }) {"),
    (JH / "hooks" / "useLensState.ts", USE_LENS_STATE, None),
    (ROOT / "CHANGELOG.md", CHANGELOG, None),
]


# ---------------------------------------------------------------------------
# PHASE 1 -- VALIDATE EVERYTHING, WRITE NOTHING
# ---------------------------------------------------------------------------

def fail(msg: str) -> None:
    print(f"\n  ABORT: {msg}")
    print("  No files were modified.\n")
    sys.exit(1)


print(f"\nJARVIS HUD v2.15 -> v2.16")
print(f"repo root: {ROOT}")
print("\n[1/3] validating anchors ...")

if not (ROOT / "hud" / "src").is_dir():
    fail(f"{ROOT}/hud/src not found -- run this from the jarvis-v2 repo root")

originals: dict[Path, str] = {}

for path, pairs in EDITS:
    if not path.is_file():
        fail(f"missing file: {path}")
    text = path.read_text(encoding="utf-8")
    originals[path] = text
    for old, new in pairs:
        n = text.count(old)
        if n != 1:
            head = old.splitlines()[0][:70]
            fail(f"{path.name}: anchor found {n}x (need exactly 1) -> {head!r}")
        if new in text:
            fail(f"{path.name}: replacement already present -- already patched?")
    print(f"      ok  {path.relative_to(ROOT)}  ({len(pairs)} anchor(s))")

for path, _content, sentinel in REWRITES:
    if sentinel is None:
        if path.exists():
            fail(f"{path.relative_to(ROOT)} already exists -- refusing to clobber")
        print(f"      ok  {path.relative_to(ROOT)}  (new file)")
    else:
        if not path.is_file():
            fail(f"missing file: {path}")
        text = path.read_text(encoding="utf-8")
        if text.count(sentinel) != 1:
            fail(f"{path.name}: sentinel not found exactly once -- unexpected content")
        originals[path] = text
        print(f"      ok  {path.relative_to(ROOT)}  (full rewrite)")

print("      all anchors validated.")


# ---------------------------------------------------------------------------
# PHASE 2 -- BACKUP
# ---------------------------------------------------------------------------

print("\n[2/3] backing up ...")
for path in originals:
    bak = path.with_suffix(path.suffix + f".{STAMP}.bak")
    shutil.copy2(path, bak)
    print(f"      {bak.name}")


# ---------------------------------------------------------------------------
# PHASE 3 -- WRITE
# ---------------------------------------------------------------------------

print("\n[3/3] writing ...")

for path, pairs in EDITS:
    text = originals[path]
    for old, new in pairs:
        text = text.replace(old, new, 1)
    path.write_text(text, encoding="utf-8")
    print(f"      patched  {path.relative_to(ROOT)}")

for path, content, _sentinel in REWRITES:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content, encoding="utf-8")
    verb = "rewrote " if path in originals else "created "
    print(f"      {verb} {path.relative_to(ROOT)}")

print(f"""
Done. Backups carry the suffix .{STAMP}.bak

Next, on rosencrantz:

    cd ~/jarvis-v2/hud
    npx tsc --noEmit          # typecheck
    npm run build             # must succeed before deploying
    npm run dev               # eyeball it locally first

Then deploy through your usual path (build -> tar dist -> scp via
barelysmash -> remote dist swap -> curl verify).
""")
