/**
 * The setup screen's action handlers (json-render's ActionProvider calls
 * them with params already resolved at the press): connect an app by the
 * name the reply used, else by the provider's name, else open Connectors;
 * a change request carries nothing the model wrote; Hire gets its params;
 * and each press reads Home's callbacks as they are now.
 */

import { describe, expect, it, vi } from 'vitest';
import { hireActionHandlers, matchProvider, type HireActionContext, type HireActions } from '../actions';

const providers = [
  { providerId: 'whatsapp', name: 'WhatsApp' },
  { providerId: 'whatsapp_business', name: 'WhatsApp Business' },
  { providerId: 'google', name: 'Google Workspace' },
  { providerId: 'stripe', name: 'Stripe' },
];

const apps = {
  gmail: { app_id: 'gmail', provider_id: 'google', name: 'Gmail', icon_ref: null, connected: false, supported: true },
  quickbooks: { app_id: 'quickbooks', provider_id: '', name: 'QuickBooks', icon_ref: null, connected: false, supported: false },
};

function homeActions() {
  return { refine: vi.fn(), openConnectors: vi.fn(), connect: vi.fn(), hire: vi.fn() } satisfies HireActions;
}

function setup(actions = homeActions()) {
  const context: HireActionContext = { apps, providers, actions };
  return { handlers: hireActionHandlers(() => context), actions, context };
}

describe('matchProvider', () => {
  it('prefers an exact name, then the longest prefix, then a substring', () => {
    expect(matchProvider('whatsapp', providers)?.providerId).toBe('whatsapp');
    expect(matchProvider('WhatsApp Business account', providers)?.providerId).toBe('whatsapp_business');
    expect(matchProvider('Google', providers)?.providerId).toBe('google');
    expect(matchProvider('workspace', providers)?.providerId).toBe('google');
  });

  it('needs three characters for a substring match', () => {
    expect(matchProvider('pe', providers)).toBeNull();
    expect(matchProvider('ripe', providers)?.providerId).toBe('stripe');
    expect(matchProvider('xyz', providers)).toBeNull();
    expect(matchProvider('', providers)).toBeNull();
  });
});

describe('hireActionHandlers', () => {
  it('handles the catalogue’s actions and leaves setState to json-render', () => {
    const { handlers } = setup();
    expect(Object.keys(handlers).sort()).toEqual(['connect_app', 'hire_employee', 'open_connectors', 'refine']);
  });

  it('connects an app the server resolved, by its provider', () => {
    const { handlers, actions } = setup();
    handlers.connect_app({ app: 'Gmail' });
    expect(actions.connect).toHaveBeenCalledWith('google', 'Gmail');
  });

  it('falls back to the provider names, and opens Connectors when nothing matches', () => {
    const { handlers, actions } = setup();
    handlers.connect_app({ app: 'Stripe' });
    expect(actions.connect).toHaveBeenCalledWith('stripe', 'Stripe');
    handlers.connect_app({ app: 'QuickBooks' });
    handlers.connect_app({});
    handlers.connect_app({ app: 'constructor' });
    expect(actions.openConnectors).toHaveBeenCalledTimes(3);
  });

  it('hands Hire its params, and a change request nothing', () => {
    const { handlers, actions } = setup();
    handlers.hire_employee({ name: 'Maya', note: 'Reports weekly' });
    expect(actions.hire).toHaveBeenCalledWith({ name: 'Maya', note: 'Reports weekly' });
    handlers.refine({ prompt: 'Only weekdays' });
    expect(actions.refine).toHaveBeenCalledTimes(1);
    expect(actions.refine).toHaveBeenCalledWith();
    handlers.open_connectors({});
    expect(actions.openConnectors).toHaveBeenCalledTimes(1);
  });

  it('reads Home’s callbacks at each press', () => {
    const first = homeActions();
    const later = homeActions();
    let context: HireActionContext = { apps, providers, actions: first };
    const handlers = hireActionHandlers(() => context);
    context = { ...context, actions: later };
    handlers.hire_employee({});
    expect(first.hire).not.toHaveBeenCalled();
    expect(later.hire).toHaveBeenCalledTimes(1);
  });
});
