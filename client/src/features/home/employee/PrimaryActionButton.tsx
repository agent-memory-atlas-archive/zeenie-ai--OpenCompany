/**
 * The button for an employee's main action (see useEmployeeControl): the
 * green run button for Start, Resume and connecting, a quiet one for Pause
 * and Open in Dev mode.
 */

import { Code } from 'lucide-react';
import { ActionButton } from '@/components/ui/action-button';
import { Button } from '@/components/ui/button';
import type { EmployeeControl } from './useEmployeeControl';

export function PrimaryActionButton({ control }: { control: EmployeeControl }) {
  const { primary } = control.view;
  if (primary.kind === 'pause' || primary.kind === 'open_workflow') {
    return (
      <Button
        variant="quiet"
        disabled={control.busy}
        onClick={control.act}
        className="h-9 gap-2 rounded-row border-border-strong px-4 font-semibold text-fg-default"
      >
        {primary.kind === 'open_workflow' && !control.busy && <Code aria-hidden className="size-3.25" />}
        {control.label}
      </Button>
    );
  }
  return (
    <ActionButton intent="run" disabled={control.busy} onClick={control.act} className="h-9 rounded-row px-4">
      {control.label}
    </ActionButton>
  );
}

export default PrimaryActionButton;
