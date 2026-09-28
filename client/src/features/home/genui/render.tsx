/**
 * Draws a normalized setup screen. Every component here is private to the
 * genui folder (lint keeps it that way); pages use HireDraftPanel.
 *
 * An element renders once the reveal reaches it and while its `visible`
 * condition holds. Its props are resolved against the screen's state, then
 * read through the catalogue's schema, so an odd value degrades one prop.
 * Each element sits in its own error boundary: one that throws disappears
 * and the rest of the screen stays.
 *
 * When they work (Schedule) reads as one plain sentence; Edit offers only
 * what the hire can build: the owner messaging them, a schedule at the
 * times it can run, or a new message in one of the apps the server says can
 * start the work. Once the owner changes it, the routine's "When" step
 * follows.
 */

import {
  Component,
  createContext,
  Fragment,
  useCallback,
  useContext,
  useLayoutEffect,
  useRef,
  useState,
  type ReactNode,
} from 'react';
import { ActionButton } from '@/components/ui/action-button';
import { Button } from '@/components/ui/button';
import { Input } from '@/components/ui/input';
import { Select, SelectContent, SelectItem, SelectTrigger, SelectValue } from '@/components/ui/select';
import { Switch } from '@/components/ui/switch';
import { ToggleGroup, ToggleGroupItem } from '@/components/ui/toggle-group';
import { animate } from '@/lib/motion';
import { cn } from '@/lib/utils';
import { MicroLabel } from '../ui/primitives';
import { PROP_SCHEMAS, STATE_PATHS, type ComponentType, type PropsOf, type StepRole, type Tone } from './catalog';
import { bindingPath, evalCondition, getPath, resolveValue, type UiState } from './expressions';
import {
  LAST_MONTH_DAY,
  SCHEDULE_EVERY,
  SCHEDULE_TIMES,
  WEEKDAYS,
  routineSteps,
  snapTrigger,
  triggerSentence,
  type HireTrigger,
} from './hirePayload';
import type { NormalizedSpec, SpecElement } from './normalize';

interface SpecContextValue {
  spec: NormalizedSpec;
  state: UiState;
  revealed: ReadonlySet<string>;
  onAction: (action: string, rawParams: unknown) => void;
  onValue: (path: string, value: unknown) => void;
  /** Apps that can start the work: any name the reply used -> the app's own name. */
  triggerApps: Readonly<Record<string, string>>;
}

const SpecContext = createContext<SpecContextValue | null>(null);

function useSpec(): SpecContextValue {
  const value = useContext(SpecContext);
  if (!value) throw new Error('Setup-screen components render inside SpecView');
  return value;
}

// ----- token maps (Tailwind scans these literals) -----

const TONE_CARD: Record<Tone, string> = {
  agent: 'border-node-agent-border bg-linear-135/srgb from-node-agent-soft to-bg-elevated to-60%',
  model: 'border-node-model-border bg-linear-135/srgb from-node-model-soft to-bg-elevated to-60%',
  tool: 'border-node-tool-border bg-linear-135/srgb from-node-tool-soft to-bg-elevated to-60%',
  trigger: 'border-node-trigger-border bg-linear-135/srgb from-node-trigger-soft to-bg-elevated to-60%',
  workflow: 'border-node-workflow-border bg-linear-135/srgb from-node-workflow-soft to-bg-elevated to-60%',
  neutral: 'border-border-default bg-bg-elevated',
};

const TONE_INK: Record<Tone, string> = {
  agent: 'text-node-agent-ink',
  model: 'text-node-model-ink',
  tool: 'text-node-tool-ink',
  trigger: 'text-node-trigger-ink',
  workflow: 'text-node-workflow-ink',
  neutral: 'text-fg-default',
};

const TONE_DOT: Record<Tone, string> = {
  agent: 'bg-node-agent',
  model: 'bg-node-model',
  tool: 'bg-node-tool',
  trigger: 'bg-node-trigger',
  workflow: 'bg-node-workflow',
  neutral: 'bg-fg-faint',
};

