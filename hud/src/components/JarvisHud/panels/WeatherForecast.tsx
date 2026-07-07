interface WeatherNow {
  temp: number;
  condition: string;
  high: number;
  low: number;
  humidity?: number;
  wind?: string;
  moonPhase?: number;
}

interface ForecastDay {
  label: string;
  icon: string;
  high: number;
  low: number;
}

interface WeatherForecastProps {
  now?: WeatherNow;
  days?: ForecastDay[];
}

const FALLBACK_NOW: WeatherNow = {
  temp: 78, condition: 'Clear', high: 88, low: 68,
  humidity: 42, wind: 'SW 8 mph', moonPhase: 0.62,
};

const FALLBACK_DAYS: ForecastDay[] = [
  { label: 'SUN', icon: '☀',  high: 86, low: 68 },
  { label: 'MON', icon: '☀',  high: 90, low: 70 },
  { label: 'TUE', icon: '⛈', high: 88, low: 72 },
  { label: 'WED', icon: '☁',  high: 83, low: 69 },
];

function Moon({ phase = 0.5 }: { phase?: number }) {
  const offset = (phase - 0.5) * 14;
  return (
    <svg width={20} height={20} viewBox="-10 -10 20 20" className="inline-block">
      <circle r={8} fill="#e0f7fa" opacity={0.85}
        filter="drop-shadow(0 0 4px rgba(128,222,234,0.5))"/>
      <circle r={8} cx={offset} fill="#001824"/>
    </svg>
  );
}

/** WeatherForecast — combined current conditions + 4-day forecast (v2.8).
 *  Replaces the separate Weather + Forecast panels. */
export function WeatherForecast({ now, days }: WeatherForecastProps) {
  const w = now  ?? FALLBACK_NOW;
  const d = days ?? FALLBACK_DAYS;
  return (
    <div className="absolute right-[18px] top-[64px] w-[260px] pointer-events-none">
      <div className="bg-black/30 backdrop-blur-sm border border-cyan-500/20
                      rounded-sm px-3 py-2.5 font-mono">
        {/* header */}
        <div className="flex justify-between items-center mb-1.5">
          <div className="text-[13px] tracking-[0.15em] text-cyan-400">WEATHER · AUSTIN</div>
          <Moon phase={w.moonPhase}/>
        </div>

        {/* current */}
        <div className="flex items-center gap-3 mb-1.5">
          <div className="text-[36px] text-cyan-100 font-light leading-none">{w.temp}°</div>
          <div className="flex flex-col leading-tight">
            <span className="text-[13px] text-cyan-200">{w.condition}</span>
            <span className="text-[12px] text-cyan-400/85">H {w.high}° · L {w.low}°</span>
            {(w.humidity != null || w.wind) && (
              <span className="text-[11px] text-cyan-400/70 mt-px">
                {w.humidity != null && `${w.humidity}% RH`}
                {w.humidity != null && w.wind && ' · '}
                {w.wind}
              </span>
            )}
          </div>
        </div>

        {/* 4-day strip */}
        <div className="flex gap-1 pt-2 border-t border-cyan-500/15">
          {d.map((day) => (
            <div key={day.label} className="flex-1 text-center leading-tight">
              <div className="text-[10px] text-cyan-400/80 tracking-[0.08em]">{day.label}</div>
              <div className="text-[16px] my-0.5 text-cyan-200
                              drop-shadow-[0_0_3px_rgba(128,222,234,0.4)]">{day.icon}</div>
              <div className="text-[12px] text-cyan-100">{day.high}°</div>
              <div className="text-[10px] text-cyan-300/70">{day.low}°</div>
            </div>
          ))}
        </div>
      </div>
    </div>
  );
}
