/**
 * What an employee may show inside a chat reply (design handoff chat,
 * "Generated UI"): the components, what they hold, which prop a control
 * binds, and the limits. The server checks a spec against
 * server/config/chat_genui_catalog.json before anything shows; this file is
 * the client's side of the same vocabulary, and a test reads that manifest
 * off disk and checks the two agree (keep the lists below plain literals).
 *
 * Prop schemas are forgiving by design: an odd value degrades that one prop
 * (a default, a clamped string) instead of failing the element.
 */

import { z } from 'zod';

export const CHAT_COMPONENT_TYPES = [
  'Stack',
  'Row',
  'Card',
  'SlotPicker',
  'Select',
  'Toggle',
  'TextField',
  'Text',
  'StatGrid',
  'BarChart',
  'Callout',
  'Button',
] as const;
export type ChatComponentType = (typeof CHAT_COMPONENT_TYPES)[number];

export type ComponentRole = 'layout' | 'input' | 'display' | 'action';

export const COMPONENT_ROLES = {
  Stack: 'layout',
  Row: 'layout',
  Card: 'layout',
  SlotPicker: 'input',
  Select: 'input',
  Toggle: 'input',
  TextField: 'input',
  Text: 'display',
  StatGrid: 'display',
  BarChart: 'display',
  Callout: 'display',
  Button: 'action',
} as const satisfies Record<ChatComponentType, ComponentRole>;

/** What a layout may hold: any component, or only these. */
export const COMPONENT_CHILDREN = {
  Stack: 'any',
  Row: ['Button'],
  Card: ['Select', 'Toggle', 'TextField', 'Text'],
} as const satisfies Partial<Record<ChatComponentType, 'any' | readonly ChatComponentType[]>>;

/** The prop each control reads and writes through `{"$bindState": "/path"}`. */
export const BIND_PROPS = {
  SlotPicker: 'value',
  Select: 'value',
  Toggle: 'checked',
  TextField: 'value',
} as const satisfies Partial<Record<ChatComponentType, string>>;

export const COMPONENT_EVENTS = {
  Button: ['press'],
} as const satisfies Partial<Record<ChatComponentType, readonly string[]>>;

/** Actions the chat runs itself; any other comes back to the employee. */
export const CHAT_ACTIONS = ['ask', 'setState'] as const;

export const CHAT_LIMITS = {
  maxElements: 24,
  maxDepth: 6,
  maxBytes: 16384,
  maxChildren: 12,
  maxIdLength: 40,
  maxPathSegments: 6,
} as const;

// ----- prop schemas (resolved values) -----

function clamp(value: string, max: number): string {
  const flat = value.replace(/\s+/g, ' ').trim();
  return flat.length > max ? `${flat.slice(0, max - 1).trimEnd()}…` : flat;
}

const scalar = z.union([z.string(), z.number(), z.boolean()]).transform(String);
/** A one-line string, whitespace collapsed and clamped; '' when unusable. */
const line = (max: number) => scalar.catch('').transform((s) => clamp(s, max));
const optionalLine = (max: number) => line(max).optional().catch(undefined);
const flag = z.boolean().optional().catch(undefined);
const gap = z.enum(['sm', 'md']).catch('md');

function listOf<T extends z.ZodType>(item: T, max: number) {
  return z
    .array(z.unknown())
    .catch([])
    .transform((items) =>
      items
        .map((raw) => item.safeParse(raw))
        .filter((result) => result.success)
        .map((result) => result.data as z.output<T>)
        .slice(0, max),
    );
}

const slotOption = z.object({
  id: line(40),
  time: line(40),
  detail: optionalLine(60),
  recommended: flag,
  unavailable: flag,
  note: optionalLine(80),
});

const stat = z.object({
  label: line(40),
  value: line(40),
  delta: optionalLine(40),
  tone: z.enum(['up', 'down']).optional().catch(undefined),
});

const bar = z.object({
  label: line(40),
  value: z.coerce
    .number()
    .catch(0)
    .transform((value) => (Number.isFinite(value) && value > 0 ? value : 0)),
});

export const CHAT_PROP_SCHEMAS = {
  Stack: z.object({ gap }),
  Row: z.object({ gap }),
  Card: z.object({ title: line(80), description: optionalLine(240) }),
  SlotPicker: z.object({
    label: line(80),
    hint: optionalLine(120),
    value: optionalLine(40),
    options: listOf(slotOption, 8).transform((options) => options.filter((option) => option.id && option.time)),
  }),
  Select: z.object({
    label: line(80),
    value: optionalLine(40),
    options: listOf(line(40), 6).transform((options) => [...new Set(options.filter(Boolean))]),
  }),
  Toggle: z.object({ label: line(80), hint: optionalLine(120), checked: z.boolean().catch(false) }),
  TextField: z.object({
    label: line(80),
    placeholder: optionalLine(80),
    value: scalar.catch('').transform((s) => s.slice(0, 400)),
  }),
  Text: z.object({ text: scalar.catch('').transform((s) => s.trim().slice(0, 600)) }),
  StatGrid: z.object({ items: listOf(stat, 6).transform((items) => items.filter((item) => item.label)) }),
  BarChart: z.object({ title: line(80), bars: listOf(bar, 12).transform((bars) => bars.filter((item) => item.label)) }),
  Callout: z.object({ tone: z.enum(['info', 'warning', 'danger', 'success']).catch('info'), text: line(400) }),
  Button: z.object({ label: line(40), variant: z.enum(['primary', 'secondary']).catch('secondary') }),
} satisfies Record<ChatComponentType, z.ZodType>;

export type ChatPropsOf<T extends ChatComponentType> = z.output<(typeof CHAT_PROP_SCHEMAS)[T]>;

export function isChatComponentType(value: unknown): value is ChatComponentType {
  return typeof value === 'string' && (CHAT_COMPONENT_TYPES as readonly string[]).includes(value);
}

/** The parents each component may sit under (null: the root), from what
 *  each layout may hold. */
export function allowedParents(type: ChatComponentType): (ChatComponentType | null)[] {
  const parents: (ChatComponentType | null)[] = [null];
  for (const [layout, children] of Object.entries(COMPONENT_CHILDREN) as [ChatComponentType, 'any' | readonly ChatComponentType[]][]) {
    if (children === 'any' || children.includes(type)) parents.push(layout);
  }
  return parents;
}

// ----- the catalogue as json-render takes it -----

type ChatCatalogComponents = {
  [T in ChatComponentType]: { props: (typeof CHAT_PROP_SCHEMAS)[T]; slots?: string[] };
};

function catalogComponents(): ChatCatalogComponents {
  const components: Partial<Record<ChatComponentType, { props: z.ZodType; slots?: string[] }>> = {};
  for (const type of CHAT_COMPONENT_TYPES) {
    const props = CHAT_PROP_SCHEMAS[type];
    components[type] = type in COMPONENT_CHILDREN ? { props, slots: ['default'] } : { props };
  }
  return components as ChatCatalogComponents;
}

/** What json-render's defineCatalog takes for a chat reply's UI. The
 *  handlers come from the chat when it renders (any action name is
 *  routed: genui/actions.ts); setState is json-render's own. */
export const CHAT_CATALOG = { components: catalogComponents(), actions: {} };