const TONE_BADGE: Record<Tone, string> = {
  agent: 'border-node-agent-edge bg-node-agent-fill text-node-agent-ink',
  model: 'border-node-model-edge bg-node-model-fill text-node-model-ink',
  tool: 'border-node-tool-edge bg-node-tool-fill text-node-tool-ink',
  trigger: 'border-node-trigger-edge bg-node-trigger-fill text-node-trigger-ink',
  workflow: 'border-node-workflow-edge bg-node-workflow-fill text-node-workflow-ink',
  neutral: 'border-border-default bg-bg-hover text-fg-default',
};

const STEP_TONE: Record<StepRole, string> = {
  trigger:
    'border-node-trigger-edge bg-linear-135/srgb from-node-trigger-fill to-bg-elevated shadow-[0_0_18px_var(--node-trigger-soft)]',
  agent: 'border-node-agent-edge bg-linear-135/srgb from-node-agent-fill to-bg-elevated shadow-[0_0_18px_var(--node-agent-soft)]',
  tool: 'border-node-tool-edge bg-linear-135/srgb from-node-tool-fill to-bg-elevated shadow-[0_0_18px_var(--node-tool-soft)]',
  workflow:
    'border-node-workflow-edge bg-linear-135/srgb from-node-workflow-fill to-bg-elevated shadow-[0_0_18px_var(--node-workflow-soft)]',
};

const STEP_LABEL: Record<StepRole, string> = { trigger: 'When', agent: 'They', tool: 'Using', workflow: 'Then' };

const EVERY_LABEL: Record<NonNullable<HireTrigger['every']>, string> = {
  hour: 'Every hour',
  day: 'Every day',
  weekday: 'Weekdays',
  week: 'Every week',
  month: 'Every month',
};

const MONTH_DAYS = Array.from({ length: LAST_MONTH_DAY }, (_, index) => String(index + 1));

const STACK_GAP = { sm: 'gap-2', md: 'gap-3', lg: 'gap-4.5' } as const;

const AGENT_STATUS = {
  ready: {
    label: 'Ready',
    dot: 'bg-status-working-dot shadow-[0_0_8px_var(--status-working-dot)]',
    ink: 'text-status-working-ink',
  },
  working: {
    label: 'Working',
    dot: 'bg-status-ready-dot shadow-[0_0_8px_var(--status-ready-dot)]',
    ink: 'text-status-ready-ink',
  },
  paused: { label: 'Paused', dot: 'bg-status-paused-dot', ink: 'text-status-paused-ink' },
} as const;

/** A soft entrance the first time an element appears. */
function useEnter<T extends HTMLElement>() {
  const done = useRef(false);
  return useCallback((node: T | null) => {
    if (!node || done.current) return;
    done.current = true;
    animate(
      node,
      [
        { opacity: 0, transform: 'translateY(8px) scale(.985)', filter: 'blur(3px)' },
        { opacity: 1, transform: 'none', filter: 'blur(0)' },
      ],
      { duration: 440 },
    );
  }, []);
}

function Pip({ className }: { className?: string }) {
  return <span aria-hidden className={cn('size-1.75 shrink-0 rounded-full', className)} />;
}

// ----- components -----

function StackView({ props, children }: { props: PropsOf<'Stack'>; children: ReactNode }) {
  const enter = useEnter<HTMLDivElement>();
  const horizontal = props.direction === 'horizontal';
  return (
    <div
      ref={enter}
      className={cn('flex min-w-0', STACK_GAP[props.gap], horizontal ? 'flex-row flex-wrap items-center' : 'flex-col')}
    >
      {children}
    </div>
  );
}

function GridView({ props, children }: { props: PropsOf<'Grid'>; children: ReactNode }) {
  const enter = useEnter<HTMLDivElement>();
  return (
    <div
      ref={enter}
      className={cn(
        'grid gap-2.5',
        props.columns === 3 ? 'grid-cols-[repeat(auto-fit,minmax(150px,1fr))]' : 'grid-cols-[repeat(auto-fit,minmax(210px,1fr))]',
      )}
    >
      {children}
    </div>
  );
}

function CardView({ props, children }: { props: PropsOf<'Card'>; children: ReactNode }) {
  const enter = useEnter<HTMLDivElement>();
  return (
    <div ref={enter} className={cn('flex flex-col gap-3 rounded-card border p-4', TONE_CARD[props.tone ?? 'neutral'])}>
      {(props.title || props.subtitle) && (
        <div className="flex flex-col gap-0.75">
          {props.title && <span className="text-lead font-semibold text-fg-default">{props.title}</span>}
          {props.subtitle && <span className="text-sm text-fg-muted">{props.subtitle}</span>}
        </div>
      )}
      {children}
    </div>
  );
}

