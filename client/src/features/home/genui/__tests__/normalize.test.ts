/**
 * The normalizer's output is json-render's shape whichever shape the model
 * wrote: a Button's action becomes `on.press` (from `on.press`, from `on`
 * written inside its props, or from the older `action` / `actionParams`
 * props), a Toggle binds `checked` (older replies say `value`), and every
 * expression whose path reaches a prototype is dropped.
 *
 * When they work, as the normalizer sets it up: one Schedule after the
 * routine, bound to /trigger, which starts as the hire button's trigger
 * (else the app the routine's first "When" step names, else the owner
 * messaging them), snapped to a trigger the server builds as it reads,
 * and kept however long the screen is.
 */

import { describe, expect, it } from 'vitest';
import { STATE_PATHS } from '../catalog';
import { bindingPath, getPath } from '../expressions';
import { normalizeSpec, type NormalizedSpec } from '../normalize';

type Raw = Record<string, unknown>;

function screen(hireTrigger: unknown, extra: Record<string, Raw> = {}, planSteps: Raw[] = [{ title: 'Answer', role: 'agent' }]) {
  return normalizeSpec({
    root: 'r',
    elements: {
      r: { type: 'Stack', props: { direction: 'vertical' }, children: ['a', 'p', ...Object.keys(extra), 'row'] },
      a: { type: 'AgentCard', props: { name: 'Maya', role: 'Receptionist', apps: [] } },
      p: { type: 'Plan', props: { title: 'Their routine', steps: planSteps } },
      ...extra,
      row: { type: 'Stack', props: { direction: 'horizontal' }, children: ['h', 'c'] },
      h: { type: 'Button', props: { label: 'Hire Maya', variant: 'primary', action: 'hire_employee', actionParams: { trigger: hireTrigger } } },
      c: { type: 'Button', props: { label: 'Change something', action: 'refine' } },
    },
  })!;
}

/** A screen with just these elements under the root (plus whatever the normalizer adds). */
function only(elements: Record<string, Raw>, state: Raw = {}) {
  return normalizeSpec({
    root: 'r',
    state,
    elements: { r: { type: 'Stack', props: { direction: 'vertical' }, children: Object.keys(elements) }, ...elements },
  })!;
}

function schedules(spec: NormalizedSpec): string[] {
  return spec.order.filter((id) => spec.elements[id].type === 'Schedule');
}

describe('json-render shape', () => {
  const hireParams = { name: 'Maya', trigger: { kind: 'manual' } };

  it('takes a Button’s action from on.press, from on inside its props, or from its older props', () => {
    const shapes = [
      { type: 'Button', props: { label: 'Hire Maya' }, on: { press: { action: 'hire_employee', params: hireParams } } },
      { type: 'Button', props: { label: 'Hire Maya', on: { press: { action: 'hire_employee', params: hireParams } } } },
      { type: 'Button', props: { label: 'Hire Maya', action: 'hire_employee', actionParams: hireParams } },
      { type: 'Button', label: 'Hire Maya', action: 'hire_employee', actionParams: hireParams },
    ];
    for (const button of shapes) {
      const spec = only({ h: button });
      expect(spec.elements.h.on).toEqual({ press: { action: 'hire_employee', params: hireParams } });
      expect(spec.elements.h.props).toEqual({ label: 'Hire Maya' });
    }
  });

  it('prefers on.press over older props, and takes the first known action of a list', () => {
    const spec = only({
      h: {
        type: 'Button',
        props: { label: 'Hire', action: 'refine' },
        on: { press: [{ action: 'wave' }, { action: 'hire_employee', params: { name: 'Ivy' } }] },
      },
    });
    expect(spec.elements.h.on?.press).toEqual({ action: 'hire_employee', params: { name: 'Ivy' } });
  });

  it('keeps an action binding to its action and params', () => {
    const spec = only({
      h: {
        type: 'Button',
        props: { label: 'Hire' },
        on: {
          press: { action: 'hire_employee', params: { name: 'Lia' }, confirm: { title: 'Sure?', message: 'Hire?' }, onSuccess: { set: { '/x': 1 } } },
          hover: { action: 'refine' },
        },
      },
    });
    expect(spec.elements.h.on).toEqual({ press: { action: 'hire_employee', params: { name: 'Lia' } } });
  });

  it('turns ask into a plain change request and drops a Button whose action is unknown', () => {
    const spec = only({
      q: { type: 'Button', props: { label: 'Only weekdays' }, on: { press: { action: 'ask', params: { text: 'Only weekdays' } } } },
      x: { type: 'Button', props: { label: 'Push' }, on: { press: { action: 'pushState', params: { statePath: '/a', value: 1 } } } },
    });
    expect(spec.elements.q.on).toEqual({ press: { action: 'refine' } });
    expect(spec.elements.x).toBeUndefined();
  });

  it('binds a Toggle’s checked, reading value from older replies', () => {
    const spec = only({
      rules: { type: 'Card', props: { title: 'Ground rules' }, children: ['t1', 't2'] },
      t1: { type: 'Toggle', props: { label: 'Only 9 to 6', value: { $bindState: '/rules/hours' } } },
      t2: { type: 'Toggle', props: { label: 'Weekends', checked: { $bindState: '/rules/weekends' }, value: true } },
    });
    expect(spec.elements.t1.props).toEqual({ label: 'Only 9 to 6', checked: { $bindState: '/rules/hours' } });
    expect(spec.elements.t2.props).toEqual({ label: 'Weekends', checked: { $bindState: '/rules/weekends' } });
    // The ask-first toggle it adds binds checked too.
    const askFirst = spec.order.find((id) => bindingPath(spec.elements[id].props.checked) === STATE_PATHS.askFirst);
    expect(askFirst).toBeDefined();
  });

  it('drops expressions whose path reaches a prototype, wherever they are', () => {
    const spec = only({
      t: {
        type: 'Text',
        props: { text: { $template: 'Hi ${/__proto__/x} and ${ /name }' } },
        visible: { $state: '/constructor/x' },
      },
      m: { type: 'Metric', props: { label: { $state: '/prototype' }, value: { $computed: 'f' } }, visible: [{ $state: '/ok' }] },
      i: { type: 'Input', props: { label: 'Name', value: { $bindState: '/inputs/__proto__' } } },
      h: {
        type: 'Button',
        props: { label: 'Hire' },
        on: { press: { action: 'hire_employee', params: { name: { $state: '/__proto__/name' }, role: { $state: '/role' } } } },
      },
    });
    expect(spec.elements.t.props).toEqual({ text: { $template: 'Hi  and ${/name}' } });
    expect(spec.elements.t.visible).toBe(false);
    expect(spec.elements.m.props).toEqual({});
    expect(spec.elements.m.visible).toEqual({ $and: [{ $state: '/ok' }] });
    expect(spec.elements.i.props).toEqual({ label: 'Name' });
    expect(spec.elements.h.on?.press.params).toEqual({ role: { $state: '/role' } });
    expect(JSON.stringify(spec)).not.toMatch(/__proto__|constructor|prototype|\$computed/);
  });
});

