/**
 * What reaches json-render: prototype keys and paths refused everywhere,
 * the features a model must not reach (watch, repeat, slots, $computed,
 * confirm, onSuccess / onError) stripped, patches limited to the places a
 * spec stream writes, and sizes capped.
 */

import { readFileSync } from 'node:fs';
import { join } from 'node:path';
import { describe, expect, it } from 'vitest';
import {
  DEFAULT_SANITIZE_LIMITS,
  MAX_PATCH_PATH,
  sanitizeActionBinding,
  sanitizeCondition,
  sanitizeElement,
  sanitizeExpressions,
  sanitizePatch,
  sanitizeSpec,
  sanitizeState,
} from '../sanitize';

const FIXTURES = join(__dirname, '..', '..', '..', 'features', 'chat', '__fixtures__');

function fixture(name: string): unknown {
  return JSON.parse(readFileSync(join(FIXTURES, `${name}.spec.json`), 'utf-8'));
}

describe('prototype keys', () => {
  it('drops them as object keys, element ids and state keys', () => {
    const raw = JSON.parse(
      '{"root":"r","state":{"__proto__":{"polluted":true},"ok":1,"constructor":{"prototype":{"x":1}}},' +
        '"elements":{"__proto__":{"type":"Text","props":{"text":"evil"}},"constructor":{"type":"Text","props":{}},' +
        '"r":{"type":"Stack","props":{"__proto__":{"polluted":true},"gap":"md","nested":{"prototype":1,"keep":2}},"children":["__proto__","a"]},' +
        '"a":{"type":"Text","props":{"text":"hi"}}}}',
    );
    const spec = sanitizeSpec(raw)!;
    expect(Object.keys(spec.elements)).toEqual(['r', 'a']);
    expect(Object.getPrototypeOf(spec.elements)).toBeNull();
    expect(spec.elements.r).toEqual({ type: 'Stack', props: { gap: 'md', nested: { keep: 2 } }, children: ['a'] });
    expect(spec.state).toEqual({ ok: 1 });
    expect(({} as Record<string, unknown>).polluted).toBeUndefined();
  });

  it('refuses a path through one in $state, $bindState, $template, visible and action params', () => {
    expect(sanitizeExpressions({ a: { $state: '/__proto__/x' }, b: { $bindState: '/x/constructor' }, c: { $state: '/ok' } })).toEqual({
      c: { $state: '/ok' },
    });
    expect(sanitizeExpressions({ $template: 'A ${/prototype/x} B ${ /name } C ${field}' })).toEqual({
      $template: 'A  B ${/name} C ${/field}',
    });
    expect(sanitizeCondition({ $state: '/a/__proto__', eq: 1 })).toBe(false);
    expect(sanitizeCondition({ $state: '/ok', eq: { $state: '/constructor' } })).toEqual({ $state: '/ok', eq: null });
    expect(sanitizeActionBinding({ action: 'go', params: { to: { $state: '/__proto__' }, n: 1 } })).toEqual({
      action: 'go',
      params: { n: 1 },
    });
    // Escaped segments are read unescaped, as json-render reads them.
    expect(sanitizeExpressions({ $state: '/a~1b/c' })).toEqual({ $state: '/a~1b/c' });
    expect(sanitizeExpressions({ $state: '/prototype~0' })).toEqual({ $state: '/prototype~0' });
  });

  it('refuses them as action and event names', () => {
    expect(sanitizeActionBinding({ action: 'constructor' })).toBeNull();
    // JSON.parse makes "__proto__" an own key, as a model's reply would.
    const on = JSON.parse('{"__proto__":{"action":"go"},"press":{"action":"go"}}');
    expect(sanitizeElement({ type: 'Button', props: {}, on })).toEqual({
      type: 'Button',
      props: {},
      on: { press: { action: 'go' } },
    });
  });
});

