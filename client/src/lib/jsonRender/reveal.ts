/**
 * A generated UI arrives as JSON patches (docs-internal/chat_protocol.md,
 * "Generated UI"): `add /root`, `add /state`, then one `add
 * /elements/<id>` per element, depth first from the root, ids escaped as
 * RFC 6901 says. `specToPatches` writes that stream for a whole spec; the
 * chat fixtures (features/chat/__fixtures__) hold it line for line.
 *
 * `useSpecReveal` replays the stream on screen for a spec that arrived
 * whole: the root and state at once, then one element every REVEAL_STEP_MS
 * (design handoff), so a screen reads as being written rather than dropped
 * in. Everything shows at once under reduced motion or while the page is
 * hidden (motionSuppressed). A new version starts again from nothing.
 */

import { useEffect, useMemo, useState } from 'react';
import type { Spec, UIElement } from '@json-render/core';
import { motionSuppressed } from '@/lib/motion';
import { escapePointer, unescapePointer } from './paths';
import type { SpecPatch } from './sanitize';

export const REVEAL_STEP_MS = 90;

/** `add /root` and `add /state`, ahead of the elements. */
const HEAD = 2;
const ELEMENTS_PREFIX = '/elements/';

function ownElement(spec: Spec, id: string): UIElement | undefined {
  return Object.prototype.hasOwnProperty.call(spec.elements, id) ? spec.elements[id] : undefined;
}

/** The patch stream that builds `spec`: root, state, then every element
 *  reachable from the root, depth first, each once. */
export function specToPatches(spec: Spec): SpecPatch[] {
  const patches: SpecPatch[] = [
    { op: 'add', path: '/root', value: spec.root },
    { op: 'add', path: '/state', value: spec.state ?? {} },
  ];
  const seen = new Set<string>();
  const walk = (id: string) => {
    const element = ownElement(spec, id);
    if (!element || seen.has(id)) return;
    seen.add(id);
    patches.push({ op: 'add', path: `${ELEMENTS_PREFIX}${escapePointer(id)}`, value: element });
    element.children?.forEach(walk);
  };
  walk(spec.root);
  return patches;
}

/** The spec the first `count` patches of such a stream build. Only the
 *  three kinds specToPatches writes are applied. */
export function specFromPatches(patches: readonly SpecPatch[], count: number = patches.length): Spec {
  let root = '';
  let state: Record<string, unknown> = {};
  // No prototype: ids are looked up here as keys.
  const elements: Record<string, UIElement> = Object.create(null);
  for (const patch of patches.slice(0, count)) {
    if (patch.op !== 'add') continue;
    if (patch.path === '/root' && typeof patch.value === 'string') root = patch.value;
    else if (patch.path === '/state' && patch.value && typeof patch.value === 'object') state = patch.value as Record<string, unknown>;
    else if (patch.path.startsWith(ELEMENTS_PREFIX)) {
      const token = patch.path.slice(ELEMENTS_PREFIX.length);
      if (token && !token.includes('/') && patch.value && typeof patch.value === 'object') {
        elements[unescapePointer(token)] = patch.value as UIElement;
      }
    }
  }
  return { root, state, elements };
}

export interface SpecReveal {
  /** The part of the spec shown so far (null while there is no spec). */
  spec: Spec | null;
  /** Elements are still arriving: render with `loading` so json-render does
   *  not warn about children that are on their way. */
  revealing: boolean;
}

export function useSpecReveal(spec: Spec | null, version: number): SpecReveal {
  const patches = useMemo(() => (spec ? specToPatches(spec) : []), [spec]);
  const total = Math.max(0, patches.length - HEAD);
  const [progress, setProgress] = useState({ version, count: 0 });
  const count = progress.version === version ? progress.count : 0;

  useEffect(() => {
    if (total === 0) return;
    if (motionSuppressed()) {
      setProgress({ version, count: total });
      return;
    }
    const started = performance.now();
    setProgress({ version, count: 1 });
    const timer = window.setInterval(() => {
      const next = motionSuppressed()
        ? total
        : Math.min(total, Math.floor((performance.now() - started) / REVEAL_STEP_MS) + 1);
      setProgress({ version, count: next });
      if (next >= total) window.clearInterval(timer);
    }, REVEAL_STEP_MS);
    return () => window.clearInterval(timer);
  }, [version, total]);

  const shown = useMemo(() => (spec ? specFromPatches(patches, HEAD + count) : null), [spec, patches, count]);
  return { spec: shown, revealing: count < total };
}
