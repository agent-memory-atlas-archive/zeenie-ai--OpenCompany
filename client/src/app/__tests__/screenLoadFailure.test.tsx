/**
 * A screen whose chunk fails to load (a tab left open across a dev-server
 * restart, or across an upgrade) shows the shell's card, not the canvas one.
 */

import { render, screen } from '@testing-library/react';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';

vi.mock('../../lib/featureFlags', () => ({ featureFlags: { normalMode: true, nodeSpecBackend: true } }));
vi.mock('../../Dashboard', () => ({ default: () => <div>Workflow editor</div> }));
vi.mock('../../features/home/HomeShell', () => {
  throw new TypeError('Failed to fetch dynamically imported module: /src/features/home/HomeShell.tsx');
});

import { useAppStore } from '../../store/useAppStore';
import { ShellModeSwitch } from '../ShellModeSwitch';

beforeEach(() => {
  vi.spyOn(console, 'error').mockImplementation(() => {});
  useAppStore.setState({ shellMode: 'normal' });
});

afterEach(() => {
  vi.restoreAllMocks();
});

describe('a screen that fails to load', () => {
  it('says the screen did not load and offers Try again and Reload', async () => {
    render(<ShellModeSwitch />);

    expect(await screen.findByText("This screen didn't load")).toBeInTheDocument();
    expect(screen.getByText("Try again, or reload the page if it still doesn't load.")).toBeInTheDocument();
    expect(screen.getByRole('button', { name: 'Try again' })).toBeInTheDocument();
    expect(screen.getByRole('button', { name: 'Reload Page' })).toBeInTheDocument();
    expect(screen.queryByText('Canvas Error Detected')).not.toBeInTheDocument();
  });
});
