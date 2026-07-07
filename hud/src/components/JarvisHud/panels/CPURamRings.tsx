import { useEffect, useState } from 'react';

interface SystemMetrics { cpu: number; ram: number; }
interface CPURamRingsProps { data?: SystemMetrics; }

function useJitter(initial: number, min: number, max: number, ms = 1300): number {
  const [v, setV] = useState(initial);
  useEffect(() => {
    const id = window.setInterval(() => {
      setV((cur) => {
        const next = cur + (Math.random() - 0.5) * 6;
        return Math.max(min, Math.min(max, next));
      });
    }, ms);
    return () => window.clearInterval(id);
  }, [min, max, ms]);
  return v;
}

function Ring({
  cx, cy, label, value, color,
}: {
  cx: number; cy: number; label: string; value: number; color: string;
}) {
  const r = 28;
  const C = 2 * Math.PI * r;
  const dash = (value / 100) * C;
  return (
    <g transform={`translate(${cx}, ${cy})`}>
      <circle r={r} fill="none" stroke="#003844" strokeWidth="2.8"/>
      <circle r={r} fill="none" stroke={color} strokeWidth="2.8"
        strokeDasharray={`${dash} ${C}`}
        strokeLinecap="round" transform="rotate(-90)"
        style={{ filter: `drop-shadow(0 0 3px ${color}80)` }}/>
      <text textAnchor="middle" y={-8} fontSize={9}
        fill={color} fontFamily="ui-monospace" letterSpacing={1.4}>{label}</text>
      <text textAnchor="middle" y={8} fontSize={14}
        fill="#e0f7fa" fontFamily="ui-monospace" fontWeight={300}>
        {Math.round(value)}%
      </text>
    </g>
  );
}

export function CPURamRings({ data }: CPURamRingsProps) {
  const cpuJ = useJitter(34, 18, 78);
  const ramJ = useJitter(56, 35, 82);
  const cpu = data?.cpu ?? cpuJ;
  const ram = data?.ram ?? ramJ;
  return (
    <g>
      <Ring cx={-440} cy={-290} label="CPU" value={cpu} color="#4dd0e1"/>
      <Ring cx={-440} cy={-218} label="RAM" value={ram} color="#80deea"/>
    </g>
  );
}
