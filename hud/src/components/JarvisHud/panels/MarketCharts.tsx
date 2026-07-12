interface IndexRow {
  symbol: string;
  value: string;
  changePct: number;
  bars: number[];
}

interface MarketChartsProps {
  data?: IndexRow[];
}

function Sparkbars({ bars, down }: { bars: number[]; down: boolean }) {
  const max = Math.max(...bars), min = Math.min(...bars);
  const range = max - min || 1;
  return (
    <div className="flex gap-[1.5px] h-[18px] items-end flex-1">
      {bars.map((v, i) => {
        const last = i === bars.length - 1;
        const h = 25 + ((v - min) / range) * 75;
        return (
          <div
            key={i}
            className={
              'flex-1 rounded-[1px] min-h-[2px] ' +
              (last
                ? (down
                    ? 'bg-[#ff8585] shadow-[0_0_4px_rgba(255,133,133,0.5)]'
                    : 'bg-[#6bef9e] shadow-[0_0_4px_rgba(107,239,158,0.5)]')
                : 'bg-cyan-300/40')
            }
            style={{ height: `${h}%` }}
          />
        );
      })}
    </div>
  );
}

/** MarketCharts — 5-day mini bars per index (v2.10). Live-data only.
 *  Positioned by the right-column flex container in index.tsx. */
export function MarketCharts({ data }: MarketChartsProps) {
  const rows = data;
  return (
    <div className="w-[260px] pointer-events-none">
      <div className="text-[13px] tracking-[0.15em] text-cyan-400 font-mono mb-1.5">
        MARKETS · 5D
      </div>
      <div className="bg-black/30 backdrop-blur-sm border border-cyan-500/20 rounded-sm px-2 py-1.5">
        {!rows || rows.length === 0 ? (
          <div className="text-[12px] text-cyan-400/50 tracking-[0.1em] py-3 text-center font-mono">
            AWAITING FEED
          </div>
        ) : (
          rows.map((r, i) => {
            const down = r.changePct < 0;
            return (
              <div key={r.symbol}
                className={
                  'flex gap-2 items-center py-[3px] font-mono leading-none ' +
                  (i < rows.length - 1 ? 'border-b border-cyan-500/10' : '')
                }>
                <span className="text-[11px] tracking-[0.08em] text-cyan-400/85 w-[32px]">{r.symbol}</span>
                <span className="text-[13px] text-cyan-100 w-[72px] text-right">{r.value}</span>
                <span className={'text-[11px] w-[48px] text-right ' +
                  (down ? 'text-[#ff8585]' : 'text-[#6bef9e]')}>
                  {down ? '▼' : '▲'}{Math.abs(r.changePct).toFixed(1)}%
                </span>
                <Sparkbars bars={r.bars} down={down} />
              </div>
            );
          })
        )}
      </div>
    </div>
  );
}