function HeadingView({ props }: { props: PropsOf<'Heading'> }) {
  const enter = useEnter<HTMLHeadingElement>();
  return (
    <h3 ref={enter} className="m-0 text-md font-semibold tracking-[-0.01em] text-fg-default">
      {props.text}
    </h3>
  );
}

function TextView({ props }: { props: PropsOf<'Text'> }) {
  const enter = useEnter<HTMLParagraphElement>();
  return (
    <p ref={enter} className={cn('m-0 text-base leading-relaxed text-pretty', props.muted ? 'text-fg-muted' : 'text-fg-default')}>
      {props.text}
    </p>
  );
}

function MetricView({ props }: { props: PropsOf<'Metric'> }) {
  const enter = useEnter<HTMLDivElement>();
  return (
    <div ref={enter} className="flex flex-col gap-1 rounded-card border border-border-default bg-bg-elevated p-3.5">
      <span className="text-xs text-fg-muted">{props.label}</span>
      <span className={cn('text-xl font-semibold tracking-[-0.02em]', TONE_INK[props.tone ?? 'neutral'])}>{props.value}</span>
      {props.hint && <span className="text-xs text-fg-faint">{props.hint}</span>}
    </div>
  );
}

function BadgeView({ props }: { props: PropsOf<'Badge'> }) {
  const enter = useEnter<HTMLSpanElement>();
  return (
    <span
      ref={enter}
      className={cn(
        'inline-flex h-5.5 items-center self-start rounded-pill border px-2.25 text-2xs font-medium whitespace-nowrap',
        TONE_BADGE[props.tone ?? 'neutral'],
      )}
    >
      {props.label}
    </span>
  );
}

function DividerView() {
  const enter = useEnter<HTMLDivElement>();
  return <div ref={enter} className="h-px bg-border-default" />;
}

function PlanStep({ step }: { step: PropsOf<'Plan'>['steps'][number] }) {
  const enter = useEnter<HTMLDivElement>();
  return (
    <div
      ref={enter}
      className={cn('flex max-w-60 min-w-0 flex-[1_0_150px] flex-col gap-1.25 rounded-row border-2 p-3', STEP_TONE[step.role])}
    >
      <span className={cn('flex items-center gap-1.5 font-mono text-2xs font-medium tracking-label uppercase', TONE_INK[step.role])}>
        <Pip className={TONE_DOT[step.role]} />
        {STEP_LABEL[step.role]}
        {step.app && <span className="truncate normal-case tracking-normal text-fg-muted">· {step.app}</span>}
      </span>
      <span className="text-row font-semibold text-fg-default">{step.title}</span>
      {step.detail && <span className="text-meta leading-snug text-fg-muted">{step.detail}</span>}
    </div>
  );
}

/** The app's own name, when the reply's name for it is one that can start the work. */
function appNameOf(trigger: HireTrigger, triggerApps: Readonly<Record<string, string>>): string | undefined {
  return trigger.app ? (triggerApps[trigger.app.toLowerCase()] ?? trigger.app) : undefined;
}

function PlanView({ props }: { props: PropsOf<'Plan'> }) {
  const enter = useEnter<HTMLDivElement>();
  const { spec, state, triggerApps } = useSpec();
  const current = snapTrigger(getPath(state, STATE_PATHS.trigger));
  const written = snapTrigger(getPath(spec.state, STATE_PATHS.trigger));
  const steps = routineSteps(props.steps, written, current, appNameOf(current, triggerApps));
  return (
    <div ref={enter} className="flex min-w-0 flex-col gap-3.5 rounded-card border border-border-default bg-bg-elevated p-4">
      <div className="flex items-center gap-2">
        <span className="text-lead font-semibold text-fg-default">{props.title || 'Their routine'}</span>
        <span className="ml-auto font-mono text-2xs text-fg-faint">
          {steps.length} {steps.length === 1 ? 'step' : 'steps'}
        </span>
      </div>
      <div className="flex min-w-0 items-stretch overflow-x-auto overflow-y-hidden pb-1 [scrollbar-width:thin]">
        {steps.map((step, index) => (
          <Fragment key={index}>
            {index > 0 && (
              <div aria-hidden className="flex w-5.5 shrink-0 items-center">
                <div className="h-0.5 w-full rounded-full bg-border-strong" />
              </div>
            )}
            <PlanStep step={step} />
          </Fragment>
        ))}
      </div>
    </div>
  );
}

