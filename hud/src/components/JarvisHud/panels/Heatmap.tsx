import { useState } from 'react';

interface Tile {
  t: string;
  tier: string;
  sector: string;
  pct: number;
  cap: number;
  capSrc: string;
}
interface HeatmapData {
  tiles?: Tile[];
}

// Finviz-style diverging scale — mirrors the standalone swing-heatmap repo.
const STOPS: [number, string][] = [
  [0.0, '#f63538'], [0.25, '#bf4045'], [0.45, '#8b444e'],
  [0.5, '#414554'], [0.55, '#35764e'], [0.75, '#2f9e4f'], [1.0, '#30cc5a'],
];
const SCALE = 2; // % change at full color saturation

function lerpHex(a: string, b: string, t: number): string {
  const pa = [1, 3, 5].map((i) => parseInt(a.slice(i, i + 2), 16));
  const pb = [1, 3, 5].map((i) => parseInt(b.slice(i, i + 2), 16));
  return (
    '#' +
    pa
      .map((v, i) =>
        Math.round(v + (pb[i] - v) * t)
          .toString(16)
          .padStart(2, '0')
      )
      .join('')
  );
}

function pctColor(pct: number): string {
  const x = Math.max(0, Math.min(1, (pct / SCALE + 1) / 2));
  for (let i = 1; i < STOPS.length; i++) {
    if (x <= STOPS[i][0]) {
      const [x0, c0] = STOPS[i - 1];
      const [x1, c1] = STOPS[i];
      return lerpHex(c0, c1, (x - x0) / (x1 - x0 || 1));
    }
  }
  return STOPS[STOPS.length - 1][1];
}

interface Rect {
  x: number;
  y: number;
  w: number;
  h: number;
}

function worstRatio(row: number[], sum: number, short: number): number {
  const max = Math.max(...row);
  const min = Math.min(...row);
  const s2 = sum * sum;
  const sh2 = short * short;
  return Math.max((sh2 * max) / s2, s2 / (sh2 * min));
}

/** Squarified treemap. `values` must be sorted descending, all > 0.
 *  Returns one rect per value, same order. */
function squarify(values: number[], rect: Rect): Rect[] {
  const total = values.reduce((a, b) => a + b, 0) || 1;
  const areas = values.map((v) => (v / total) * rect.w * rect.h);
  const out: Rect[] = [];
  let { x, y, w, h } = rect;
  let i = 0;
  while (i < areas.length) {
    const short = Math.min(w, h);
    let row = [areas[i]];
    let sum = areas[i];
    let worst = worstRatio(row, sum, short);
    let j = i + 1;
    while (j < areas.length) {
      const cand = [...row, areas[j]];
      const candSum = sum + areas[j];
      const candWorst = worstRatio(cand, candSum, short);
      if (candWorst > worst) break;
      row = cand;
      sum = candSum;
      worst = candWorst;
      j++;
    }
    const along = sum / short; // row thickness
    let off = 0;
    for (const a of row) {
      const len = a / along;
      if (w >= h) out.push({ x, y: y + off, w: along, h: len });
      else out.push({ x: x + off, y, w: len, h: along });
      off += len;
    }
    if (w >= h) {
      x += along;
      w -= along;
    } else {
      y += along;
      h -= along;
    }
    i = j;
  }
  return out;
}

const MAP_W = 1100;
const MAP_H = 560;
const TIER_LABEL_H = 14;
// Area weight: sqrt compresses mega-cap dominance (~700x -> ~26x)
// so THEME/SPEC tiers and small tickers stay legible.
const areaW = (cap: number) => Math.sqrt(Math.max(cap, 1));

/** SwingMap (v2.13) — BarelySwingTrade universe % change treemap, sized by
 *  market cap. Collapsed: top movers card in the right column. Click ->
 *  centered treemap overlay grouped by tier. Live-data only. */
