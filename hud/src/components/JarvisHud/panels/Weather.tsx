interface WeatherData {
  temp: number;
  condition: string;
  high: number;
  low: number;
  humidity?: number;
  wind?: string;
  moonPhase?: number;
}

interface WeatherProps {
  data?: WeatherData;
}

const FALLBACK: WeatherData = {
  temp: 78, condition: 'Clear', high: 88, low: 68,
  humidity: 42, wind: 'SW 8 mph', moonPhase: 0.62,
};

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

export function Weather({ data }: WeatherProps) {
  const w = data ?? FALLBACK;
  return (
    <div className="absolute right-[18px] top-[70px] w-[240px] pointer-events-none">
      <div className="bg-black/30 backdrop-blur-sm border border-cyan-500/20 rounded-sm px-3 py-2 font-mono">
        <div className="flex justify-between items-center mb-1">
          <div className="text-[13px] tracking-[0.15em] text-cyan-400">WEATHER · AUSTIN</div>
          <Moon phase={w.moonPhase}/>
        </div>
        <div className="flex items-baseline gap-2">
          <div className="text-[36px] text-cyan-100 font-light leading-none">{w.temp}°</div>
          <div className="text-[13px] text-cyan-300">{w.condition}</div>
        </div>
        <div className="text-[12px] text-cyan-400/80 mt-1.5">
          H {w.high}° · L {w.low}°
          {w.humidity != null && <> · {w.humidity}% RH</>}
          {w.wind && <> · {w.wind}</>}
        </div>
      </div>
    </div>
  );
}
