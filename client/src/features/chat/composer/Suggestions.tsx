/**
 * What to ask, in an empty chat: the slash commands marked `suggest`
 * (get_chat_context), each a card that puts its text in the message box for
 * the owner to finish or send.
 */

import { CornerDownLeft } from 'lucide-react';
import type { ChatCommand } from '../data/chatContext';

export function Suggestions({ items, onPick }: { items: readonly ChatCommand[]; onPick: (command: ChatCommand) => void }) {
  if (items.length === 0) return null;
  return (
    <ul aria-label="Suggestions" className="m-0 grid w-full max-w-130 list-none grid-cols-1 gap-2 p-0 sm:grid-cols-2">
      {items.map((item) => (
        <li key={item.command}>
          <button
            type="button"
            onClick={() => onPick(item)}
            className="flex h-full w-full flex-col items-start gap-1 rounded-card border border-border-default bg-bg-panel px-3.5 py-3 text-left transition-[border-color,background-color] duration-(--dur-default) hover:border-border-strong hover:bg-bg-hover"
          >
            <span className="text-sm font-semibold text-fg-default">{item.description || item.command}</span>
            <span className="flex items-center gap-1.5 text-xs text-fg-muted">
              <CornerDownLeft aria-hidden className="size-3" strokeWidth={2} />
              {item.fill}
            </span>
          </button>
        </li>
      ))}
    </ul>
  );
}

export default Suggestions;
