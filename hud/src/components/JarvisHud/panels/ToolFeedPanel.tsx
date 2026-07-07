import { ToolFeed } from '../../ToolFeed';
import type { ToolEvent } from '../../../hooks/useJarvisState';

interface ToolFeedPanelProps {
  events: ToolEvent[];
}

/** ToolFeedPanel — wraps existing ToolFeed at bottom-left, above the news ticker. */
export function ToolFeedPanel({ events }: ToolFeedPanelProps) {
  return (
    <div className="absolute left-[18px] bottom-[42px] w-[300px] pointer-events-auto">
      <ToolFeed events={events} />
    </div>
  );
}
