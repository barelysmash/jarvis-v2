import { useClock } from '../hooks/useClock';

/**
 * DateClock — the enlarged date/time ring on the left side of the HUD.
 * v2.7: removed the AUSTIN · SYS LOCK footer (decorative noise, hard to
 * read at base scale), bumped the day and time fonts for legibility.
 */
export function DateClock() {
  const { monthDow, dayNum, time } = useClock();

  return (
    <g transform="translate(-560, -240)">
      <circle r={72} fill="none" stroke="#0096b8" strokeWidth="1.2" opacity="0.7"/>
      <circle r={64} fill="none" stroke="#4dd0e1" strokeWidth="0.5"
        opacity="0.4" strokeDasharray="2 6"/>
      <circle r={78} fill="none" stroke="#005566" strokeWidth="0.5"
        opacity="0.6" strokeDasharray="1 5"/>

      <text textAnchor="middle" y={-42} fontSize={11} fill="#4dd0e1"
        fontFamily="ui-monospace, monospace" letterSpacing={3}>
        {monthDow}
      </text>
      <text textAnchor="middle" y={-2} fontSize={26} fill="#e0f7fa"
        fontFamily="ui-monospace, monospace" fontWeight={200}>
        {dayNum}
      </text>
      <text textAnchor="middle" y={42} fontSize={30} fill="#e0f7fa"
        fontFamily="ui-monospace, monospace" letterSpacing={0.5} fontWeight={300}>
        {time}
      </text>
    </g>
  );
}