describe('features a model must not reach', () => {
  it('strips watch, repeat and slots from an element, and keeps type, props, children, visible and on', () => {
    const element = sanitizeElement({
      type: 'Card',
      props: { title: 'T' },
      children: ['a'],
      visible: { $state: '/show' },
      on: { press: { action: 'go' } },
      watch: { '/x': { action: 'go' } },
      repeat: { statePath: '/items' },
      slots: { footer: ['b'] },
      key: 'k',
    });
    expect(element).toEqual({
      type: 'Card',
      props: { title: 'T' },
      children: ['a'],
      visible: { $state: '/show' },
      on: { press: { action: 'go' } },
    });
  });

  it('drops $computed, and $item, $index and $bindItem, which only work inside repeat', () => {
    expect(
      sanitizeExpressions({
        a: { $computed: 'secret', args: { x: 1 } },
        b: { $item: 'name' },
        c: { $index: true },
        d: { $bindItem: 'done' },
        e: [{ $computed: 'f' }, 'kept'],
        f: { $cond: { $state: '/x' }, $then: { $computed: 'f' }, $else: 'no' },
      }),
    ).toEqual({ e: ['kept'], f: { $cond: { $state: '/x' }, $then: null, $else: 'no' } });
  });

  it('keeps an action binding to its action and params', () => {
    expect(
      sanitizeActionBinding({
        action: 'save',
        params: { id: 1 },
        confirm: { title: 'Sure?', message: 'Really?' },
        onSuccess: { set: { '/__proto__/x': 1 } },
        onError: { action: 'other' },
        preventDefault: true,
      }),
    ).toEqual({ action: 'save', params: { id: 1 } });
    expect(sanitizeActionBinding({ action: '' })).toBeNull();
    expect(sanitizeActionBinding({ action: 'has space' })).toBeNull();
  });
});

describe('conditions', () => {
  it('canonicalizes what json-render reads differently', () => {
    expect(sanitizeCondition(undefined)).toBeUndefined();
    expect(sanitizeCondition(null)).toBeUndefined();
    expect(sanitizeCondition(true)).toBe(true);
    expect(sanitizeCondition([{ $state: '/a' }, { $state: 'b', not: 1 }])).toEqual({
      $and: [{ $state: '/a' }, { $state: '/b', not: true }],
    });
    expect(sanitizeCondition({ $or: [false, { $state: '/c', gt: 2, lt: { $state: '/d' } }] })).toEqual({
      $or: [false, { $state: '/c', gt: 2, lt: { $state: '/d' } }],
    });
    // Only meaningful inside repeat, or no shape at all: hidden.
    expect(sanitizeCondition({ $item: 'done' })).toBe(false);
    expect(sanitizeCondition({ eq: 1 })).toBe(false);
    expect(sanitizeCondition({ $and: 'nope' })).toBe(false);
  });
});

describe('sanitizeSpec', () => {
  it('needs a usable root and an elements object', () => {
    expect(sanitizeSpec(null)).toBeNull();
    expect(sanitizeSpec({ root: 'a', elements: [] })).toBeNull();
    expect(sanitizeSpec({ root: '__proto__', elements: {} })).toBeNull();
    expect(sanitizeSpec({ root: 'r', elements: {} })).toEqual({ root: 'r', state: {}, elements: {} });
  });

  it('caps elements, children, strings, arrays and depth', () => {
    const elements: Record<string, Record<string, unknown>> = Object.fromEntries(
      Array.from({ length: 80 }, (_, i) => [`e${i}`, { type: 'Text', props: { text: 'x'.repeat(5000) }, children: [] }]),
    );
    elements.e0.children = Array.from({ length: 100 }, (_, i) => `c${i}`);
    let deep: unknown = 'leaf';
    for (let i = 0; i < 20; i++) deep = { next: deep };
    elements.e1.props = { list: Array.from({ length: 80 }, (_, i) => i), deep };
    const spec = sanitizeSpec({ root: 'e0', elements })!;
    expect(Object.keys(spec.elements)).toHaveLength(DEFAULT_SANITIZE_LIMITS.maxElements);
    expect(spec.elements.e0.children).toHaveLength(DEFAULT_SANITIZE_LIMITS.maxChildren);
    expect(String(spec.elements.e2.props.text)).toHaveLength(DEFAULT_SANITIZE_LIMITS.maxString);
    expect(spec.elements.e1.props.list).toHaveLength(DEFAULT_SANITIZE_LIMITS.maxArray);
    expect(JSON.stringify(spec.elements.e1.props.deep).match(/next/g)!.length).toBeLessThan(20);
  });

  it('keeps the chat fixtures as they are, adding only empty props', () => {
    for (const name of ['saturday-booking', 'reply-insights', 'reminders']) {
      const raw = fixture(name) as { elements: Record<string, Record<string, unknown>> };
      const spec = sanitizeSpec(raw)!;
      expect(Object.keys(spec.elements)).toEqual(Object.keys(raw.elements));
      for (const [id, element] of Object.entries(raw.elements)) {
        expect(spec.elements[id]).toEqual({ props: {}, ...element });
      }
    }
  });
});

