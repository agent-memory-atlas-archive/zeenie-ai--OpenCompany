/**
 * The state one generated UI reads and writes: a json-render StateStore
 * (pass it to JSONUIProvider as `store`) with the forbidden-path guard on
 * every write.
 *
 * json-render's own createStateStore writes through any path, `__proto__`
 * included. This one refuses whatever paths.ts refuses, copies along the
 * path on a write (snapshots stay immutable, as useSyncExternalStore
 * needs), keeps arrays as arrays, and reports every change to `onChange`
 * with its path in canonical form. json-render's StateProvider reports
 * nothing for a store it is handed, so this is where a host learns what the
 * owner changed (a draft to keep, a chat message's state to send).
 *
 * Create one per spec, and a new one for a new version of it.
 */

import type { StateModel, StateStore } from '@json-render/core';
import { joinStatePath, parseStatePath, readPath, writePath } from './paths';

export interface UiStateChange {
  path: string;
  value: unknown;
}

export function createUiStateStore(
  initialState: StateModel = {},
  onChange?: (changes: UiStateChange[]) => void,
): StateStore {
  let state: StateModel = { ...initialState };
  const listeners = new Set<() => void>();

  /** The state with one more write applied, and the change it made (none
   *  when the path is refused or the value is already there). */
  const apply = (from: StateModel, path: string, value: unknown): [StateModel, UiStateChange | null] => {
    const segments = parseStatePath(path);
    if (!segments || readPath(from, path) === value) return [from, null];
    const next = writePath(from, path, value);
    return next === from ? [from, null] : [next, { path: joinStatePath(segments), value }];
  };

  const commit = (next: StateModel, changes: UiStateChange[]) => {
    if (changes.length === 0) return;
    state = next;
    for (const listener of listeners) listener();
    onChange?.(changes);
  };

  return {
    get: (path) => readPath(state, path),
    set(path, value) {
      const [next, change] = apply(state, path, value);
      commit(next, change ? [change] : []);
    },
    update(updates) {
      let next = state;
      const changes: UiStateChange[] = [];
      for (const [path, value] of Object.entries(updates)) {
        const [written, change] = apply(next, path, value);
        next = written;
        if (change) changes.push(change);
      }
      commit(next, changes);
    },
    getSnapshot: () => state,
    getServerSnapshot: () => state,
    subscribe(listener) {
      listeners.add(listener);
      return () => {
        listeners.delete(listener);
      };
    },
  };
}
