/** Home's AI prompts target the shared credentials browser without losing
 *  the employee, hire draft, or Settings page underneath. */
import { beforeEach, describe, expect, it, vi } from 'vitest';

vi.mock('../orb/orb', () => ({ SPIKE: { connect: 1, settings: 1 }, spikeOrb: vi.fn() }));

import { useShellDialogsStore } from '@/stores/shellDialogsStore';
import { useHomeStore } from '../state/homeStore';

beforeEach(() => {
  useShellDialogsStore.setState({
    credentialsOpen: false,
    credentialsOptions: { intent: 'manage' },
    credentialsRequestId: 0,
  });
  useHomeStore.setState({ view: { kind: 'hire' }, settingsOpen: false, settingsTab: 'profile', settingsCategory: 'all' });
});

describe('Home AI connection navigation', () => {
  it('opens the shared AI category with connection intent', () => {
    useHomeStore.getState().openConnectAI();

    expect(useShellDialogsStore.getState()).toMatchObject({
      credentialsOpen: true,
      credentialsOptions: { categoryId: 'ai', intent: 'connect' },
    });
    expect(useHomeStore.getState().view).toEqual({ kind: 'hire' });
  });

  it('replaces an old provider target while keeping the employee and Settings location', () => {
    useHomeStore.setState({
      view: { kind: 'employee', workflowId: '4' },
      settingsOpen: true,
      settingsTab: 'connectors',
      settingsCategory: 'messages',
    });
    useShellDialogsStore.getState().openCredentials({ providerId: 'telegram', intent: 'manage' });

    useHomeStore.getState().openConnectAI();

    expect(useShellDialogsStore.getState().credentialsOptions).toEqual({ categoryId: 'ai', intent: 'connect' });
    expect(useHomeStore.getState()).toMatchObject({
      view: { kind: 'employee', workflowId: '4' },
      settingsOpen: true,
      settingsTab: 'connectors',
      settingsCategory: 'messages',
    });
  });
});