describe('sanitizePatch', () => {
  const element = { type: 'Text', props: { text: 'hi' } };

  it('takes add, replace and remove at the places a spec stream writes', () => {
    expect(sanitizePatch({ op: 'add', path: '/root', value: 'root' })).toEqual({ op: 'add', path: '/root', value: 'root' });
    expect(sanitizePatch({ op: 'add', path: '/state', value: JSON.parse('{"a":1,"__proto__":{"polluted":true}}') })).toEqual({
      op: 'add',
      path: '/state',
      value: { a: 1 },
    });
    expect(sanitizePatch({ op: 'replace', path: '/state/form/name', value: 'Ana' })).toEqual({
      op: 'replace',
      path: '/state/form/name',
      value: 'Ana',
    });
    expect(sanitizePatch({ op: 'add', path: '/elements/a', value: { ...element, watch: {} } })).toEqual({
      op: 'add',
      path: '/elements/a',
      value: element,
    });
    expect(sanitizePatch({ op: 'replace', path: '/elements/a/props/text', value: { $state: '/__proto__' } })).toBeNull();
    expect(sanitizePatch({ op: 'replace', path: '/elements/a/props/text', value: { $state: '/t' } })).toEqual({
      op: 'replace',
      path: '/elements/a/props/text',
      value: { $state: '/t' },
    });
    expect(sanitizePatch({ op: 'add', path: '/elements/a/children/-', value: 'b' })).toEqual({
      op: 'add',
      path: '/elements/a/children/-',
      value: 'b',
    });
    expect(sanitizePatch({ op: 'replace', path: '/elements/a/visible', value: [{ $state: '/x' }] })).toEqual({
      op: 'replace',
      path: '/elements/a/visible',
      value: { $and: [{ $state: '/x' }] },
    });
    expect(sanitizePatch({ op: 'add', path: '/elements/a/on/press', value: { action: 'go', confirm: {} } })).toEqual({
      op: 'add',
      path: '/elements/a/on/press',
      value: { action: 'go' },
    });
    expect(sanitizePatch({ op: 'remove', path: '/elements/a' })).toEqual({ op: 'remove', path: '/elements/a' });
  });

  it('refuses every other op, place and prototype path', () => {
    const refused = [
      { op: 'move', from: '/elements/a', path: '/elements/b' },
      { op: 'copy', from: '/elements/a', path: '/elements/b' },
      { op: 'test', path: '/root', value: 'r' },
      { op: 'add', path: '/foo', value: 1 },
      { op: 'add', path: 'root', value: 'r' },
      { op: 'remove', path: '/root' },
      { op: 'add', path: '/root', value: 'bad id' },
      { op: 'add', path: '/state/__proto__/polluted', value: true },
      { op: 'add', path: '/state/a/constructor', value: true },
      { op: 'add', path: '/elements/__proto__', value: element },
      { op: 'add', path: '/elements/a/props/constructor/prototype', value: 1 },
      { op: 'add', path: '/elements/a/watch', value: {} },
      { op: 'add', path: '/elements/a/repeat', value: { statePath: '/x' } },
      { op: 'add', path: '/elements/a/slots/footer', value: ['b'] },
      { op: 'add', path: '/elements/a/type', value: 'Text' },
      { op: 'add', path: '/elements/a/visible/0', value: true },
      { op: 'add', path: '/elements/a/children/x', value: 'b' },
      { op: 'add', path: '/elements/a/children/0/y', value: 'b' },
      { op: 'add', path: '/elements/a/on/constructor', value: { action: 'go' } },
      { op: 'add', path: '/elements/a', value: { type: 'not a name' } },
      { op: 'add', path: '/elements//props', value: {} },
      { op: 'add', path: `/state/${'a/'.repeat(10)}b`, value: 1 },
      { op: 'add', path: `/elements/${'a'.repeat(MAX_PATCH_PATH)}`, value: element },
    ];
    for (const patch of refused) expect(sanitizePatch(patch), JSON.stringify(patch).slice(0, 80)).toBeNull();
    expect(({} as Record<string, unknown>).polluted).toBeUndefined();
  });
});

describe('sanitizeState', () => {
  it('is plain JSON, always an object', () => {
    expect(sanitizeState([1, 2])).toEqual({});
    expect(sanitizeState({ n: Number.NaN, f: () => 1, s: 'x', ok: [true, null] })).toEqual({ s: 'x', ok: [true, null] });
  });
});
