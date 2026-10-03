/**
 * The answering employee's avatar beside their turns: their initial in
 * their role colour, and a spinning ring while they work (design handoff
 * chat, "Streaming": the live ring).
 */

import { AVATAR_CLASS, initialOf } from '@/components/catalog/presentation';
import { cn } from '@/lib/utils';
import type { ChatPersona } from '../host';

export function ChatAvatar({ persona, live = false, compact = false }: { persona: ChatPersona; live?: boolean; compact?: boolean }) {
  return (
    <span aria-hidden className={cn('relative shrink-0', compact ? 'size-6' : '-mt-0.5 size-7.5')}>
      {live && <span data-live-ring className="opencompany-live-ring absolute -inset-0.75 rounded-full" />}
      <span
        className={cn(
          'absolute inset-0 grid place-items-center rounded-full border font-semibold',
          AVATAR_CLASS[persona.colorRole],
          compact ? 'text-2xs' : 'text-xs',
        )}
      >
        {initialOf(persona.name)}
      </span>
    </span>
  );
}
