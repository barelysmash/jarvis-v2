interface NewsItem {
  category: string;
  headline: string;
}

interface NewsTickerProps {
  items?: NewsItem[];
}

const FALLBACK: NewsItem[] = [
  { category: 'MARKETS', headline: 'Nasdaq sheds 4.18% in worst session since April 2025 as megacap tech leads broad selloff' },
  { category: 'TECH',    headline: 'AI infrastructure capex hits $480B annualized as cloud providers race for compute' },
  { category: 'CRYPTO',  headline: 'Bitcoin extends 13-day record ETF outflow streak; spot price dips below $62K' },
  { category: 'WORLD',   headline: 'Japan PM signals readiness to intervene on yen as Nikkei records new all-time high midweek' },
  { category: 'FED',     headline: 'Powell remarks ambiguous on July cut path; futures price 38% probability' },
  { category: 'ENERGY',  headline: 'WTI crude steadies near $69 as inventories build offsets Mideast risk premium' },
];

function NewsNode({ item }: { item: NewsItem }) {
  return (
    <span className="inline-flex items-center gap-2 mr-12 font-mono text-[14px]">
      <span className="px-2 py-px text-[11px] tracking-[0.12em]
                       text-cyan-400 border border-cyan-500/40 rounded-sm">
        {item.category}
      </span>
      <span className="text-cyan-100">{item.headline}</span>
    </span>
  );
}

export function NewsTicker({ items }: NewsTickerProps) {
  const list = items ?? FALLBACK;
  return (
    <>
      <div className="absolute left-[18px] bottom-[10px] flex items-center gap-2
                      pointer-events-none z-10 font-mono">
        <span className="w-[7px] h-[7px] rounded-full bg-red-400 blink
                         shadow-[0_0_5px_rgba(248,113,113,0.8)]"/>
        <span className="text-[12px] tracking-[0.18em] text-red-300">LIVE · NEWSWIRE</span>
      </div>
      <div className="scroll-wrap left-[180px] right-[18px] bottom-[10px] h-[20px]">
        <div
          className="scroll-inner"
          style={{ animation: 'jhud-scroll-left 90s linear infinite' }}
        >
          {[0, 1].map((copy) => (
            <span key={copy} className="inline-flex shrink-0">
              {list.map((it, i) => <NewsNode key={`${copy}-${i}`} item={it}/>)}
            </span>
          ))}
        </div>
      </div>
    </>
  );
}
