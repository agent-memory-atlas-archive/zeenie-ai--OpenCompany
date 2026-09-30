import { beforeEach, describe, expect, it } from 'vitest';
import { useShellDialogsStore } from '../shellDialogsStore';

beforeEach(() => {
  useShellDialogsStore.setState({
    credentialsOpen: false,
    credentialsOptions: { intent: 'manage' },
    credentialsRequestId: 0,
  });
});

describe('credentials navigation requests', () => {
  it('opens the manager without retaining the last contextual target', () => {
    useShellDialogsStore.getState().openCredentials({ providerId: 'gemini', categoryId: 'ai', intent: 'connect' });
    useShellDialogsStore.getState().closeCredentials();
    useShellDialogsStore.getState().openCredentials();

    expect(useShellDialogsStore.getState()).toMatchObject({
      credentialsOpen: true,
      credentialsOptions: { intent: 'manage' },
      credentialsRequestId: 2,
    });
    expect(useShellDialogsStore.getState().credentialsOptions).toEqual({ intent: 'manage' });
  });

  it('reopens the same target as a fresh request and defaults its intent to manage', () => {
    const options = { providerId: 'google' };
    useShellDialogsStore.getState().openCredentials(options);
    useShellDialogsStore.getState().openCredentials(options);

    expect(useShellDialogsStore.getState()).toMatchObject({
      credentialsOpen: true,
      credentialsOptions: { providerId: 'google', intent: 'manage' },
      credentialsRequestId: 2,
    });
    expect(options).toEqual({ providerId: 'google' });
  });
});
