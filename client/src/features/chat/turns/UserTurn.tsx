/**
 * The owner's message: a bubble on the right (design handoff chat,
 * "Thread"), with when they sent it under it. `chat-msg chat-msg-user` is
 * the theme hook the stylized themes paint.
 */

import { cn } from '@/lib/utils';
import type { ChatMessage } from '../data/schemas';
import { timeLabel } from '../thread/timeLabel';
import { TurnMeta } from './TurnMeta';

export function UserTurn({ message, now, latest, compact }: { message: ChatMessage; now: Date; latest: boolean; compact: boolean }) {
  return (
    <div data-turn="user" data-message={message.id} className="chat-turn-user group/turn flex flex-col items-end gap-1.5">
      <p
        className={cn(
          'chat-msg chat-msg-user m-0 max-w-[85%] rounded-draft rounded-br-sm border border-border-default bg-bg-elevated whitespace-pre-wrap text-fg-default wrap-anywhere',
          compact ? 'px-3 py-2 leading-normal' : 'px-4 py-2.5 text-md leading-normal',
        )}
      >
        {message.text}
      </p>
      <TurnMeta shown={latest || message.pending}>{message.pending ? 'Sending…' : timeLabel(message.timestamp, now)}</TurnMeta>
    </div>
  );
}
