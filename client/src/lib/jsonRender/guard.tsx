/* eslint-disable react-refresh/only-export-components -- the guard element, its contexts and the entrance hook travel together; renderers use guarded()'s result, never this file's component. */
/**
 * The wrapper every generated-UI component goes through.
 *
 * json-render hands a component the element's props resolved but
 * unchecked: defineRegistry passes `element.props` straight through,
 * whatever the catalog's schema says. `guarded` reads them with the
 * component's schema first. Schemas here are forgiving (a `.catch` per
 * prop), so a model's odd value degrades that one prop; with a stricter
 * schema each prop is still read on its own, and only a prop with no
 * usable value at all (not even its default) drops the element. It can
 * also hold a component to the parents it may appear under, and gives each
 * element one entrance the first time it appears, but only when it appears
 * live (inside `LiveUiContext` set to true), so a UI shown again from
 * history does not animate.
 *
 * Error isolation needs nothing here: json-render renders every element
 * inside its own error boundary, so one that throws renders nothing and the
 * rest of the UI stays.
 *
 * No json-render import at run time (types only), so this module costs
 * Home's first chunk nothing.
 */

import { createContext, useCallback, useContext, useMemo, useRef, type ComponentType, type ReactNode } from 'react';
import { z } from 'zod';
import type { BaseComponentProps } from '@json-render/react';
import { animate } from '@/lib/motion';

/** True where a generated UI is arriving now (it animates in); false or
 *  absent for one shown again (history). */
export const LiveUiContext = createContext(false);

/** The type of the element a component renders inside; null at the root. */
const ParentTypeContext = createContext<string | null>(null);

/** What a guarded component receives: json-render's arguments with the
 *  props read through the schema, minus slots (sanitize.ts strips them),
 *  plus the ref that plays its entrance. */
export type GuardedComponentProps<P> = Omit<BaseComponentProps<P>, 'slots'> & {
  /** Attach to the element's outermost node. */
  enterRef: (node: HTMLElement | null) => void;
};

export interface GuardOptions {
  /** The parent types this component may appear under (null: the root).
   *  Anywhere else it renders nothing. Default: anywhere. */
  parents?: readonly (string | null)[];
}

interface GuardConfig {
  type: string;
  schema: z.ZodType;
  Component: ComponentType<GuardedComponentProps<any>>;
  parents?: readonly (string | null)[];
}

const ENTER_KEYFRAMES: Keyframe[] = [
  { opacity: 0, transform: 'translateY(8px) scale(.985)', filter: 'blur(3px)' },
  { opacity: 1, transform: 'none', filter: 'blur(0)' },
];

function isRecord(value: unknown): value is Record<string, unknown> {
  return typeof value === 'object' && value !== null && !Array.isArray(value);
}

/** Props read through `schema`: all at once when they pass, otherwise one
 *  prop at a time, each falling back to its default; null when a prop has
 *  neither a usable value nor a default. */
export function parseProps<S extends z.ZodType>(schema: S, raw: unknown): z.output<S> | null {
  const input = isRecord(raw) ? raw : {};
  const whole = schema.safeParse(input);
  if (whole.success) return whole.data;
  if (!(schema instanceof z.ZodObject)) return null;
  const out: Record<string, unknown> = {};
  for (const [key, field] of Object.entries(schema.shape as Record<string, z.ZodType>)) {
    const own = field.safeParse(input[key]);
    const result = own.success ? own : field.safeParse(undefined);
    if (!result.success) return null;
    if (result.data !== undefined) out[key] = result.data;
  }
  return out as z.output<S>;
}

/** A ref that plays the generated-UI entrance on the node it first gets,
 *  once, when the UI is live. */
export function useEntrance<T extends Element = HTMLElement>(): (node: T | null) => void {
  const live = useContext(LiveUiContext);
  const entered = useRef(false);
  return useCallback(
    (node: T | null) => {
      if (!node || entered.current) return;
      entered.current = true;
      if (live) animate(node, ENTER_KEYFRAMES, { duration: 'genui-enter', easing: 'spring' });
    },
    [live],
  );
}

function Guard({ config, ctx }: { config: GuardConfig; ctx: BaseComponentProps<unknown> }) {
  const parent = useContext(ParentTypeContext);
  const enterRef = useEntrance<HTMLElement>();
  const props = useMemo(() => parseProps(config.schema, ctx.props), [config.schema, ctx.props]);
  if (props === null) return null;
  if (config.parents && !config.parents.includes(parent)) return null;
  const { Component } = config;
  return (
    <ParentTypeContext.Provider value={config.type}>
      <Component
        props={props}
        emit={ctx.emit}
        on={ctx.on}
        bindings={ctx.bindings}
        loading={ctx.loading}
        enterRef={enterRef}
      >
        {ctx.children}
      </Component>
    </ParentTypeContext.Provider>
  );
}

/**
 * A json-render component function for defineRegistry: `Component` drawn
 * with its props read through `schema`.
 */
export function guarded<S extends z.ZodType>(
  type: string,
  schema: S,
  Component: ComponentType<GuardedComponentProps<z.output<S>>>,
  options: GuardOptions = {},
): (ctx: BaseComponentProps<unknown>) => ReactNode {
  const config: GuardConfig = { type, schema, Component, parents: options.parents };
  // defineRegistry calls this as a plain function inside its own component,
  // so it renders the guard rather than running hooks itself.
  return (ctx) => <Guard config={config} ctx={ctx} />;
}