function ScheduleField({ label, children }: { label: string; children: ReactNode }) {
  return (
    <div className="flex flex-col gap-1.5">
      <span className="text-xs font-medium text-fg-muted">{label}</span>
      {children}
    </div>
  );
}

function SchedulePicker({
  label,
  value,
  options,
  onChange,
}: {
  label: string;
  value: string | undefined;
  options: readonly { value: string; label: string }[];
  onChange: (value: string) => void;
}) {
  return (
    <Select value={value} onValueChange={onChange}>
      <SelectTrigger aria-label={label} className="min-w-28 rounded-lg bg-bg-app text-fg-default dark:bg-bg-app">
        <SelectValue />
      </SelectTrigger>
      <SelectContent>
        {options.map((option) => (
          <SelectItem key={option.value} value={option.value}>
            {option.label}
          </SelectItem>
        ))}
      </SelectContent>
    </Select>
  );
}

function ScheduleView({ props, raw }: { props: PropsOf<'Schedule'>; raw: SpecElement['props'] }) {
  const enter = useEnter<HTMLDivElement>();
  const { onValue, triggerApps } = useSpec();
  const [editing, setEditing] = useState(false);
  const path = bindingPath(raw.value);
  const trigger = snapTrigger(props.value);
  const appName = appNameOf(trigger, triggerApps);
  const apps = [...new Set(Object.values(triggerApps))];
  const change = (next: HireTrigger) => path && onValue(path, snapTrigger(next));
  const starts = trigger.kind === 'app_event' ? `app:${appName ?? ''}` : trigger.kind;
  const pickStart = (value: string) => {
    if (value === 'manual' || value === 'schedule') change({ kind: value });
    else change({ kind: 'app_event', app: value.slice('app:'.length) });
  };
  return (
    <div ref={enter} className="flex flex-col gap-3 rounded-card border border-border-default bg-bg-elevated p-4">
      <div className="flex items-center gap-3">
        <div className="flex min-w-0 flex-1 flex-col gap-1">
          <MicroLabel>When they work</MicroLabel>
          <span className="text-base font-medium text-fg-default">{triggerSentence(trigger, appName)}</span>
        </div>
        {path && (
          <Button
            variant="quiet"
            aria-expanded={editing}
            onClick={() => setEditing((on) => !on)}
            className="h-8 rounded-lg border-border-strong px-3 font-semibold text-fg-default"
          >
            {editing ? 'Done' : 'Edit'}
          </Button>
        )}
      </div>
      {editing && path && (
        <div className="flex flex-col gap-3 border-t border-border-default pt-3">
          <ScheduleField label="What starts their work">
            <ToggleGroup
              type="single"
              variant="chips"
              aria-label="What starts their work"
              value={starts}
              onValueChange={(next) => next && pickStart(next)}
              className="flex-wrap gap-1.5"
            >
              <ToggleGroupItem value="manual">When you message them</ToggleGroupItem>
              <ToggleGroupItem value="schedule">On a schedule</ToggleGroupItem>
              {apps.map((app) => (
                <ToggleGroupItem key={app} value={`app:${app}`}>
                  When something new arrives in {app}
                </ToggleGroupItem>
              ))}
            </ToggleGroup>
          </ScheduleField>
          {trigger.kind === 'schedule' && (
            <>
              <ScheduleField label="How often">
                <ToggleGroup
                  type="single"
                  variant="segmented"
                  aria-label="How often"
                  value={trigger.every ?? ''}
                  onValueChange={(next) => next && change({ ...trigger, every: next as HireTrigger['every'] })}
                  className="flex-wrap self-start"
                >
                  {SCHEDULE_EVERY.map((every) => (
                    <ToggleGroupItem key={every} value={every} className="border-0">
                      {EVERY_LABEL[every]}
                    </ToggleGroupItem>
                  ))}
                </ToggleGroup>
              </ScheduleField>
              {trigger.every !== 'hour' && (
                <div className="flex flex-wrap gap-4">
                  {trigger.every === 'week' && (
                    <ScheduleField label="On">
                      <SchedulePicker
                        label="Day of the week"
                        value={trigger.day}
                        options={WEEKDAYS.map((day) => ({ value: day, label: `${day.charAt(0).toUpperCase()}${day.slice(1)}` }))}
                        onChange={(day) => change({ ...trigger, day })}
                      />
                    </ScheduleField>
                  )}
                  {trigger.every === 'month' && (
                    <ScheduleField label="On day">
                      <SchedulePicker
                        label="Day of the month"
                        value={trigger.day}
                        options={MONTH_DAYS.map((day) => ({ value: day, label: day }))}
                        onChange={(day) => change({ ...trigger, day })}
                      />
                    </ScheduleField>
                  )}
                  <ScheduleField label="At">
                    <SchedulePicker
                      label="Time"
                      value={trigger.at}
                      options={SCHEDULE_TIMES.map((time) => ({ value: time, label: time }))}
                      onChange={(at) => change({ ...trigger, at })}
                    />
                  </ScheduleField>
                </div>
              )}
            </>
          )}
        </div>
      )}
    </div>
  );
}

