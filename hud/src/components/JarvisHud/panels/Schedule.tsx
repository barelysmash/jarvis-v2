import { ScheduleWidget } from '../../ScheduleWidget';

interface CalendarEvent {
  title: string;
  human_time: string;
  location?: string;
}

interface ScheduleProps {
  events: CalendarEvent[] | null;
}

/** Schedule panel — wraps existing ScheduleWidget, positions it at left-mid. */
export function Schedule({ events }: ScheduleProps) {
  return (
    <div className="absolute left-[18px] top-[358px] w-[220px] pointer-events-auto">
      <ScheduleWidget events={events} />
    </div>
  );
}
