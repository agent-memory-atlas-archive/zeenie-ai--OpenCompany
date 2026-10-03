/**
 * What the owner might ask next, under the employee's latest answer (design
 * handoff chat, "follow-up chips"): up to three short questions the employee
 * suggested (`parts.followups`). Pressing one sends it as the owner's
 * message, exactly as it reads. They spring in one after another the first
 * time they show.
 */

import { CornerDownLeft } from 'lucide-react';
import { useLayoutEffect, useRef } from 'react';
import { Button } from '@/components/ui/button';
import { stagger, staggerStep } from '@/lib/motion';

export function FollowUps({ items, onPick, disabled = false }: { items: string[]; onPick: (text: string) => void; disabled?: boolean }) {
  const listRef = useRef<HTMLDivElement>(null);
  useLayoutEffect(() => {
    const chips = listRef.current?.querySelectorAll('[data-followup]');
    if (!chips?.length) return;
    stagger(chips, [{ opacity: 0, transform: 'translateY(6px)' }, { opacity: 1, transform: 'none' }], {
      duration: 'follow-in',
      easing: 'spring',
      step: staggerStep('follow'),
    });
    // Once, when they first show.
  }, []);

  if (items.length === 0) return null;
  return (
    <div ref={listRef} role="group" aria-label="Ask next" className="mt-0.5 flex flex-col items-start gap-1.5">
      {items.map((text) => (
        <Button
          key={text}
          data-followup
          variant="quiet"
          disabled={disabled}
          onClick={() => onPick(text)}
          className="h-auto min-h-8 max-w-full justify-start gap-2 rounded-pill border-border-default py-1.25 pr-3 pl-2.5 text-left text-sm font-medium whitespace-normal text-fg-default hover:border-border-strong hover:bg-bg-hover"
        >
          <CornerDownLeft aria-hidden className="size-3.25 shrink-0 text-fg-faint" strokeWidth={2} />
          {text}
        </Button>
      ))}
    </div>
  );
}
