import { useClock } from '../hooks/useClock';

export function DateTicker() {
  const { date, now } = useClock();
  const daysInMonth = new Date(now.getFullYear(), now.getMonth() + 1, 0).getDate();
  const days = Array.from({ length: daysInMonth }, (_, i) => i + 1);

  return (
    <div className="absolute top-[10px] left-1/2 -translate-x-1/2
                    flex gap-[7px] text-[13px] tracking-[0.08em]
                    text-[#4dd0e1] opacity-90 pointer-events-none">
      {days.map((d) => (
        <span
          key={d}
          className={
            'inline-block min-w-[22px] text-center ' +
            (d === date
              ? 'text-[#e0f7fa] opacity-100 border border-[#4dd0e1] rounded-sm ' +
                'px-[4px] py-px shadow-[0_0_6px_rgba(0,229,255,0.5)]'
              : 'opacity-55')
          }
        >
          {String(d).padStart(2, '0')}
        </span>
      ))}
    </div>
  );
}
