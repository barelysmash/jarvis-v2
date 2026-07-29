interface CalendarEvent {
  title: string;
  human_time: string;
  location?: string;
}

interface ScheduleWidgetProps {
  events: CalendarEvent[] | null;
}

export function ScheduleWidget({ events }: ScheduleWidgetProps) {
  return (
    <div className="bg-black/30 backdrop-blur-sm border border-cyan-500/20 rounded-sm">
      <div className="px-4 py-2 border-b border-cyan-500/20">
        <div className="text-cyan-400 text-xs font-mono tracking-[0.2em]">
          SCHEDULE
        </div>
      </div>
      <div className="p-4 text-xs font-mono max-h-[186px] overflow-y-auto">
        {!events || !Array.isArray(events) || events.length === 0 ? (
          <div className="text-cyan-800 italic">No upcoming events</div>
        ) : (
          <ul className="space-y-2">
            {events.slice(0, 5).map((e, i) => (
              <li key={i} className="border-l-2 border-cyan-500/40 pl-2">
                <div className="text-cyan-200 leading-tight">{e.title}</div>
                <div className="text-cyan-700 text-[10px]">{e.human_time}</div>
                {e.location && (
                  <div className="text-cyan-800 text-[10px]">📍 {e.location}</div>
                )}
              </li>
            ))}
          </ul>
        )}
      </div>
    </div>
  );
}
