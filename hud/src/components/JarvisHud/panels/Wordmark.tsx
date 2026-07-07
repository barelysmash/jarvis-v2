import { JARVIS_VERSION } from '../version';

export function Wordmark() {
  const label = `JARVIS ${JARVIS_VERSION.toUpperCase()}`;
  return (
    <g transform="translate(0, 345)">
      <text textAnchor="middle" fontSize={16} fill="#4dd0e1"
        letterSpacing={14} fontWeight={300} opacity="0.9">
        {label}
      </text>
      <text textAnchor="middle" y={16} fontSize={9} fill="#80deea"
        letterSpacing={8} opacity="0.55">
        OPTICAL · TELEMETRY · ARRAY · COMMS
      </text>
    </g>
  );
}
