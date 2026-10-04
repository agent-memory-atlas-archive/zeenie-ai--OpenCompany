/**
 * ‹ 1 / 2 › between the versions of a message (the owner's edits) or of an
 * answer (the tries), in the turn's hover bar. Moving shows that version and
 * the conversation that followed it. Off while the conversation cannot
 * change.
 */

import { ChevronLeft, ChevronRight } from 'lucide-react';
import { Button } from '@/components/ui/button';
import type { MessageSiblings } from '../data/schemas';

export function VersionStepper({
  siblings,
  noun,
  disabled,
  onSwitch,
}: {
  siblings: MessageSiblings;
  /** What steps: "version" (a message) or "reply" (an answer). */
  noun: 'version' | 'reply';
  disabled: boolean;
  onSwitch: (messageId: string) => void;
}) {
  const { index, count, ids } = siblings;
  const go = (to: number) => {
    const id = ids[to];
    if (id !== undefined) onSwitch(id);
  };
  return (
    <span role="group" aria-label={noun === 'reply' ? 'Replies' : 'Versions'} className="flex items-center font-mono text-xs text-fg-muted">
      <Button
        variant="quiet"
        size="icon-xs"
        disabled={disabled || index === 0}
        onClick={() => go(index - 1)}
        aria-label={`Previous ${noun}`}
      >
        <ChevronLeft aria-hidden className="size-3.25" strokeWidth={2.2} />
      </Button>
      <span className="tabular-nums">
        {index + 1} / {count}
      </span>
      <Button
        variant="quiet"
        size="icon-xs"
        disabled={disabled || index === count - 1}
        onClick={() => go(index + 1)}
        aria-label={`Next ${noun}`}
      >
        <ChevronRight aria-hidden className="size-3.25" strokeWidth={2.2} />
      </Button>
    </span>
  );
}
