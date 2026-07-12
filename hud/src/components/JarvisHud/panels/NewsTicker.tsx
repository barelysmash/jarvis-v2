interface NewsItem {
  category: string;
  headline: string;
}

interface NewsTickerProps {
  items?: NewsItem[];
}

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

/** NewsTicker (v2.10) — live-data only: renders a static AWAITING chip until
 *  the `news` widget event arrives (instant on connect via replay cache). */
export function NewsTicker({ items }: NewsTickerProps) {
  const list = items;
  return (
    <>
      <div className="absolute left-[18px] bottom-[10px] flex items-center gap-2
                      pointer-events-none z-10 font-mono">
        <span className="w-[7px] h-[7px] rounded-full bg-red-400 blink
                         shadow-[0_0_5px_rgba(248,113,113,0.8)]"/>
        <span className="text-[12px] tracking-[0.18em] text-red-300">LIVE · NEWSWIRE</span>
      </div>
      <div className="scroll-wrap left-[180px] right-[18px] bottom-[10px] h-[20px]">
        {!list || list.length === 0 ? (
          <span className="inline-flex items-center font-mono text-[12px]
                           text-cyan-400/50 tracking-[0.1em]">
            NEWS FEED · AWAITING DATA
          </span>
        ) : (
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
        )}
      </div>
    </>
  );
}
