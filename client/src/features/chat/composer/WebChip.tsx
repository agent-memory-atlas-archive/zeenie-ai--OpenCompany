/**
 * Web, beside the message box (design handoff chat, "Composer"): while it is
 * off, the employee answers the next messages without searching the web
 * (`options.web: false`; the server leaves its search tools out for those
 * runs). Shown only when the employee has a search tool.
 */

import { Globe } from 'lucide-react';
import { Toggle } from '@/components/ui/toggle';
import { cn } from '@/lib/utils';

export function WebChip({ on, name, onChange, compact = false }: { on: boolean; name: string; onChange: (next: boolean) => void; compact?: boolean }) {
  return (
    <Toggle
      variant="chips"
      size="sm"
      pressed={on}
      onPressedChange={onChange}
      aria-label="Web"
      title={on ? `${name} may search the web` : `${name} answers without searching the web`}
      className={cn(
        'gap-1.25 data-[state=on]:border-action-save-border data-[state=on]:bg-action-save-soft data-[state=on]:text-action-save-ink',
        compact && 'h-6 px-2',
      )}
    >
      <Globe aria-hidden className="size-3.25" strokeWidth={1.9} />
      Web
    </Toggle>
  );
}

export default WebChip;
