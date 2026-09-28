/**
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

function schedules(spec: NormalizedSpec): string[] {
  return spec.order.filter((id) => spec.elements[id].type === 'Schedule');
}

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
