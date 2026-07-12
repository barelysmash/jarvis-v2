import { ConversationLog } from '../../ConversationLog';
import { TextInput } from '../../TextInput';
import type { Message } from '../../../hooks/useJarvisState';

interface ChatProps {
  messages: Message[];
}

/** Chat panel (v2.10) — expands on hover/focus so long JARVIS responses are
 *  fully readable, overlaying MarketCharts with a solid backdrop while
 *  interacting and collapsing back afterwards. Conversation text is
 *  selectable/copyable (overrides the stage-wide user-select: none). */
export function Chat({ messages }: ChatProps) {
  return (
    <div className="absolute right-[18px] bottom-[42px] w-[420px] h-[240px]
                    hover:h-[430px] focus-within:h-[430px]
                    hover:z-30 focus-within:z-30
                    hover:bg-[#001018]/95 focus-within:bg-[#001018]/95
                    hover:backdrop-blur-sm focus-within:backdrop-blur-sm
                    hover:shadow-[0_0_24px_rgba(0,0,0,0.6)]
                    focus-within:shadow-[0_0_24px_rgba(0,0,0,0.6)]
                    transition-all duration-200 rounded-sm
                    flex flex-col gap-1 pointer-events-auto">
      <div className="flex-1 min-h-0 select-text cursor-text">
        <ConversationLog messages={messages} />
      </div>
      <TextInput />
    </div>
  );
}
