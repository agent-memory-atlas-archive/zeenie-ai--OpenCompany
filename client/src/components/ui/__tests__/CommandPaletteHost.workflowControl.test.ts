import { describe, expect, it, vi } from 'vitest';
import type { WorkflowControlStatus } from '../../../contexts/WebSocketContext';
import {
  buildWorkflowLifecycleCommands,
  type CommandPaletteHandlers,
} from '../CommandPaletteHost';

const control = (
  overrides: Partial<WorkflowControlStatus> = {},
): WorkflowControlStatus => ({
  workflow_id: 'workflow-1',
  generation: 1,
  state: 'running',
  revision: 1,
  active_count: 0,
  in_flight_count: 0,
  queued_count: 0,
  can_start: false,
  can_pause: false,
  can_resume: false,
  can_reset: false,
  can_edit: false,
  ...overrides,
});

const handlers = (
  workflowControl: WorkflowControlStatus,
  overrides: Partial<CommandPaletteHandlers> = {},
) => ({
  start: vi.fn(),
  pause: vi.fn(),
  resume: vi.fn(),
  reset: vi.fn(),
  workflowControl,
  ...overrides,
});

describe('command palette workflow lifecycle capabilities', () => {
  it.each([
    ['run.start', { state: 'failed' as const, can_start: true }],
    ['run.pause', { state: 'paused' as const, can_pause: true }],
    ['run.resume', { state: 'running' as const, can_resume: true }],
  ])('exposes %s from its capability rather than the generic state', (id, overrides) => {
    const commands = buildWorkflowLifecycleCommands(handlers(control(overrides)));
    expect(commands.map((command) => command.id)).toContain(id);
  });

  it('does not infer a start action when no lifecycle capability allows one', () => {
    const commands = buildWorkflowLifecycleCommands(handlers(control({
      state: 'never_started',
      can_start: false,
    })));
    expect(commands.map((command) => command.id)).not.toContain('run.start');
  });

  it('offers only Reset while a local Stop is pending', () => {
    const reset = vi.fn();
    const commands = buildWorkflowLifecycleCommands(handlers(
      control({ can_pause: true, can_reset: true }),
      { workflowControlPending: { action: 'pause', state: 'pausing' }, reset },
    ));
    expect(commands.map(command => command.id)).toEqual(['run.reset']);
    commands[0].onRun();
    expect(reset).toHaveBeenCalledOnce();
  });

  it.each([
    { action: 'start' as const, state: 'starting' as const },
    { action: 'resume' as const, state: 'resuming' as const },
    { action: 'reset' as const, state: 'resetting' as const },
  ])('suppresses lifecycle actions while $action is pending', (pending) => {
    const commands = buildWorkflowLifecycleCommands(handlers(
      control({ can_pause: true, can_reset: true }),
      { workflowControlPending: pending },
    ));
    expect(commands).toEqual([]);
  });

  it('does not offer Reset during Stop without the server capability', () => {
    const commands = buildWorkflowLifecycleCommands(handlers(
      control({ can_reset: false }),
      { workflowControlPending: { action: 'pause', state: 'pausing' } },
    ));
    expect(commands).toEqual([]);
  });

  it.each([
    ['pausing' as const, 'run.pause', 'Stopping Workflow…'],
    ['resuming' as const, 'run.resume', 'Retry Resume Workflow'],
    ['resetting' as const, 'run.reset', 'Retry Reset Workflow Execution'],
  ])('makes an authoritative %s transition retryable', (state, id, label) => {
    const commands = buildWorkflowLifecycleCommands(handlers(control({
      state,
      can_reset: state === 'resetting',
    })));
    expect(commands).toEqual(expect.arrayContaining([
      expect.objectContaining({ id, label }),
    ]));
  });

  it('allows Reset to recover an authoritative Start transition when permitted', () => {
    const commands = buildWorkflowLifecycleCommands(handlers(control({
      state: 'starting',
      can_reset: true,
    })));
    expect(commands.map((command) => command.id)).toEqual(['run.reset']);
  });
});
