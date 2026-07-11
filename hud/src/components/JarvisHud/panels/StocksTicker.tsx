interface TickerItem {
  symbol: string;
  value: string;
  changePct: number;
}

interface StocksTickerProps {
  items?: TickerItem[];
}

function Chip({ item }: { item: TickerItem }) {
  const down = item.changePct < 0;
  return (
    <span className="inline-flex items-center gap-2 px-3 py-[2px] mr-7 font-mono text-[14px]">
      <span className="text-cyan-400 tracking-[0.08em]">{item.symbol}</span>
      <span className="text-cyan-100">{item.value}</span>
      <span className={
        'px-1.5 py-px rounded-sm text-[12px] ' +
        (down ? 'text-[#ff8585] bg-red-400/10' : 'text-[#6bef9e] bg-green-400/10')
      }>
        {down ? '▼' : '▲'} {Math.abs(item.changePct).toFixed(2)}%
      </span>
    </span>
  );
}

/** StocksTicker — scrolling market strip. Live-data only: renders a static
 *  AWAITING FEED chip until the `stocks` widget event arrives. */
export function StocksTicker({ items }: StocksTickerProps) {
  const list = items;
  if (!list || list.length === 0) {
    return (
      <div className="scroll-wrap left-0 right-0 top-[38px] h-[22px]">
        <span className="inline-flex items-center px-3 py-[2px] font-mono text-[12px]
                         text-cyan-400/50 tracking-[0.1em]">
          MARKET FEED · AWAITING DATA
        </span>
      </div>
    );
  }
  return (
    <div className="scroll-wrap left-0 right-0 top-[38px] h-[22px]">
      <div
        className="scroll-inner"
        style={{ animation: 'jhud-scroll-left 60s linear infinite' }}
      >
        {[0, 1].map((copy) => (
          <span key={copy} className="inline-flex shrink-0">
            {list.map((it, i) => <Chip key={`${copy}-${i}`} item={it}/>)}
          </span>
        ))}
      </div>
    </div>
  );
}
