/**
 * One employee's page (design handoff "Employee view"): their name under
 * the orb, then the conversation with them (EmployeeTalk), whose message
 * box stays pinned to the bottom of the page. Only a new hire's notes
 * (HireNotice) ever sit between the two.
 *
 * What to act on lives in the conversation, and only while there is
 * something to do: the drafts to check after the messages; above the
 * message box, their main action while they cannot read messages (Resume,
 * Start, or connect what they are missing; useEmployeeControl) and Help in
 * browser while they wait for the owner there. Pausing them is the
 * Workspace's, which the header's Workspace pill opens on the employee on
 * screen; the header's Dev switch opens their workflow.
 */

import { useEffect, useRef } from 'react';
import { Button } from '@/components/ui/button';
import { Skeleton } from '@/components/ui/skeleton';
import { useEmployeeDetailQuery, useEmployeesQuery } from '../data/employees';
import { useLiveTask } from '../data/liveTask';
import type { EmployeeSummary } from '../data/schemas';
import { HireNotice } from '../hire/HireNotice';
import { OrbSlot } from '../orb/OrbSlot';
import { SPIKE, spikeOrb } from '../orb/orb';
import { useHomeStore } from '../state/homeStore';
import { DraftsSection } from './DraftsSection';
import { EmployeeTalk } from './EmployeeTalk';
import { useEmployeeControl } from './useEmployeeControl';

/** The orb stirs when their work moves on (design handoff "Live work"). */
function useTaskSpike(employee: EmployeeSummary): void {
  const live = useLiveTask(employee);
  const text = (live ?? employee.task)?.text;
  const shown = useRef(text);
  useEffect(() => {
    if (shown.current === text) return;
    shown.current = text;
    if (text) spikeOrb(SPIKE.task);
  }, [text]);
}

function EmployeePage({ employee, onConnect }: { employee: EmployeeSummary; onConnect: (providerId: string) => void }) {
  const control = useEmployeeControl(employee, onConnect);
  useTaskSpike(employee);
  const paused = employee.control.state === 'paused' || employee.control.state === 'pausing';
  return (
    <section aria-label={employee.name} className="flex w-full max-w-(--w-employee-card) flex-1 flex-col items-center gap-4">
      <div className="flex flex-col items-center gap-2">
        <OrbSlot size="employee" />
        <h2 className="text-center text-title font-semibold tracking-[-0.02em] break-words text-fg-default">{employee.name}</h2>
      </div>
      <HireNotice workflowId={employee.workflow_id} />
      <EmployeeTalk
        employee={employee}
        control={control}
        drafts={<DraftsSection workflowId={employee.workflow_id} employeeName={employee.name} paused={paused} />}
      />
    </section>
  );
}

export function EmployeeView({ workflowId, onConnect }: { workflowId: string; onConnect: (providerId: string) => void }) {
  const list = useEmployeesQuery();
  const fromList = list.data?.find((e) => e.workflow_id === workflowId) ?? null;
  // Only when the list has no row for it (just hired, or the list failed).
  const detailId = fromList || list.isPending ? null : workflowId;
  const detail = useEmployeeDetailQuery(detailId);
  const showHire = useHomeStore((s) => s.showHire);
  const employee = fromList ?? detail.data ?? null;

  if (!employee) {
    if (list.isPending || (detailId !== null && detail.isPending)) {
      return (
        <section aria-busy className="flex w-full max-w-(--w-employee-card) flex-col items-center gap-2">
          <OrbSlot size="employee" />
          <Skeleton className="h-7 w-44 rounded-row" />
        </section>
      );
    }
    return (
      <section className="flex w-full max-w-(--w-employee-card) flex-col items-center gap-3 pt-16 text-center">
        <p className="m-0 text-md text-fg-default">
          {detail.isError ? 'Couldn’t load this employee.' : 'This employee is no longer on the team.'}
        </p>
        <div className="flex gap-2">
          {detail.isError && (
            <Button variant="quiet" onClick={() => void detail.refetch()} className="border-border-default text-fg-default">
              Try again
            </Button>
          )}
          <Button variant="quiet" onClick={() => showHire()} className="border-border-default text-fg-default">
            Back to hiring
          </Button>
        </div>
      </section>
    );
  }

  return <EmployeePage employee={employee} onConnect={onConnect} />;
}

export default EmployeeView;
