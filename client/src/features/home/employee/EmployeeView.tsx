/**
 * One employee (design handoff "Employee view"): their card, then the
 * conversation with them (EmployeeTalk), whose message box stays pinned to
 * the bottom of the page.
 *
 * The card shows the server's summary, which the server re-sends the
 * moment the control plane moves: who they are, what they are doing (unless
 * the page already says it), the drafts waiting for the owner, the apps
 * they use, how much they did today, and what to do next (Pause, Resume,
 * Start, or connect what they are missing; useEmployeeControl, shared with
 * the message box). "Watch live" opens the Workspace on this employee
 * ("Help in browser", on its Browser tab, while the agent is waiting for the
 * owner). The header's Dev switch opens their workflow in Dev mode.
 */

import { Monitor } from 'lucide-react';
import { useLayoutEffect, useRef, type RefObject } from 'react';
import { ActionButton } from '@/components/ui/action-button';
import { Button } from '@/components/ui/button';
import { Skeleton } from '@/components/ui/skeleton';
import { animate } from '@/lib/motion';
import { cn } from '@/lib/utils';
import { useEmployeeDetailQuery, useEmployeesQuery } from '../data/employees';
import { useLiveTask } from '../data/liveTask';
import type { EmployeeSummary } from '../data/schemas';
import { HireNotice } from '../hire/HireNotice';
import { OrbSlot } from '../orb/OrbSlot';
import { SPIKE, spikeOrb } from '../orb/orb';
import { useHomeStore } from '../state/homeStore';
import { AppMark, Avatar, MicroLabel, StatusPill } from '../ui/primitives';
import { DraftsSection } from './DraftsSection';
import { EmployeeTalk } from './EmployeeTalk';
import { PrimaryActionButton } from './PrimaryActionButton';
import { useEmployeeControl, type EmployeeControl } from './useEmployeeControl';

/** What they are doing, or none when the page already says it. */
function TaskBox({ employee }: { employee: EmployeeSummary }) {
  const live = useLiveTask(employee);
  const task = live ?? employee.task;
  const taskText = task?.text;
  const textRef = useRef<HTMLSpanElement>(null);
  const shownText = useRef(taskText);
  // A new task blurs in and the card's border flashes green (design handoff "Live work").
  useLayoutEffect(() => {
    if (shownText.current === taskText) return;
    shownText.current = taskText;
    if (!taskText) return;
    spikeOrb(SPIKE.task);
    const text = textRef.current;
    animate(
      text,
      [
        { opacity: 0, transform: 'translateY(8px)', filter: 'blur(3px)' },
        { opacity: 1, transform: 'none', filter: 'blur(0)' },
      ],
      { duration: 520, easing: 'spring', fill: 'backwards' },
    );
    animate(text?.closest('[data-employee]'), [{ borderColor: 'var(--glow-task)' }, { borderColor: 'var(--border-default)' }], {
      duration: 'glow',
      fill: 'none',
    });
  }, [taskText]);
  if (!task) return null;
  return (
    <div className="flex flex-col gap-1.5 rounded-card border border-border-default bg-bg-app px-4 py-3.5">
      <MicroLabel>{task.label}</MicroLabel>
      <span ref={textRef} data-task={employee.workflow_id} className="text-md text-fg-default" aria-live="polite">
        {task.text}
      </span>
    </div>
  );
}

