/**
 * The message box (design handoff chat, "Composer"): a text box that grows
 * with what is written, up to `--h-chat-composer-max`, then scrolls; and
 * Send, which becomes Stop while the employee is answering. Enter sends,
 * Shift+Enter starts a new line, and the Enter that confirms an input
 * method's composition never sends (lib/composerKeys). Esc stops an answer
 * (ChatPane).
 *
 * What is written lives in the composer store per conversation, so it
 * survives switching to another employee and back. While the employee is
 * still answering the last message the box takes text but Send waits.
 */

import { ArrowUp, Square } from 'lucide-react';
import type { KeyboardEvent, RefObject } from 'react';
import { Button } from '@/components/ui/button';
import { Textarea } from '@/components/ui/textarea';
import { isSendKey } from '@/lib/composerKeys';
import { useAutoGrow } from '@/lib/useAutoGrow';
import { cn } from '@/lib/utils';
import { useComposerDraft, useComposerStore } from '../state/composerStore';

export interface ComposerProps {
  sessionId: string;
  name: string;
  /** The conversation has loaded, so a message can go after it. */
  ready: boolean;
  /** A message is on its way, or the employee is still answering one. */
  busy: boolean;
  onSend: () => void;
  /** Stops the answer under way; absent while there is none. */
  onStop?: () => void;
  /** The answer is already stopping. */
  stopping?: boolean;
  compact: boolean;
  /** The text box, for the host's focus requests. */
  boxRef: RefObject<HTMLTextAreaElement | null>;
}

export function Composer({ sessionId, name, ready, busy, onSend, onStop, stopping = false, compact, boxRef }: ComposerProps) {
  const draft = useComposerDraft(sessionId);
  const setText = useComposerStore((state) => state.setText);
  useAutoGrow(boxRef, draft.text);
  const canSend = ready && !busy && draft.text.trim().length > 0;

  const onKeyDown = (event: KeyboardEvent<HTMLTextAreaElement>) => {
    if (!isSendKey(event)) return;
    event.preventDefault();
    if (canSend) onSend();
  };

  const iconSize = compact ? 'size-3.5' : 'size-4';
  const button = onStop ? (
    <Button
      variant="invert"
      size="icon"
      disabled={stopping}
      onClick={onStop}
      aria-label={stopping ? 'Stopping' : 'Stop reply'}
      title={stopping ? 'Stopping…' : 'Stop (Esc)'}
      className={cn('shrink-0 rounded-full', compact ? 'size-7' : 'size-8.5')}
    >
      <Square aria-hidden fill="currentColor" strokeWidth={0} className={compact ? 'size-2.75' : 'size-3'} />
    </Button>
  ) : (
    <Button
      variant="invert"
      size="icon"
      disabled={!canSend}
      onClick={onSend}
      aria-label="Send"
      title={busy ? `${name} is still answering` : 'Send (Enter)'}
      className={cn('shrink-0 rounded-full', compact ? 'size-7' : 'size-8.5')}
    >
      <ArrowUp aria-hidden strokeWidth={2.25} className={iconSize} />
    </Button>
  );

  return (
    <div
      className={cn(
        'chat-composer relative flex border border-border-default bg-bg-panel transition-colors duration-(--dur-slow) focus-within:border-border-strong',
        compact ? 'items-end gap-2 rounded-lg py-1.5 pr-1.5 pl-3' : 'flex-col gap-2 rounded-card py-2.5 pr-2.5 pb-2 pl-3.5 shadow-modal',
      )}
    >
      <Textarea
        ref={boxRef}
        variant="bare"
        rows={1}
        value={draft.text}
        onChange={(event) => setText(sessionId, event.target.value)}
        onKeyDown={onKeyDown}
        aria-label={`Message ${name}`}
        placeholder={`Message ${name}…`}
        className={cn(
          'max-h-(--h-chat-composer-max) overflow-hidden text-fg-default field-sizing-fixed',
          compact ? 'py-1 text-sm leading-normal' : 'py-1.5 text-md leading-normal',
        )}
      />
      {compact ? button : <div className="flex items-center justify-end gap-1.5">{button}</div>}
    </div>
  );
}
