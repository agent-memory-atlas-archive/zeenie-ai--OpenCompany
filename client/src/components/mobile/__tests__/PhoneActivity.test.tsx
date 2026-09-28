import { act, cleanup, fireEvent, render, screen, within } from '@testing-library/react';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { PhoneActivity } from '../PhoneActivity';

beforeEach(() => {
  vi.useFakeTimers();
  vi.setSystemTime(new Date('2026-09-28T12:00:00Z'));
});
afterEach(() => { cleanup(); vi.useRealTimers(); });

describe('Android activity', () => {
  it('shows a quiet model wait and retains legacy operations in collapsed technical details', () => {
    const now = Date.now() / 1000;
    render(<PhoneActivity task={{ run_id: 'run', phase: 'Waiting for model', started_at: now - 65,
      phase_started_at: now - 40, updated_at: now - 40, model: 'test-model', provider: 'openai',
      activity: [{ at: now - 42, message: 'Reading screen and accessibility tree: completed (0.2s)', step: 1 }] }} />);
    expect(screen.getByText(/No new activity for 40s/)).toBeInTheDocument();
    expect(screen.getByText(/Reading screen and accessibility tree/)).not.toBeVisible();
    expect(screen.getByText(/openai test-model/)).not.toBeVisible();
    expect(screen.getByLabelText('Android technical activity')).not.toHaveAttribute('open');
    expect(screen.queryByRole('list', { name: 'Recent Android actions' })).toBeNull();
    fireEvent.click(screen.getByText('Technical activity'));
    expect(screen.getByText(/Reading screen and accessibility tree/)).toBeVisible();
    expect(screen.getByText(/openai test-model/)).toBeVisible();
  });
  it('retains the failure location after the task ends', () => {
    render(<PhoneActivity task={{ run_id: 'run', status: 'failed', phase: 'Waiting for model',
      started_at: 100, finished_at: 180, error_code: 'timeout' }} />);
    expect(screen.getByText(/Stopped during Waiting for model · timeout/)).toBeInTheDocument();
    expect(screen.getByText(/80s total/)).toBeInTheDocument();
    expect(screen.queryByText(/No new activity/)).toBeNull();
  });

  it('keeps the current subtask visible across model waits without inventing actions', () => {
    const now = Date.now() / 1000;
    const task = { run_id: 'run', phase: 'Using phone', started_at: now - 20,
      phase_started_at: now - 5, updated_at: now, steps: 4, max_steps: 40,
      current_goal: 'Find the display settings',
      agent_activity: [{ at: now - 5, step: 4, kind: 'action', message: 'Open Settings',
        detail: 'Find the display controls', state: 'completed', outcome: 'Settings opened', duration_ms: 1200 }],
    };
    const view = render(<PhoneActivity task={task} />);
    expect(within(screen.getByRole('region', { name: 'Current Android subtask' })).getByText('Find the display settings')).toBeVisible();
    view.rerender(<PhoneActivity task={{ ...task, phase: 'Waiting for model', phase_started_at: now }} />);
    act(() => { vi.advanceTimersByTime(5000); });
    expect(screen.getByText('Find the display settings')).toBeVisible();
    expect(screen.getByText(/AI activity · Waiting for model/)).toBeVisible();
    expect(screen.getByText(/Current phase: 5s/)).toBeVisible();
    expect(within(screen.getByRole('list', { name: 'Recent Android actions' })).getAllByRole('listitem')).toHaveLength(1);
    expect(screen.getByText('Settings opened', { exact: false })).toBeVisible();
    expect(screen.getByText(/Engine step 4 \/ 40/)).toBeVisible();
  });

  it('shows plan statuses and the reason a subtask failed', () => {
    const now = Date.now() / 1000;
    render(<PhoneActivity task={{ run_id: 'run', started_at: now, updated_at: now,
      current_goal: 'Change brightness', plan: [
        { id: '1', description: 'Open Settings', status: 'success' },
        { id: '2', description: 'Change brightness', status: 'pending' },
        { id: '3', description: 'Enable dark mode', status: 'not_started' },
        { id: '4', description: 'Set a schedule', status: 'failure', reason: 'Scheduling is unavailable on this device' },
      ],
    }} />);
    const details = screen.getByLabelText('Android task plan');
    expect(details).not.toHaveAttribute('open');
    expect(screen.getByText('Plan · 1 / 4 done')).toBeVisible();
    fireEvent.click(screen.getByText('Plan · 1 / 4 done'));
    const plan = within(details);
    const rows = plan.getAllByRole('listitem');
    expect(rows[0]).toHaveTextContent('Done · Open Settings');
    expect(rows[1]).toHaveTextContent('In progress · Change brightness');
    expect(rows[2]).toHaveTextContent('Not started · Enable dark mode');
    expect(rows[3]).toHaveTextContent('Failed · Set a schedule');
    expect(plan.getByText('Scheduling is unavailable on this device')).toBeVisible();
  });

  it('shows a failed action with intent, actual result, duration and engine step', () => {
    const now = Date.now() / 1000;
    render(<PhoneActivity task={{ run_id: 'run', started_at: now - 20, updated_at: now,
      agent_activity: [{ action_id: 'action-1', at: now - 5, step: 3, kind: 'action',
        message: 'Open display settings', detail: 'Find the brightness controls',
        state: 'failed', outcome: 'Display option was not found', duration_ms: 1350 }],
      activity: [{ at: now, step: 3, message: 'Accessibility tree: completed (0.2s)' }],
    }} />);
    const actions = within(screen.getByRole('list', { name: 'Recent Android actions' }));
    expect(actions.getByText('Open display settings')).toBeVisible();
    expect(actions.getByText('Find the brightness controls')).toBeVisible();
    expect(actions.getByText('Result: Display option was not found')).toBeVisible();
    expect(actions.getByRole('listitem')).toHaveTextContent('+15s · Engine step 3 · Failed · 1.4s');
    expect(screen.getByText(/Accessibility tree: completed/)).not.toBeVisible();
  });

  it('updates an action in place when its result arrives and preserves its start time', () => {
    const now = Date.now() / 1000;
    const action = { action_id: 'tap-1', at: now - 2, step: 2, kind: 'action', message: 'Open Wi-Fi settings', state: 'started' };
    const task = { run_id: 'run', started_at: now - 10, updated_at: now, agent_activity: [action], current_action: action };
    const view = render(<PhoneActivity task={task} />);
    const list = screen.getByRole('list', { name: 'Recent Android actions' });
    expect(within(list).getByRole('listitem')).toHaveTextContent('In progress · 2.0s');
    const completed = { ...action, state: 'completed', finished_at: now, outcome: 'Wi-Fi settings opened', duration_ms: 2000 };
    view.rerender(<PhoneActivity task={{ ...task, current_action: completed, agent_activity: [completed] }} />);
    expect(within(list).getAllByRole('listitem')).toHaveLength(1);
    expect(within(list).getByRole('listitem')).toHaveTextContent('+8s · Engine step 2 · Completed · 2.0s');
    expect(within(list).getByText('Result: Wi-Fi settings opened')).toBeVisible();
  });

  it('shows a current action when a snapshot has no semantic history yet', () => {
    const now = Date.now() / 1000;
    render(<PhoneActivity task={{ run_id: 'run', started_at: now, updated_at: now,
      current_action: { at: now, step: 1, kind: 'action', message: 'Open the requested app', state: 'started' },
    }} />);
    expect(screen.getByText('Open the requested app')).toBeVisible();
  });

  it('marks an interrupted action as unconfirmed rather than claiming it succeeded', () => {
    const now = Date.now() / 1000;
    render(<PhoneActivity task={{ run_id: 'run', started_at: now - 2, updated_at: now,
      current_action: { at: now - 2, step: 1, kind: 'action', message: 'Open the requested app',
        state: 'returned', outcome: 'Action interrupted before the tool confirmed its outcome.', duration_ms: 2000 },
    }} />);
    const actions = within(screen.getByRole('list', { name: 'Recent Android actions' }));
    expect(actions.getByText('Unconfirmed')).toBeVisible();
    expect(actions.getByText('Result: Action interrupted before the tool confirmed its outcome.')).toBeVisible();
    expect(actions.queryByText('Completed')).toBeNull();
    expect(actions.queryByText('Failed')).toBeNull();
  });

  it('adds the quiet warning as time passes without replacing the reported goal', () => {
    const now = Date.now() / 1000;
    render(<PhoneActivity task={{ run_id: 'run', phase: 'Waiting for model', started_at: now,
      phase_started_at: now, updated_at: now, current_goal: 'Check whether dark mode is enabled' }} />);
    expect(screen.queryByText(/No new activity/)).toBeNull();
    act(() => { vi.advanceTimersByTime(30_000); });
    expect(screen.getByText(/No new activity for 30s/)).toBeVisible();
    expect(screen.getByText('Check whether dark mode is enabled')).toBeVisible();
    expect(screen.queryByRole('list', { name: 'Recent Android actions' })).toBeNull();
  });
});
