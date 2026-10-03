/**
 * What a working employee is doing, under their turn (design handoff chat,
 * "Streaming": three dots and a label). A status region, so a screen reader
 * hears it change.
 */

import { cn } from '@/lib/utils';

export function StatusLine({ label, note, compact = false }: { label: string; note?: string | null; compact?: boolean }) {
  return (
    <div role="status" className={cn('flex min-w-0 items-center gap-2 font-mono text-xs text-fg-faint', compact && 'text-2xs')}>
      <span aria-hidden className="opencompany-dots flex shrink-0 gap-0.75">
        <span className="size-1 rounded-full bg-action-tools-ink" />
        <span className="size-1 rounded-full bg-action-tools-ink" />
        <span className="size-1 rounded-full bg-action-tools-ink" />
      </span>
      <span className="shrink-0 text-fg-muted">{label}</span>
      {note && <span className="min-w-0 truncate font-body">{note}</span>}
    </div>
  );
}