export function Heatmap({ data }: { data?: HeatmapData }) {
  const [open, setOpen] = useState(false);
  const tiles = data?.tiles;

  const movers = tiles
    ? [...tiles].sort((a, b) => Math.abs(b.pct) - Math.abs(a.pct)).slice(0, 4)
    : null;

  // ── expanded overlay: tier regions squarified by total cap, tiles within ──
  let regions: {
    tier: string;
    rect: Rect;
    items: { tile: Tile; rect: Rect }[];
  }[] = [];
  if (open && tiles && tiles.length > 0) {
    const byTier = new Map<string, Tile[]>();
    for (const t of tiles) {
      const arr = byTier.get(t.tier) ?? [];
      arr.push(t);
      byTier.set(t.tier, arr);
    }
    const tierList = [...byTier.entries()]
      .map(([tier, arr]) => ({
        tier,
        arr: [...arr].sort((a, b) => b.cap - a.cap),
        cap: arr.reduce((s, t) => s + t.cap, 0),
        w: arr.reduce((s, t) => s + areaW(t.cap), 0),
      }))
      .sort((a, b) => b.cap - a.cap);
    const tierRects = squarify(
      tierList.map((t) => t.w),
      { x: 0, y: 0, w: MAP_W, h: MAP_H }
    );
    regions = tierList.map((t, i) => {
      const r = tierRects[i];
      const inner = {
        x: r.x + 1,
        y: r.y + TIER_LABEL_H,
        w: Math.max(r.w - 2, 1),
        h: Math.max(r.h - TIER_LABEL_H - 1, 1),
      };
      const itemRects = squarify(
        t.arr.map((x) => areaW(x.cap)),
        inner
      );
      return {
        tier: t.tier,
        rect: r,
        items: t.arr.map((tile, k) => ({ tile, rect: itemRects[k] })),
      };
    });
  }

  return (
    <div
      className="w-[260px] pointer-events-auto cursor-pointer"
      onClick={(e) => {
        e.stopPropagation(); // don't fire the lens shutter
        setOpen((o) => !o);
      }}
    >
      <div className="text-[13px] tracking-[0.15em] text-cyan-400 font-mono mb-1.5">
        SWING MAP · 1D
      </div>
      <div className="bg-black/30 backdrop-blur-sm border border-cyan-500/20 rounded-sm px-2 py-1.5">
        {!movers || movers.length === 0 ? (
          <div className="text-[12px] text-cyan-400/50 tracking-[0.1em] py-3 text-center font-mono">
            AWAITING FEED
          </div>
        ) : (
          movers.map((r, i) => {
            const down = r.pct < 0;
            return (
              <div
                key={r.t}
                className={
                  'flex gap-2 items-center py-[3px] font-mono leading-none ' +
                  (i < movers.length - 1 ? 'border-b border-cyan-500/10' : '')
                }
              >
                <span className="text-[11px] tracking-[0.08em] text-cyan-400/85 w-[48px]">
                  {r.t}
                </span>
                <span className="text-[10px] text-cyan-400/40 flex-1">
                  {r.tier}
                </span>
                <span
                  className={
                    'text-[11px] w-[52px] text-right ' +
                    (down ? 'text-[#ff8585]' : 'text-[#6bef9e]')
                  }
                >
                  {down ? '▼' : '▲'}
                  {Math.abs(r.pct).toFixed(2)}%
                </span>
              </div>
            );
          })
        )}
        {movers && movers.length > 0 && (
          <div className="text-[9px] text-cyan-400/35 tracking-[0.12em] pt-1 text-center font-mono">
            {open ? 'CLICK TO CLOSE MAP' : 'CLICK TO EXPAND MAP'}
          </div>
        )}
      </div>

      {/* Overlay. `fixed` inside .jhud resolves against the transformed
          stage (transform creates the containing block), so inset-0 spans
          exactly the 1180x664 design stage regardless of viewport scale. */}
      {open && tiles && tiles.length > 0 && (
        <div
          className="fixed inset-0 z-40 flex items-center justify-center bg-black/50 cursor-default"
          onClick={(e) => {
            e.stopPropagation();
            setOpen(false);
          }}
        >
          <div
            className="relative bg-black/80 backdrop-blur-md border border-cyan-500/30 rounded-sm p-2 cursor-default"
            style={{ width: MAP_W + 16, height: MAP_H + 34 }}
            onClick={(e) => e.stopPropagation()}
          >
            <div className="flex justify-between font-mono mb-1">
              <span className="text-[12px] tracking-[0.15em] text-cyan-400">
                BARELYSWINGTRADE · UNIVERSE · 1D · AREA ∝ √MKT CAP
              </span>
              <span
                className="text-[12px] text-cyan-400/70 cursor-pointer px-1"
                onClick={(e) => {
                  e.stopPropagation();
                  setOpen(false);
                }}
              >
                ✕
              </span>
            </div>
            <div
              className="relative"
              style={{ width: MAP_W, height: MAP_H }}
            >
              {regions.map((reg) => (
                <div key={reg.tier}>
                  <div
                    className="absolute font-mono text-[9px] tracking-[0.15em] text-cyan-300/80 overflow-hidden whitespace-nowrap"
                    style={{
                      left: reg.rect.x + 2,
                      top: reg.rect.y + 1,
                      width: Math.max(reg.rect.w - 4, 0),
                      height: TIER_LABEL_H - 2,
                    }}
                  >
                    {reg.tier} · {reg.items.length}
                  </div>
                  {reg.items.map(({ tile, rect }) => {
                    const showSym = rect.w >= 34 && rect.h >= 15;
                    const showPct = showSym && rect.h >= 28;
                    return (
                      <div
                        key={tile.t}
                        title={`${tile.t} ${tile.pct >= 0 ? '+' : ''}${tile.pct.toFixed(2)}% (${tile.sector}${tile.capSrc === 'proxy' ? ', size≈dollar-vol' : ''})`}
                        className="absolute overflow-hidden flex flex-col items-center justify-center font-mono leading-none border border-black/40"
                        style={{
                          left: rect.x,
                          top: rect.y,
                          width: Math.max(rect.w - 1, 1),
                          height: Math.max(rect.h - 1, 1),
                          backgroundColor: pctColor(tile.pct),
                        }}
                      >
                        {showSym && (
                          <span
                            className="text-white/90"
                            style={{
                              fontSize: Math.max(
                                8,
                                Math.min(12, rect.w / 5)
                              ),
                            }}
                          >
                            {tile.t}
                            {tile.capSrc === 'proxy' ? '*' : ''}
                          </span>
                        )}
                        {showPct && (
                          <span
                            className="text-white/75"
                            style={{ fontSize: 8 }}
                          >
                            {tile.pct >= 0 ? '+' : ''}
                            {tile.pct.toFixed(1)}%
                          </span>
                        )}
                      </div>
                    );
                  })}
                </div>
              ))}
            </div>
          </div>
        </div>
      )}
    </div>
  );
}
