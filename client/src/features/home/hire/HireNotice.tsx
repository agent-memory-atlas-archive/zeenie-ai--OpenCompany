/**
 * What a hire could not set up as asked (a tool left out while they ask
 * before sending, a skill an employee cannot be given), in the server's
 * words, shown once on the new employee's page. It goes when the owner
 * dismisses it or opens another view (homeStore's hireNotice).
 */

import { Info, X } from 'lucide-react';
import { Button } from '@/components/ui/button';
import { useHomeStore } from '../state/homeStore';

export function HireNotice({ workflowId }: { workflowId: string }) {
  const notice = useHomeStore((s) => (s.hireNotice?.workflowId === workflowId ? s.hireNotice : null));
  const setHireNotice = useHomeStore((s) => s.setHireNotice);
  if (!notice) return null;
  return (
    <div
      role="status"
      className="flex w-full items-start gap-3 rounded-card border border-status-ready-border bg-status-ready-fill px-4 py-3.5"
    >
      <Info aria-hidden className="mt-0.5 size-4 shrink-0 text-status-ready-ink" />
      <div className="flex min-w-0 flex-1 flex-col gap-1">
        <span className="text-base font-semibold text-fg-default">A few notes on {notice.name}’s setup</span>
        <ul className="m-0 flex list-none flex-col gap-0.5 p-0">
          {notice.warnings.map((warning) => (
            <li key={warning} className="text-sm text-pretty text-fg-muted">
              {warning}
            </li>
          ))}
        </ul>
      </div>
      <Button
        variant="quiet"
        size="icon-sm"
        aria-label="Dismiss"
        title="Dismiss"
        onClick={() => setHireNotice(null)}
        className="rounded-lg"
      >
        <X className="size-3.5" />
      </Button>
    </div>
  );
}

export default HireNotice;
