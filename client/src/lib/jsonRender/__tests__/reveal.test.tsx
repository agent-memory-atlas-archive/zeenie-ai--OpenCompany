/**
 * The patch stream for a spec (root, state, then each element depth first,
 * ids escaped as RFC 6901 says), held line for line against the chat
 * fixtures, and its paced replay: one element every 90 ms, everything at
 * once under reduced motion, from nothing again for a new version.
 */

import { readFileSync } from 'node:fs';
import { join } from 'node:path';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { act, renderHook } from '@testing-library/react';
import type { Spec } from '@json-render/core';
import { setReducedMotion } from '@/test/waapi';
import { REVEAL_STEP_MS, specFromPatches, specToPatches, useSpecReveal } from '../reveal';

const FIXTURES = join(__dirname, '..', '..', '..', 'features', 'chat', '__fixtures__');

function read(name: string): string {
  return readFileSync(join(FIXTURES, name), 'utf-8');
}

const SPEC: Spec = {
  root: 'r',
  state: { on: true },
  elements: {
    r: { type: 'Stack', props: {}, children: ['a', 'b/c', 'r'] },
    a: { type: 'Card', props: {}, children: ['a1', 'missing', 'a2'] },
    a1: { type: 'Text', props: { text: '1' } },
    a2: { type: 'Text', props: { text: '2' } },
    'b/c': { type: 'Text', props: { text: 'slash' }, children: ['a1'] },
    'x~y': { type: 'Text', props: { text: 'unreachable' } },
  },
};

describe('specToPatches', () => {
  it.each(['saturday-booking', 'reply-insights', 'reminders'])('matches %s.patches.jsonl line by line', (name) => {
    const spec = JSON.parse(read(`${name}.spec.json`)) as Spec;
    const lines = read(`${name}.patches.jsonl`)
      .split(/\r?\n/)
      .filter((line) => line.trim() !== '');
    const patches = specToPatches(spec).map((patch) => JSON.stringify(patch));
    expect(patches).toEqual(lines);
  });

  it('walks depth first from the root, each element once, escaping ids', () => {
    expect(specToPatches(SPEC).map((patch) => patch.path)).toEqual([
      '/root',
      '/state',
      '/elements/r',
      '/elements/a',
      '/elements/a1',
      '/elements/a2',
      '/elements/b~1c',
    ]);
    expect(specToPatches({ root: 'r', elements: {} })).toEqual([
      { op: 'add', path: '/root', value: 'r' },
      { op: 'add', path: '/state', value: {} },
    ]);
  });

  it('builds the spec back, a prefix at a time', () => {
    const patches = specToPatches(SPEC);
    expect(specFromPatches(patches)).toEqual({
      root: 'r',
      state: { on: true },
      elements: { r: SPEC.elements.r, a: SPEC.elements.a, a1: SPEC.elements.a1, a2: SPEC.elements.a2, 'b/c': SPEC.elements['b/c'] },
    });
    expect(Object.keys(specFromPatches(patches, 4).elements)).toEqual(['r', 'a']);
  });
});

describe('useSpecReveal', () => {
  let restoreMotion: () => void;

  beforeEach(() => {
    restoreMotion = setReducedMotion(false);
    vi.useFakeTimers({ toFake: ['setInterval', 'clearInterval', 'performance'] });
  });

  afterEach(() => {
    vi.useRealTimers();
    restoreMotion();
  });

  const shownIds = (result: { current: ReturnType<typeof useSpecReveal> }) => Object.keys(result.current.spec?.elements ?? {});

  it('shows one element every REVEAL_STEP_MS, depth first', () => {
    const { result } = renderHook(() => useSpecReveal(SPEC, 1));
    expect(result.current.spec?.root).toBe('r');
    expect(shownIds(result)).toEqual(['r']);
    expect(result.current.revealing).toBe(true);
    act(() => {
      vi.advanceTimersByTime(REVEAL_STEP_MS);
    });
    expect(shownIds(result)).toEqual(['r', 'a']);
    act(() => {
      vi.advanceTimersByTime(REVEAL_STEP_MS * 2);
    });
    expect(shownIds(result)).toEqual(['r', 'a', 'a1', 'a2']);
    act(() => {
      vi.advanceTimersByTime(REVEAL_STEP_MS * 10);
    });
    expect(shownIds(result)).toEqual(['r', 'a', 'a1', 'a2', 'b/c']);
    expect(result.current.revealing).toBe(false);
  });

  it('starts again from nothing for a new version', () => {
    const { result, rerender } = renderHook(({ version }) => useSpecReveal(SPEC, version), { initialProps: { version: 1 } });
    act(() => {
      vi.advanceTimersByTime(REVEAL_STEP_MS * 10);
    });
    expect(shownIds(result)).toHaveLength(5);
    rerender({ version: 2 });
    expect(shownIds(result)).toEqual(['r']);
    expect(result.current.revealing).toBe(true);
  });

  it('shows everything at once under reduced motion', () => {
    restoreMotion();
    restoreMotion = setReducedMotion(true);
    const { result } = renderHook(() => useSpecReveal(SPEC, 1));
    expect(shownIds(result)).toEqual(['r', 'a', 'a1', 'a2', 'b/c']);
    expect(result.current.revealing).toBe(false);
  });

  it('has nothing to show without a spec', () => {
    const { result } = renderHook(() => useSpecReveal(null, 1));
    expect(result.current).toEqual({ spec: null, revealing: false });
  });
});
