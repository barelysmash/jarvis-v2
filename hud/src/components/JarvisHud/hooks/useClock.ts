import { useEffect, useState } from 'react';

const MONTHS = ['JAN','FEB','MAR','APR','MAY','JUN','JUL','AUG','SEP','OCT','NOV','DEC'] as const;
const DOWS   = ['SUN','MON','TUE','WED','THU','FRI','SAT'] as const;

export interface ClockFields {
  /** HH:MM:SS, local */
  time: string;
  /** zero-padded day of month, e.g. "06" */
  dayNum: string;
  /** "JUN · SAT" */
  monthDow: string;
  /** raw Date for consumers who need it */
  now: Date;
  /** 1..31 */
  date: number;
  /** 0..11 */
  monthIndex: number;
  /** 0..6 (Sun..Sat) */
  dowIndex: number;
}

function compute(): ClockFields {
  const d = new Date();
  return {
    time: d.toTimeString().slice(0, 8),
    dayNum: String(d.getDate()).padStart(2, '0'),
    monthDow: `${MONTHS[d.getMonth()]} · ${DOWS[d.getDay()]}`,
    now: d,
    date: d.getDate(),
    monthIndex: d.getMonth(),
    dowIndex: d.getDay(),
  };
}

export function useClock(): ClockFields {
  const [c, setC] = useState<ClockFields>(compute);
  useEffect(() => {
    const id = window.setInterval(() => setC(compute()), 1000);
    return () => window.clearInterval(id);
  }, []);
  return c;
}
