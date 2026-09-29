/**
 * The Home header's Normal/Dev switch: on an employee's page Dev opens that
 * employee's workflow (the card has no menu for it); elsewhere Dev shows
 * what the editor last had.
 */

import { beforeEach, describe, expect, it, vi } from 'vitest';
import { fireEvent, render, screen } from '@testing-library/react';

vi.mock('../../../app/useShellActions', () => ({ enterDev: vi.fn(), enterNormal: vi.fn() }));
vi.mock('../../../app/ShellModeSwitch', () => ({ useShellMode: () => 'normal' }));
vi.mock('../workspace/WorkspaceButton', () => ({ WorkspaceButton: () => null }));
vi.mock('../header/ThemeButton', () => ({ ThemeButton: () => null }));

import { enterDev } from '../../../app/useShellActions';
import { HomeHeader } from '../header/HomeHeader';
import { useHomeStore } from '../state/homeStore';

describe('HomeHeader', () => {
  beforeEach(() => {
    vi.mocked(enterDev).mockClear();
    useHomeStore.setState({ sidebarOpen: true });
  });

  it('opens the employee on screen in Dev mode', () => {
    useHomeStore.setState({ view: { kind: 'employee', workflowId: 'w1' } });
    render(<HomeHeader title="Maya" scrolled={false} />);
    fireEvent.click(screen.getByRole('radio', { name: 'Dev' }));
    expect(enterDev).toHaveBeenCalledWith({ workflowId: 'w1' });
  });

  it('opens what the editor last had from the hire view', () => {
    useHomeStore.setState({ view: { kind: 'hire' } });
    render(<HomeHeader title="New employee" scrolled={false} />);
    fireEvent.click(screen.getByRole('radio', { name: 'Dev' }));
    expect(enterDev).toHaveBeenCalledWith({ workflowId: undefined });
  });
});
