/**
 * Patches on an activity's content (lib/agui/patch.ts): add, replace and
 * remove, never through a prototype key, never changing the input, and an
 * operation that does not apply is skipped. The same cases as the server's
 * tests/services/genui/test_patches.py.
 */

import { describe, expect, it } from 'vitest';
import { applyPatch, applyPatchOp, type PatchOp } from '../patch';

describe('applyPatchOp', () => {
  it('adds, replaces and removes without touching the input', () => {
    const content = { a: { b: [1, 2] } };
    expect(applyPatchOp(content, { op: 'add', path: '/a/c', value: 3 })).toEqual({ a: { b: [1, 2], c: 3 } });
    expect(applyPatchOp(content, { op: 'add', path: '/a/b/1', value: 9 })).toEqual({ a: { b: [1, 9, 2] } });
    expect(applyPatchOp(content, { op: 'add', path: '/a/b/-', value: 9 })).toEqual({ a: { b: [1, 2, 9] } });
    expect(applyPatchOp(content, { op: 'replace', path: '/a/b/0', value: 7 })).toEqual({ a: { b: [7, 2] } });
    expect(applyPatchOp(content, { op: 'remove', path: '/a/b/0' })).toEqual({ a: { b: [2] } });
    expect(applyPatchOp(content, { op: 'remove', path: '/a' })).toEqual({});
    expect(applyPatchOp(content, { op: 'replace', path: '', value: { fresh: 1 } })).toEqual({ fresh: 1 });
    expect(content).toEqual({ a: { b: [1, 2] } });
  });

  it.each<PatchOp>([
    { op: 'add', path: '/__proto__/polluted', value: true },
    { op: 'add', path: '/a/constructor', value: 1 },
    { op: 'replace', path: '/missing', value: 1 },
    { op: 'remove', path: '/missing' },
    { op: 'add', path: '/a/x/y', value: 1 },
    { op: 'add', path: '/a/b/5', value: 1 },
    { op: 'replace', path: '/a/b/-', value: 1 },
    { op: 'move', path: '/b' },
    { op: 'add', path: 'no-slash', value: 1 },
  ])('skips $op $path', (op) => {
    const content = { a: { b: [1, 2] } };
    expect(applyPatchOp(content, op)).toBe(content);
    expect(({} as Record<string, unknown>).polluted).toBeUndefined();
  });

  it('folds a stream in order', () => {
    const built = applyPatch({ root: '', state: {}, elements: {} }, [
      { op: 'add', path: '/root', value: 'r' },
      { op: 'add', path: '/state', value: { slot: 's2' } },
      { op: 'add', path: '/elements/r', value: { type: 'Stack', props: {} } },
      { op: 'add', path: '/elements/a~1b', value: { type: 'Text', props: { text: 'x' } } },
    ]);
    expect(built).toEqual({
      root: 'r',
      state: { slot: 's2' },
      elements: { r: { type: 'Stack', props: {} }, 'a/b': { type: 'Text', props: { text: 'x' } } },
    });
  });
});
