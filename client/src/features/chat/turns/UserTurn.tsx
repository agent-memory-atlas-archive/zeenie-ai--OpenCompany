/**
 * The owner's message: a bubble on the right (design handoff chat,
 * "Thread"), with when they sent it under it. A button they pressed in an
 * interface the employee showed (`kind: "action"`) reads as the press: the
 * button's label beside a pointer, in a quieter pill. `chat-msg
 * chat-msg-user` is the theme hook the stylized themes paint.
 */

import { MousePointerClick } from 'lucide-react';
import { cn } from '@/lib/utils';
import type { ChatMessage } from '../data/schemas';
import { timeLabel } from '../thread/timeLabel';
import { TurnMeta } from './TurnMeta';

export function UserTurn({ message, now, latest, compact }: { message: ChatMessage; now: Date; latest: boolean; compact: boolean }) {
  const pressed = message.kind === 'action';
  return (
    <div data-turn="user" data-message={message.id} className="chat-turn-user group/turn flex flex-col items-end gap-1.5">
      {pressed ? (
        <p
          className={cn(
            'm-0 flex max-w-[85%] items-center gap-2 rounded-pill border border-border-default bg-bg-panel text-fg-muted wrap-anywhere',
            compact ? 'px-3 py-1.5 text-sm' : 'px-3.5 py-2 text-sm',
          )}
        >
          <MousePointerClick aria-hidden className="size-3.5 shrink-0 text-fg-faint" strokeWidth={2} />
          <span className="sr-only">Pressed </span>
          <span className="font-medium text-fg-default">{message.text}</span>
        </p>
      ) : (
        <p
          className={cn(
            'chat-msg chat-msg-user m-0 max-w-[85%] rounded-draft rounded-br-sm border border-border-default bg-bg-elevated whitespace-pre-wrap text-fg-default wrap-anywhere',
            compact ? 'px-3 py-2 leading-normal' : 'px-4 py-2.5 text-md leading-normal',
          )}
        >
          {message.text}
        </p>
      )}
      <TurnMeta shown={latest || message.pending}>{message.pending ? 'Sending…' : timeLabel(message.timestamp, now)}</TurnMeta>
    </div>
  );
}
