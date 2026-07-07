import { ConversationLog } from '../../ConversationLog';
import { TextInput } from '../../TextInput';
import type { Message } from '../../../hooks/useJarvisState';

interface ChatProps {
  messages: Message[];
}

/** Chat panel — bigger in v2.8 (420×240, was 360×168) so JARVIS responses
 *  have room to render without scrolling on first impression. */
export function Chat({ messages }: ChatProps) {
  return (
    <div className="absolute right-[18px] bottom-[42px] w-[420px] h-[240px]
                    flex flex-col gap-1 pointer-events-auto">
      <div className="flex-1 min-h-0">
        <ConversationLog messages={messages} />
      </div>
      <TextInput />
    </div>
  );
}
