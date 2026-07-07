interface ForecastDay {
  label: string;
  icon: string;
  high: number;
  low: number;
}

interface ForecastProps {
  days?: ForecastDay[];
}

const FALLBACK: ForecastDay[] = [
  { label: 'SUN', icon: '☀',  high: 86, low: 68 },
  { label: 'MON', icon: '☀',  high: 90, low: 70 },
  { label: 'TUE', icon: '⛈', high: 88, low: 72 },
  { label: 'WED', icon: '☁',  high: 83, low: 69 },
];

export function Forecast({ days }: ForecastProps) {
  const d = days ?? FALLBACK;
  return (
    <div className="absolute right-[18px] top-[348px] w-[240px] pointer-events-none">
      <div className="text-[13px] tracking-[0.15em] text-cyan-400 font-mono mb-1.5">
        FORECAST · 4D
      </div>
      <div className="flex gap-1.5">
        {d.map((day) => (
          <div key={day.label}
            className="flex-1 px-1 py-1.5 border border-cyan-500/20 rounded-sm
                       bg-black/30 text-center font-mono leading-tight">
            <div className="text-[10px] text-cyan-400/80 tracking-[0.12em]">{day.label}</div>
            <div className="text-[18px] my-0.5 text-cyan-200
                            drop-shadow-[0_0_3px_rgba(128,222,234,0.4)]">{day.icon}</div>
            <div className="text-[13px] text-cyan-100">{day.high}°</div>
            <div className="text-[11px] text-cyan-300/70">{day.low}°</div>
          </div>
        ))}
      </div>
    </div>
  );
}
