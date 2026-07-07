interface TickerItem {
  symbol: string;
  value: string;
  changePct: number;
}

interface StocksTickerProps {
  items?: TickerItem[];
}

const FALLBACK: TickerItem[] = [
  { symbol: 'S&P',  value: '7,383.74',  changePct: -2.64 },
  { symbol: 'NDX',  value: '25,709.43', changePct: -4.18 },
  { symbol: 'DJI',  value: '50,866.78', changePct: -1.35 },
  { symbol: 'BTC',  value: '62,045',    changePct: -2.30 },
  { symbol: 'VIX',  value: '24.7',      changePct:  18.40 },
  { symbol: 'RUT',  value: '2,418.50',  changePct: -2.05 },
  { symbol: '10Y',  value: '4.31%',     changePct:   0.04 },
  { symbol: 'WTI',  value: '69.12',     changePct:   0.85 },
  { symbol: 'GOLD', value: '3,182',     changePct:  -0.45 },
  { symbol: 'ETH',  value: '3,408',     changePct:  -3.10 },
];

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

export function StocksTicker({ items }: StocksTickerProps) {
  const list = items ?? FALLBACK;
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