function AgentCardView({ props }: { props: PropsOf<'AgentCard'> }) {
  const enter = useEnter<HTMLDivElement>();
  const status = AGENT_STATUS[props.status];
  return (
    <div ref={enter} className={cn('flex gap-3.5 rounded-card border p-4', TONE_CARD.agent)}>
      <span
        aria-hidden
        className="grid size-10.5 shrink-0 place-items-center rounded-full border-2 border-node-agent-edge bg-node-agent-fill text-md font-semibold text-node-agent-ink"
      >
        {Array.from(props.name)[0]?.toLocaleUpperCase() ?? 'A'}
      </span>
      <div className="flex min-w-0 flex-1 flex-col gap-1.25">
        <div className="flex flex-wrap items-center gap-2">
          <span className="text-lead font-semibold text-fg-default">{props.name}</span>
          <span className="text-meta text-fg-muted">{props.role}</span>
          <span className={cn('ml-auto flex items-center gap-1.5 text-xs', status.ink)}>
            <Pip className={status.dot} />
            {status.label}
          </span>
        </div>
        {props.description && <span className="text-row leading-normal text-fg-muted">{props.description}</span>}
        {props.apps.length > 0 && (
          <div className="mt-1 flex flex-wrap gap-1.5">
            {props.apps.map((app) => (
              <span
                key={app}
                className="inline-flex h-5.5 items-center rounded-md border border-border-default bg-bg-hover px-2 text-xs text-fg-default"
              >
                {app}
              </span>
            ))}
          </div>
        )}
      </div>
    </div>
  );
}

function ListView({ props }: { props: PropsOf<'List'> }) {
  const enter = useEnter<HTMLDivElement>();
  return (
    <div ref={enter} className="flex flex-col rounded-card border border-border-default bg-bg-elevated p-1.5">
      {props.items.map((item, index) => (
        <div key={index} className={cn('flex items-start gap-2.5 p-2.5', index > 0 && 'border-t border-border-default')}>
          <Pip className={cn('mt-1.5', TONE_DOT[item.tone ?? 'neutral'])} />
          <div className="flex min-w-0 flex-1 flex-col gap-0.5">
            <span className="text-base font-medium text-fg-default">{item.title}</span>
            {item.detail && <span className="text-meta leading-snug text-fg-muted">{item.detail}</span>}
          </div>
          {item.meta && <span className="mt-0.5 font-mono text-2xs whitespace-nowrap text-fg-faint">{item.meta}</span>}
        </div>
      ))}
    </div>
  );
}

/** A message waiting to go out: the channel, who it is for, the text.
 *  Shared with the approval step, which shows drafts the same way. */
