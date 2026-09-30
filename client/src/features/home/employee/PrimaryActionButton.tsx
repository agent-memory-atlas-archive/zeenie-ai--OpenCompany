/**
 * The button for an employee's main action (see useEmployeeControl): the
 * green run button for Start, Resume and connecting, a quiet one for Pause.
 * Normal mode omits the developer-workflow fallback. `className` sizes it
 * for a smaller host (the Workspace header).
 */

import { ActionButton } from '@/components/ui/action-button';
import { Button } from '@/components/ui/button';
import { cn } from '@/lib/utils';
import type { EmployeeControl } from './useEmployeeControl';

export function PrimaryActionButton({ control, className }: { control: EmployeeControl; className?: string }) {
  const { primary } = control.view;
  // Keep an in-flight lifecycle label until it settles, but never offer
  // the developer-workflow fallback from normal-mode employee controls.
  if (primary.kind === 'open_workflow' && !control.busy) return null;
  if (primary.kind === 'pause' || primary.kind === 'open_workflow') {
    return (
      <Button
        variant="quiet"
        disabled={control.busy}
        onClick={control.act}
        className={cn('h-9 gap-2 rounded-row border-border-strong px-4 font-semibold text-fg-default', className)}
      >
        {control.label}
      </Button>
    );
  }
  return (
    <ActionButton intent="run" disabled={control.busy} onClick={control.act} className={cn('h-9 rounded-row px-4', className)}>
      {control.label}
    </ActionButton>
  );
}

export default PrimaryActionButton;
