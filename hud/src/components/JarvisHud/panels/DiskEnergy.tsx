import { useEffect, useState } from 'react';

/** v2.9: ENRG ring dropped. No clean source metric on a headless server VM
 *  (no battery, Azure doesn't expose power draw to the guest, and the slot
 *  is reserved for a real metric — net throughput, load avg, etc — when
 *  one earns it). Filename and export name kept as `DiskEnergy` to avoid
 *  churning the index.tsx import path; will be renamed in a future cleanup. */

interface DiskData { disk: number; }
interface DiskEnergyProps { data?: DiskData; }

function useJitter(initial: number, min: number, max: number, ms = 1800): number {
  const [v, setV] = useState(initial);
  useEffect(() => {
    const id = window.setInterval(() => {
      setV((cur) => {
        const next = cur + (Math.random() - 0.5) * 4;
        return Math.max(min, Math.min(max, next));
      });
    }, ms);
    return () => window.clearInterval(id);
  }, [min, max, ms]);
  return v;
}

function BigRing({
  cx, cy, label, value, color, unit = '%',
}: {
  cx: number; cy: number; label: string; value: number; color: string; unit?: string;
}) {
  const r = 36;
  const C = 2 * Math.PI * r;
  const dash = (value / 100) * C;
  return (
    <g transform={`translate(${cx}, ${cy})`}>
      <circle r={r + 4} fill="none" stroke="#005566" strokeWidth="0.4"
        opacity="0.55" strokeDasharray="1 4"/>
      <circle r={r} fill="none" stroke="#002934" strokeWidth="2.8"/>
      <circle r={r} fill="none" stroke={color} strokeWidth="2.8"
        strokeDasharray={`${dash} ${C}`} strokeLinecap="round"
        transform="rotate(-90)"
        style={{ filter: `drop-shadow(0 0 4px ${color}80)` }}/>
      <text textAnchor="middle" y={-12} fontSize={9}
        fill={color} fontFamily="ui-monospace" letterSpacing={1.6}>{label}</text>
      <text textAnchor="middle" y={4} fontSize={16}
        fill="#e0f7fa" fontFamily="ui-monospace" fontWeight={300}>
        {Math.round(value)}
      </text>
      <text textAnchor="middle" y={16} fontSize={8}
        fill="#80deea" fontFamily="ui-monospace" opacity="0.75">{unit}</text>
    </g>
  );
}

export function DiskEnergy({ data }: DiskEnergyProps) {
  const diskJ = useJitter(46, 42, 58, 4000);
  const disk  = data?.disk ?? diskJ;
  return (
    <g>
      <BigRing cx={-560} cy={-108} label="DISK" value={disk} color="#4dd0e1"/>
    </g>
  );
}