export function MessagePreview({
  channel,
  to,
  subject,
  body,
  className,
}: {
  channel?: string;
  to?: string;
  subject?: string;
  body: string;
  className?: string;
}) {
  return (
    <div className={cn('overflow-hidden rounded-card border border-border-default bg-bg-elevated', className)}>
      <div className="flex items-center gap-2 border-b border-border-default bg-bg-panel px-3.5 py-2.5">
        <MicroLabel className="text-node-model-ink">{channel || 'Draft'}</MicroLabel>
        <span className="truncate text-sm text-fg-muted">to {to || '…'}</span>
      </div>
      {subject && <div className="px-3.5 pt-3 text-base font-semibold text-fg-default">{subject}</div>}
      <div className="px-3.5 pt-2.5 pb-3.5 text-base leading-relaxed whitespace-pre-wrap text-fg-default">{body}</div>
    </div>
  );
}

function DraftView({ props }: { props: PropsOf<'Draft'> }) {
  const enter = useEnter<HTMLDivElement>();
  return (
    <div ref={enter}>
      <MessagePreview channel={props.channel} to={props.to} subject={props.subject} body={props.body} />
    </div>
  );
}

function ProgressView({ props }: { props: PropsOf<'Progress'> }) {
  const enter = useEnter<HTMLDivElement>();
  const barRef = useRef<HTMLDivElement>(null);
  const shown = useRef<number | null>(null);
  useLayoutEffect(() => {
    if (shown.current === props.value) return;
    shown.current = props.value;
    animate(barRef.current, [{ width: '0%' }, { width: `${props.value}%` }], { duration: 900, easing: 'spring', fill: 'none' });
  }, [props.value]);
  return (
    <div ref={enter} className="flex flex-col gap-1.5">
      <div className="flex text-sm text-fg-muted">
        {props.label}
        <span className="ml-auto font-mono text-xs">{Math.round(props.value)}%</span>
      </div>
      <div className="h-1.5 overflow-hidden rounded-pill bg-border-default">
        {/* Width is the runtime value. */}
        <div
          ref={barRef}
          className={cn('h-full rounded-pill', TONE_DOT[!props.tone || props.tone === 'neutral' ? 'model' : props.tone])}
          style={{ width: `${props.value}%` }}
        />
      </div>
    </div>
  );
}

function ToggleView({ props, raw }: { props: PropsOf<'Toggle'>; raw: SpecElement['props'] }) {
  const enter = useEnter<HTMLDivElement>();
  const { onValue } = useSpec();
  const path = bindingPath(raw.value);
  return (
    <div ref={enter} className="flex items-center gap-3.5 py-1">
      <div className="flex flex-1 flex-col gap-0.5">
        <span className="text-base font-medium text-fg-default">{props.label}</span>
        {props.description && <span className="text-meta text-fg-muted">{props.description}</span>}
      </div>
      <Switch
        size="md"
        tone="run"
        aria-label={props.label}
        checked={props.value}
        onCheckedChange={(next) => path && onValue(path, next)}
      />
    </div>
  );
}

function ChoiceView({ props, raw }: { props: PropsOf<'Choice'>; raw: SpecElement['props'] }) {
  const enter = useEnter<HTMLDivElement>();
  const { onValue } = useSpec();
  const path = bindingPath(raw.value);
  if (props.options.length === 0) return null;
  return (
    <div ref={enter} className="flex flex-col gap-1.5">
      <span className="text-xs font-medium text-fg-muted">{props.label}</span>
      <ToggleGroup
        type="single"
        variant="segmented"
        aria-label={props.label}
        value={props.value ?? ''}
        onValueChange={(next) => next && path && onValue(path, next)}
        className="flex-wrap self-start"
      >
        {props.options.map((option) => (
          <ToggleGroupItem key={option} value={option} className="border-0">
            {option}
          </ToggleGroupItem>
        ))}
      </ToggleGroup>
    </div>
  );
}

function InputView({ props, raw }: { props: PropsOf<'Input'>; raw: SpecElement['props'] }) {
  const enter = useEnter<HTMLLabelElement>();
  const { onValue } = useSpec();
  const path = bindingPath(raw.value);
  return (
    <label ref={enter} className="flex flex-col gap-1.5 text-xs font-medium text-fg-muted">
      {props.label}
      <Input
        value={props.value}
        placeholder={props.placeholder}
        maxLength={200}
        disabled={!path}
        onChange={(event) => path && onValue(path, event.target.value)}
        className="h-9.5 rounded-lg bg-bg-app px-3 text-base font-normal text-fg-default md:text-base dark:bg-bg-app"
      />
    </label>
  );
}

