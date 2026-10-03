/**
 * Normal mode's header (design handoff "Main header"; chat "Header"): with
 * the sidebar collapsed, an open button and the logo. Then the view: while
 * hiring, its title; on an employee's page, who they are (avatar, name,
 * role and apps, their status) and New conversation. At the end the
 * Workspace pill, the Normal/Dev switch (on an employee's page, Dev opens
 * their workflow) and the theme button. The bottom border appears only once
 * the content has scrolled, so the hero reads as one surface.
 */

import { PanelLeft } from 'lucide-react';
import { OcLogo } from '@/components/brand/Logo';
import { ModeToggle } from '@/components/shell/ModeToggle';
import { Button } from '@/components/ui/button';
import { cn } from '@/lib/utils';
import { useWorkflowControlPending } from '@/stores/workflowControlStore';
import { presentEmployee } from '../data/presentation';
import type { EmployeeSummary } from '../data/schemas';
import { useHomeStore } from '../state/homeStore';
import { Avatar, StatusPill } from '../ui/primitives';
import { WorkspaceButton } from '../workspace/WorkspaceButton';
import { NewConversationButton } from './NewConversationButton';
import { ThemeButton } from './ThemeButton';

/** "Receptionist · WhatsApp, Google Calendar". */
function subtitleOf(employee: EmployeeSummary): string {
  const apps = employee.apps.map((app) => app.name).join(', ');
  return [employee.role, apps].filter(Boolean).join(' · ');
}

function Identity({ employee }: { employee: EmployeeSummary }) {
  const pending = useWorkflowControlPending(employee.workflow_id);
  const { pill, pulse } = presentEmployee(employee, pending);
  const subtitle = subtitleOf(employee);
  return (
    <div className="flex min-w-0 items-center gap-2.5 px-1">
      <Avatar name={employee.name} colorRole={employee.color_role} size="sm" />
      <div className="flex min-w-0 flex-col">
        <h2 className="m-0 truncate text-base font-semibold text-fg-default">{employee.name}</h2>
        {subtitle && <span className="truncate text-xs text-fg-muted">{subtitle}</span>}
      </div>
      <StatusPill tone={pill.tone} label={pill.label} pulse={pulse} size="compact" />
    </div>
  );
}

export function HomeHeader({ title, employee, scrolled }: { title: string; employee: EmployeeSummary | null; scrolled: boolean }) {
  const sidebarOpen = useHomeStore((s) => s.sidebarOpen);
  const toggleSidebar = useHomeStore((s) => s.toggleSidebar);
  const logoPulse = useHomeStore((s) => s.logoPulse);
  const employeeId = useHomeStore((s) => (s.view.kind === 'employee' ? s.view.workflowId : undefined));

  return (
    <header
      className={cn(
        'relative z-10 flex h-(--h-home-header) shrink-0 items-center gap-2 border-b px-3.5 transition-colors duration-(--dur-slow)',
        scrolled ? 'border-border-default' : 'border-transparent',
      )}
    >
      {!sidebarOpen && (
        <>
          <Button variant="quiet" size="icon" onClick={toggleSidebar} aria-label="Open sidebar" title="Open sidebar" className="rounded-lg">
            <PanelLeft className="size-4.25" strokeWidth={1.75} />
          </Button>
          <OcLogo size="header" pulseNonce={logoPulse} className="pr-1.5 pl-0.5" />
          <span aria-hidden className="h-5 w-px bg-border-default" />
        </>
      )}
      {employee ? <Identity employee={employee} /> : <h2 className="truncate px-1.5 text-lead font-semibold text-fg-default">{title}</h2>}
      <div className="ml-auto flex shrink-0 items-center gap-2">
        {employee && employee.talk.state === 'on' && <NewConversationButton employee={employee} />}
        <WorkspaceButton />
        <ModeToggle workflowId={employeeId} />
        <ThemeButton />
      </div>
    </header>
  );
}

export default HomeHeader;
