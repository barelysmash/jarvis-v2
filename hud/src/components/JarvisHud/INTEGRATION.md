# JARVIS HUD v2.2 — React Port Integration

The `JarvisHud/` directory drops into `~/jarvis/hud/src/components/`. Then in
`App.tsx`:

```tsx
import { JarvisHud } from './components/JarvisHud';

export default function App() {
  return <JarvisHud />;
}
```

That replaces the current `VoiceOrb` / `StatusBar` / `AmbientLayer` composition.
The existing components don't go anywhere — they get wrapped by panels inside
`JarvisHud` (in batch 2).

## What's in batch 1 (this commit)

```
JarvisHud/
├── index.tsx              ← top-level layout, owns shutter state
├── Lens.tsx               ← the amorphous lens centerpiece
├── JarvisHud.css          ← keyframes + SVG filter animations
├── version.ts             ← JARVIS_VERSION = 'v2.2'
├── effects/
│   ├── Scanline.tsx
│   ├── Vignette.tsx
│   └── Flash.tsx
├── hooks/
│   ├── useClock.ts        ← 1s-tick date/time fields
│   └── useShutter.ts      ← click-to-fire state machine + SMIL trigger
└── panels/
    ├── DateClock.tsx      ← big enlarged date/time SVG ring
    ├── DateTicker.tsx     ← top day-of-month strip (HTML overlay)
    └── Wordmark.tsx       ← JARVIS V2.2 + subtitle
```

With just batch 1 in place, the HUD builds and shows the lens centerpiece,
the date ring on the left, the date-strip across the top, and the wordmark
at the bottom. All other regions (markets, schedule, chat, comm, weather,
forecast, news, stocks) are stubbed out — see the `TODO batch 2` comment
in `index.tsx`.

## Batch 2 — coming next

**Self-contained new panels** (no existing-component dependency):
- `panels/StocksTicker.tsx` — scrolling top-row market ticker
- `panels/NewsTicker.tsx`   — scrolling bottom-row AP wire
- `panels/Weather.tsx`      — current temp + inline moon icon
- `panels/Forecast.tsx`     — 4-day horizontal strip
- `panels/MarketCharts.tsx` — 5-index bar charts (S&P / NDX / DOW / BTC / NKY)
- `panels/CPURamRings.tsx`  — small SVG rings + live values
- `panels/DiskEnergy.tsx`   — disk + energy rings

**Wrappers around existing components** (need API shapes from you):
- `panels/Schedule.tsx`     — wraps `ScheduleWidget`
- `panels/Chat.tsx`         — wraps `ConversationLog` + `TextInput`
- `panels/ToolFeedPanel.tsx`— wraps `ToolFeed`

To unblock the wrappers, paste the contents of `ScheduleWidget.tsx`,
`ConversationLog.tsx`, `TextInput.tsx`, `ToolFeed.tsx`, and
`hooks/useJarvisState.ts` and I'll write them to match.

## Audio reactivity (Lens)

The Lens has a CSS-driven idle breathe cycle. To wire `useAudioLevel` in:

```tsx
const level = useAudioLevel();           // 0..1
<Lens bladeFireRef={...} audioLevel={level} />
```

I'll add the `audioLevel` prop in batch 2 (or as soon as you confirm the
hook's return shape — is it `number` or `{level: number, peak: number}`?).
The component reads it and modulates the dilate scale via an inline
`style={{ '--audio': level }}` custom property; the keyframes already
multiply by it.

## Deploy path (after batches done)

The hud is its own Vite project at `~/jarvis/hud`. Deploy is:

```bash
# rosencrantz (Git Bash) — after fixing the ssh config alias
scp -r JarvisHud ocelia@guildenstern:/tmp/
ssh ocelia@guildenstern '
  set -e
  cd ~/jarvis/hud
  cp -a src/components/JarvisHud src/components/JarvisHud.bak.$(date -u +%Y%m%dT%H%M%SZ) 2>/dev/null || true
  rm -rf src/components/JarvisHud
  mv /tmp/JarvisHud src/components/
  # update App.tsx to use it (one-line change — manual)
  npm install   # only if package.json deps changed (it shouldn't for batch 1)
  npm run build # produces dist/
  echo "BUILD OK — restart whatever serves dist/, or relaunch electron"
'
```

`jarvis-api.service` does NOT need a restart — FastAPI doesn't serve the
HUD bundle (confirmed empty `StaticFiles|mount` grep earlier).

## Rollback

Per release: just flip `~/jarvis` back to `~/jarvis-previous`:

```bash
ssh ocelia@guildenstern '
  ln -sfn jarvis-releases/jarvis-20260508-134641 ~/jarvis
  systemctl --user restart jarvis-api.service   # not strictly required for HUD-only
'
```

Per HUD bundle: replace `src/components/JarvisHud` with the `.bak.*`
directory captured during deploy, then `npm run build`.
