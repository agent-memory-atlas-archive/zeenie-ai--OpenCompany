/**
 * Shown above the message box while saved changes wait for a restart
 * (`pending_changes`: a tool or skill the employee added in Talk, or an
 * edit in Dev mode). The employee already uses what was added in this
 * conversation; Apply restarts them on the latest setup so the rest of
 * their work does too.
 */

import { ActionButton } from '@/components/ui/action-button';
import { restartDraftsWarning } from '../data/presentation';
import type { EmployeeSummary } from '../data/schemas';
import { useApplyChanges } from '../data/talk';
import { pillToast } from '../ui/pillToast';

function applyErrorMessage(code: string, name: string): string {
  switch (code) {
    case 'not_found':
      return 'This employee is no longer on the team.';
    case 'conflict':
      return `${name} is in the middle of a change. Try again in a moment.`;
    case 'restart_failed':
      return `${name} couldn’t restart. Try starting them again.`;
    default:
      return 'That did not work. Try again.';
  }
}

export function PendingChangesNotice({ employee }: { employee: EmployeeSummary }) {
  const apply = useApplyChanges();
  const { name, pending_approvals: drafts } = employee;

  const onApply = () =>
    apply.mutate(employee.workflow_id, {
      onSuccess: () => pillToast(`${name} restarted with the new abilities.`),
      onError: (error) => pillToast(applyErrorMessage(error.message, name), { tone: 'error' }),
    });

  return (
    <div className="flex flex-wrap items-center gap-3 rounded-card border border-border-default bg-bg-panel px-4 py-3">
      <p className="m-0 min-w-60 flex-1 text-sm text-fg-default">
        {name} has new abilities for this conversation. Apply to make them part of all their work (restarts {name} and
        clears this conversation).
        {drafts > 0 && ` ${restartDraftsWarning(name, drafts)}`}
      </p>
      <ActionButton intent="config" disabled={apply.isPending} onClick={onApply} className="h-9 rounded-row px-4">
        {apply.isPending ? 'Applying…' : 'Apply'}
      </ActionButton>
    </div>
  );
}

export default PendingChangesNotice;
