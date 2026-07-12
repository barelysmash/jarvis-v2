// Server payload shape (widget event `weather`), published by _weather_publisher.
interface ServerForecastDay {
  day: string;
  high: number;
  low: number;
  condition: string;
  precip_pct?: number | null;
}

interface WeatherNow {
  location?: string;
  temp: number;
  condition: string;
  high?: number | null;
  low?: number | null;
  humidity?: number;
  wind_mph?: number;
  wind_dir?: string;
  is_day?: boolean;
  forecast?: ServerForecastDay[];
  // legacy fields tolerated if some other producer sends them
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

function iconFor(condition: string): string {
  const c = condition.toLowerCase();
  if (c.includes('thunder')) return '⛈';
  if (c.includes('rain') || c.includes('drizzle') || c.includes('shower')) return '🌧';
  if (c.includes('snow')) return '❄';
  if (c.includes('fog')) return '🌫';
  if (c.includes('partly')) return '⛅';
  if (c.includes('overcast') || c.includes('cloud')) return '☁';
  return '☀';
}

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

/** WeatherForecast — combined current conditions + 4-day forecast (v2.10).
 *  Live-data only. Positioned by the right-column flex container in
 *  index.tsx, so it can grow without colliding with MarketCharts. */
export function WeatherForecast({ now, days }: WeatherForecastProps) {
  const w = now;
  const d: ForecastDay[] | null =
    days ??
    (w?.forecast
      ? w.forecast.map((f) => ({
          label: f.day,
          icon: iconFor(f.condition),
          high: f.high,
          low: f.low,
        }))
      : null);

  const windStr =
    w?.wind ??
    (w?.wind_mph != null && w?.wind_dir
      ? `${w.wind_dir} ${w.wind_mph} mph`
      : undefined);

  return (
    <div className="w-[260px] pointer-events-none">
      <div className="bg-black/30 backdrop-blur-sm border border-cyan-500/20
                      rounded-sm px-3 py-2.5 font-mono">
        {/* header */}
        <div className="flex justify-between items-center mb-1.5">
          <div className="text-[13px] tracking-[0.15em] text-cyan-400">
            WEATHER · {(w?.location ?? 'AUSTIN').toUpperCase()}
          </div>
          <Moon phase={w?.moonPhase}/>
        </div>

        {!w ? (
          <div className="text-[12px] text-cyan-400/50 tracking-[0.1em] py-3 text-center">
            AWAITING DATA
          </div>
        ) : (
          <>
            {/* current */}
            <div className="flex items-center gap-3 mb-1.5">
              <div className="text-[36px] text-cyan-100 font-light leading-none">{w.temp}°</div>
              <div className="flex flex-col leading-tight">
                <span className="text-[13px] text-cyan-200">{w.condition}</span>
                {(w.high != null || w.low != null) && (
                  <span className="text-[12px] text-cyan-400/85">
                    H {w.high ?? '—'}° · L {w.low ?? '—'}°
                  </span>
                )}
                {(w.humidity != null || windStr) && (
                  <span className="text-[11px] text-cyan-400/70 mt-px">
                    {w.humidity != null && `${w.humidity}% RH`}
                    {w.humidity != null && windStr && ' · '}
                    {windStr}
                  </span>
                )}
              </div>
            </div>

            {/* 4-day strip */}
            {d && d.length > 0 && (
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
            )}
          </>
        )}
      </div>
    </div>
  );
}
