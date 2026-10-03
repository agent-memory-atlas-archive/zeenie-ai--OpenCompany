/**
 * The line under a turn: when it was written. Faint until the turn is
 * hovered or focused (design handoff chat: the hover bar); always shown on
 * touch screens, on the newest turn, and while it says something that
 * matters now ("Sending…").
 */

import type { ReactNode } from 'react';
import { cn } from '@/lib/utils';

export function TurnMeta({ children, shown = false, className }: { children: ReactNode; shown?: boolean; className?: string }) {
  if (!children) return null;
  return (
    <div
      className={cn(
        'flex h-6 items-center gap-1 px-1.5 text-xs text-fg-faint transition-opacity duration-(--dur-default)',
        shown ? 'opacity-100' : 'opacity-0 group-focus-within/turn:opacity-100 group-hover/turn:opacity-100 pointer-coarse:opacity-100',
        className,
      )}
    >
      {children}
    </div>
  );
}