describe('when they work', () => {
  it('is one Schedule right after the routine, bound to /trigger', () => {
    const spec = screen({ kind: 'app_event', app: 'WhatsApp' });
    const [schedule] = schedules(spec);
    expect(schedules(spec)).toHaveLength(1);
    expect(bindingPath(spec.elements[schedule].props.value)).toBe(STATE_PATHS.trigger);
    expect(spec.elements.r.children.indexOf(schedule)).toBe(spec.elements.r.children.indexOf('p') + 1);
    expect(getPath(spec.state, STATE_PATHS.trigger)).toEqual({ kind: 'app_event', app: 'WhatsApp' });
  });

  it('starts as the hire button’s trigger, snapped to what can run', () => {
    const spec = screen({ kind: 'schedule', every: 'weekday', at: '09:30', app: 'Gmail' });
    expect(getPath(spec.state, STATE_PATHS.trigger)).toEqual({ kind: 'schedule', every: 'weekday', at: '09:00' });
  });

  it('reads the hire button’s trigger from on.press as well', () => {
    const spec = only({
      a: { type: 'AgentCard', props: { name: 'Ivy', role: 'Diary keeper' } },
      h: {
        type: 'Button',
        props: { label: 'Hire Ivy', variant: 'primary' },
        on: { press: { action: 'hire_employee', params: { trigger: { kind: 'schedule', every: 'week', day: 'tue', at: '08:00' } } } },
      },
    });
    expect(getPath(spec.state, STATE_PATHS.trigger)).toEqual({ kind: 'schedule', every: 'week', day: 'tuesday', at: '08:00' });
  });

  it('takes the routine’s app when the button names none, else the owner messaging them', () => {
    const steps = [{ title: 'When a message arrives', role: 'trigger', app: 'Telegram' }, { title: 'Answer', role: 'agent' }];
    expect(getPath(screen(undefined, {}, steps).state, STATE_PATHS.trigger)).toEqual({ kind: 'app_event', app: 'Telegram' });
    expect(getPath(screen({ kind: 'app_event' }, {}, steps).state, STATE_PATHS.trigger)).toEqual({
      kind: 'app_event',
      app: 'Telegram',
    });
    expect(getPath(screen(undefined).state, STATE_PATHS.trigger)).toEqual({ kind: 'manual' });
    expect(getPath(screen({ kind: 'manual' }, {}, steps).state, STATE_PATHS.trigger)).toEqual({ kind: 'manual' });
  });

  it('keeps one Schedule the model wrote, rebound, and drops the rest', () => {
    const spec = screen(
      { kind: 'manual' },
      { s1: { type: 'Schedule', props: { value: 'whenever' } }, s2: { type: 'Schedule', props: {} } },
    );
    expect(schedules(spec)).toEqual(['s1']);
    expect(bindingPath(spec.elements.s1.props.value)).toBe(STATE_PATHS.trigger);
  });

  it('survives the size cap', () => {
    const texts = Object.fromEntries(
      Array.from({ length: 30 }, (_, i) => [`t${i}`, { type: 'Text', props: { text: `Line ${i}` } }]),
    );
    const spec = screen({ kind: 'manual' }, texts);
    expect(spec.order.length).toBeLessThanOrEqual(16);
    expect(schedules(spec)).toHaveLength(1);
  });
});
