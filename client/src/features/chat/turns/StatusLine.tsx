/**
 * What a working employee is doing, under their turn (design handoff chat,
 * "Streaming"): three dots, a label ("Thinking", "Writing"), how fast the
 * answer comes, and, while it can be stopped, that Esc stops it. A status
 * region, so a screen reader hears the label change (the rate and the hint
 * are left out of it).
 */

import { cn } from '@/lib/utils';

export function StatusLine({
  label,
  note,
  rate,
  stopHint = false,
  compact = false,
}: {
  label: string;
  note?: string | null;
  rate?: string | null;
  stopHint?: boolean;
  compact?: boolean;
}) {
  return (
    <div className={cn('flex min-w-0 items-center gap-2 font-mono text-xs text-fg-faint', compact && 'text-2xs')}>
      <span aria-hidden className="opencompany-dots flex shrink-0 gap-0.75">
        <span className="size-1 rounded-full bg-action-tools-ink" />
        <span className="size-1 rounded-full bg-action-tools-ink" />
        <span className="size-1 rounded-full bg-action-tools-ink" />
      </span>
      <span role="status" className="shrink-0 text-fg-muted">
        {label}
      </span>
      {rate && (
        <span aria-hidden className="shrink-0">
          {rate}
        </span>
      )}
      {note && <span className="min-w-0 truncate font-body">{note}</span>}
      {stopHint && (
        <span aria-hidden className="ml-auto flex shrink-0 items-center gap-1.5">
          Stop
          <kbd className="rounded-sm border border-border-default bg-bg-panel px-1.25 py-px font-mono text-2xs text-fg-muted">Esc</kbd>
        </span>
      )}
    </div>
  );
}