function ButtonView({ props, raw }: { props: PropsOf<'Button'>; raw: SpecElement['props'] }) {
  const { onAction } = useSpec();
  const enter = useEnter<HTMLButtonElement>();
  const run = () => onAction(props.action, raw.actionParams);
  if (props.variant === 'primary') {
    return (
      <ActionButton
        ref={enter}
        intent="run"
        onClick={run}
        className="h-8.5 self-start rounded-lg px-4 whitespace-nowrap active:translate-y-px"
      >
        {props.label}
      </ActionButton>
    );
  }
  return (
    <Button
      ref={enter}
      variant="quiet"
      onClick={run}
      className="h-8.5 self-start rounded-lg border-border-strong px-3.5 font-semibold text-fg-default"
    >
      {props.label}
    </Button>
  );
}

// ----- tree -----

class ElementBoundary extends Component<{ children: ReactNode }, { failed: boolean }> {
  state = { failed: false };
  static getDerivedStateFromError() {
    return { failed: true };
  }
  componentDidCatch(error: unknown) {
    console.warn('[genui] a setup-screen element failed to render', error);
  }
  render() {
    return this.state.failed ? null : this.props.children;
  }
}

function renderLeaf(type: ComponentType, props: unknown, raw: SpecElement['props'], children: ReactNode): ReactNode {
  switch (type) {
    case 'Stack':
      return <StackView props={props as PropsOf<'Stack'>}>{children}</StackView>;
    case 'Grid':
      return <GridView props={props as PropsOf<'Grid'>}>{children}</GridView>;
    case 'Card':
      return <CardView props={props as PropsOf<'Card'>}>{children}</CardView>;
    case 'Heading':
      return <HeadingView props={props as PropsOf<'Heading'>} />;
    case 'Text':
      return <TextView props={props as PropsOf<'Text'>} />;
    case 'Metric':
      return <MetricView props={props as PropsOf<'Metric'>} />;
    case 'Badge':
      return <BadgeView props={props as PropsOf<'Badge'>} />;
    case 'Plan':
      return <PlanView props={props as PropsOf<'Plan'>} />;
    case 'Schedule':
      return <ScheduleView props={props as PropsOf<'Schedule'>} raw={raw} />;
    case 'AgentCard':
      return <AgentCardView props={props as PropsOf<'AgentCard'>} />;
    case 'List':
      return <ListView props={props as PropsOf<'List'>} />;
    case 'Draft':
      return <DraftView props={props as PropsOf<'Draft'>} />;
    case 'Progress':
      return <ProgressView props={props as PropsOf<'Progress'>} />;
    case 'Toggle':
      return <ToggleView props={props as PropsOf<'Toggle'>} raw={raw} />;
    case 'Choice':
      return <ChoiceView props={props as PropsOf<'Choice'>} raw={raw} />;
    case 'Input':
      return <InputView props={props as PropsOf<'Input'>} raw={raw} />;
    case 'Button':
      return <ButtonView props={props as PropsOf<'Button'>} raw={raw} />;
    case 'Divider':
      return <DividerView />;
  }
}

function SpecNode({ id }: { id: string }) {
  const { spec, state, revealed } = useSpec();
  const element = spec.elements[id];
  if (!element || !revealed.has(id)) return null;
  if (element.visible !== undefined && !evalCondition(element.visible, state)) return null;
  const parsed = PROP_SCHEMAS[element.type].safeParse(resolveValue(element.props, state) ?? {});
  if (!parsed.success) return null;
  const children = element.children.map((child) => <SpecNode key={child} id={child} />);
  return <ElementBoundary>{renderLeaf(element.type, parsed.data, element.props, children)}</ElementBoundary>;
}

export function SpecView({ spec, state, revealed, onAction, onValue, triggerApps }: SpecContextValue) {
  return (
    <SpecContext.Provider value={{ spec, state, revealed, onAction, onValue, triggerApps }}>
      <div className="flex min-w-0 flex-col gap-3">
        <SpecNode id={spec.root} />
      </div>
    </SpecContext.Provider>
  );
}