function EmployeeCard({
  employee,
  control,
  cardRef,
}: {
  employee: EmployeeSummary;
  control: EmployeeControl;
  cardRef: RefObject<HTMLDivElement | null>;
}) {
  const openWorkspace = useHomeStore((s) => s.openWorkspace);
  const setWorkspaceTab = useHomeStore((s) => s.setWorkspaceTab);
  const helpInBrowser = employee.browser_request !== null;
  const countRef = useRef<HTMLSpanElement>(null);
  const { view } = control;
  // A role that only repeats the name ("AI Assistant", "AI assistant") says nothing.
  const role = employee.role.trim().toLowerCase() === employee.name.trim().toLowerCase() ? '' : employee.role;

  // One more done today: the count bumps in green.
  const shownDone = useRef(employee.done_today);
  useLayoutEffect(() => {
    const previous = shownDone.current;
    shownDone.current = employee.done_today;
    if (employee.done_today <= previous) return;
    animate(
      countRef.current,
      [
        { transform: 'none', color: 'var(--status-working-ink)' },
        { transform: 'translateY(-3px) scale(1.25)', color: 'var(--status-working-ink)', offset: 0.35 },
        { transform: 'none', color: 'var(--fg-default)' },
      ],
      { duration: 700, fill: 'none' },
    );
  }, [employee.done_today]);

  return (
    <div
      ref={cardRef}
      data-employee={employee.workflow_id}
      className="flex w-full flex-col gap-4.5 rounded-draft border border-border-default bg-bg-elevated p-5.5 shadow-float"
    >
      <div className="flex flex-wrap items-center gap-3.5">
        <Avatar name={employee.name} colorRole={employee.color_role} size="lg" />
        <div className="flex min-w-40 flex-1 flex-col gap-0.75">
          <span className="text-title font-semibold tracking-[-0.02em] text-fg-default">{employee.name}</span>
          {role && <span className="text-base text-fg-muted">{role}</span>}
        </div>
        <StatusPill tone={view.pill.tone} label={view.pill.label} pulse={view.pulse} />
      </div>

      <TaskBox employee={employee} />

      <DraftsSection
        workflowId={employee.workflow_id}
        employeeName={employee.name}
        paused={employee.control.state === 'paused' || employee.control.state === 'pausing'}
      />

      {(employee.apps.length > 0 || employee.done_today > 0) && (
        <div className="flex flex-wrap items-center gap-2">
          {employee.apps.map((app) => (
            <span
              key={app.app_id}
              className={cn(
                'flex h-7.5 items-center gap-1.5 rounded-pill border bg-bg-app pr-2.5 pl-1 text-meta text-fg-default',
                app.connected ? 'border-border-default' : 'border-status-attention-border',
              )}
              title={app.connected ? `${app.name} is connected` : `${app.name} is not connected`}
            >
              <AppMark name={app.name} iconRef={app.icon_ref} size="xs" />
              {app.name}
            </span>
          ))}
          {employee.done_today > 0 && (
            <span className="ml-auto font-mono text-sm whitespace-nowrap text-fg-muted">
              <span ref={countRef} data-count={employee.workflow_id} className="inline-block text-fg-default">
                {employee.done_today}
              </span>{' '}
              done today
            </span>
          )}
        </div>
      )}

      {employee.unsupported_apps.length > 0 && (
        <p className="m-0 text-xs text-fg-muted">
          Not available yet: {employee.unsupported_apps.join(', ')}. They work without it for now.
        </p>
      )}

      <div className="flex flex-wrap gap-2">
        <PrimaryActionButton control={control} />
        <ActionButton
          intent="tools"
          onClick={() => {
            openWorkspace(employee.workflow_id);
            if (helpInBrowser) setWorkspaceTab('browser');
          }}
          className="h-9 gap-2 rounded-row px-3.5"
        >
          <Monitor aria-hidden className="size-3.5" />
          {helpInBrowser ? 'Help in browser' : 'Watch live'}
        </ActionButton>
      </div>
    </div>
  );
}

function EmployeePage({ employee, onConnect }: { employee: EmployeeSummary; onConnect: (providerId: string) => void }) {
  const cardRef = useRef<HTMLDivElement>(null);
  const control = useEmployeeControl(employee, onConnect, cardRef);
  return (
    <section aria-label={employee.name} className="flex w-full max-w-(--w-employee-card) flex-1 flex-col items-center gap-4.5">
      <OrbSlot size="employee" />
      <EmployeeCard employee={employee} control={control} cardRef={cardRef} />
      <HireNotice workflowId={employee.workflow_id} />
      <EmployeeTalk employee={employee} control={control} />
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
        <section aria-busy className="flex w-full max-w-(--w-employee-card) flex-col items-center gap-4.5">
          <OrbSlot size="employee" />
          <Skeleton className="h-72 w-full rounded-draft" />
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
