/**
 * The generated UI's state store: writes copy along the path and notify,
 * a forbidden path is refused (json-render's own store would walk into
 * `__proto__`), and every change reaches onChange in canonical form.
 */

import { describe, expect, it, vi } from 'vitest';
import { createUiStateStore } from '../uiState';

describe('createUiStateStore', () => {
  it('reads and writes by JSON Pointer, immutably, and tells listeners and onChange', () => {
    const onChange = vi.fn();
    const store = createUiStateStore({ rules: { askFirst: true }, list: ['a', 'b'] }, onChange);
    const listener = vi.fn();
    store.subscribe(listener);
    const before = store.getSnapshot();

    store.set('/rules/askFirst', false);
    expect(store.get('/rules/askFirst')).toBe(false);
    expect(store.getSnapshot()).not.toBe(before);
    expect(before).toEqual({ rules: { askFirst: true }, list: ['a', 'b'] });
    expect(store.getSnapshot().list).toBe(before.list);
    expect(listener).toHaveBeenCalledTimes(1);
    expect(onChange).toHaveBeenCalledWith([{ path: '/rules/askFirst', value: false }]);

    store.set('list/1', 'z');
    expect(store.get('/list')).toEqual(['a', 'z']);
    expect(onChange).toHaveBeenLastCalledWith([{ path: '/list/1', value: 'z' }]);
  });

  it('does nothing when the value is already there', () => {
    const onChange = vi.fn();
    const store = createUiStateStore({ a: 1 }, onChange);
    const listener = vi.fn();
    store.subscribe(listener);
    store.set('/a', 1);
    store.update({ '/a': 1 });
    expect(listener).not.toHaveBeenCalled();
    expect(onChange).not.toHaveBeenCalled();
  });

  it('refuses a path through __proto__, constructor or prototype', () => {
    const onChange = vi.fn();
    const store = createUiStateStore({ a: {} }, onChange);
    const before = store.getSnapshot();
    store.set('/__proto__/x', true);
    store.set('/a/constructor/prototype/polluted', true);
    store.set('/a/prototype', 1);
    store.update({ '/__proto__/y': 1, '/constructor': 2 });
    expect(store.getSnapshot()).toBe(before);
    expect(({} as Record<string, unknown>).x).toBeUndefined();
    expect(({} as Record<string, unknown>).polluted).toBeUndefined();
    expect(Object.getPrototypeOf(store.getSnapshot())).toBe(Object.prototype);
    expect(store.get('/__proto__')).toBeUndefined();
    expect(store.get('/a/constructor')).toBeUndefined();
    expect(onChange).not.toHaveBeenCalled();
  });

  it('applies an update as one change, skipping the paths it refuses', () => {
    const onChange = vi.fn();
    const store = createUiStateStore({}, onChange);
    const listener = vi.fn();
    store.subscribe(listener);
    store.update({ '/form/name': 'Ana', '/__proto__/x': 1, '/form/age': 30 });
    expect(store.getSnapshot()).toEqual({ form: { name: 'Ana', age: 30 } });
    expect(listener).toHaveBeenCalledTimes(1);
    expect(onChange).toHaveBeenCalledWith([
      { path: '/form/name', value: 'Ana' },
      { path: '/form/age', value: 30 },
    ]);
  });

  it('stops telling a listener once it unsubscribes', () => {
    const store = createUiStateStore();
    const listener = vi.fn();
    const unsubscribe = store.subscribe(listener);
    unsubscribe();
    store.set('/a', 1);
    expect(listener).not.toHaveBeenCalled();
    expect(store.getServerSnapshot?.()).toEqual({ a: 1 });
  });
});
