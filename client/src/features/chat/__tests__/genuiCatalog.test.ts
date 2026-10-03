/**
 * The chat catalogue (genui/catalog.ts) against the server's manifest
 * (server/config/chat_genui_catalog.json), which the server checks every
 * spec against: the same components, roles, children, bound props, events,
 * actions and limits. The server's tests read this side the same way.
 */

import { readFileSync } from 'node:fs';
import { resolve } from 'node:path';
import { describe, expect, it } from 'vitest';
import {
  BIND_PROPS,
  CHAT_ACTIONS,
  CHAT_COMPONENT_TYPES,
  CHAT_LIMITS,
  CHAT_PROP_SCHEMAS,
  COMPONENT_CHILDREN,
  COMPONENT_EVENTS,
  COMPONENT_ROLES,
  allowedParents,
} from '../genui/catalog';

interface ManifestComponent {
  role: string;
  children?: 'any' | string[];
  bind?: string;
  events?: string[];
  props: { properties?: Record<string, unknown> };
}

const manifest = JSON.parse(readFileSync(resolve(__dirname, '../../../../../server/config/chat_genui_catalog.json'), 'utf-8')) as {
  limits: Record<string, number>;
  components: Record<string, ManifestComponent>;
  actions: Record<string, unknown>;
};

describe('the chat catalogue', () => {
  it('has the manifest’s components, in its order', () => {
    expect([...CHAT_COMPONENT_TYPES]).toEqual(Object.keys(manifest.components));
  });

  it('agrees on roles, children, bound props and events', () => {
    for (const type of CHAT_COMPONENT_TYPES) {
      const component = manifest.components[type];
      expect(COMPONENT_ROLES[type], type).toBe(component.role);
      expect((COMPONENT_CHILDREN as Record<string, unknown>)[type] ?? null, type).toEqual(component.children ?? null);
      expect((BIND_PROPS as Record<string, unknown>)[type] ?? null, type).toBe(component.bind ?? null);
      expect((COMPONENT_EVENTS as Record<string, unknown>)[type] ?? null, type).toEqual(component.events ?? null);
    }
  });

  it('reads every prop the manifest declares', () => {
    for (const type of CHAT_COMPONENT_TYPES) {
      const declared = Object.keys(manifest.components[type].props.properties ?? {}).sort();
      expect(Object.keys(CHAT_PROP_SCHEMAS[type].shape).sort(), type).toEqual(declared);
    }
  });

  it('agrees on actions and limits', () => {
    expect([...CHAT_ACTIONS].sort()).toEqual(Object.keys(manifest.actions).sort());
    expect(CHAT_LIMITS).toEqual({
      maxElements: manifest.limits.max_elements,
      maxDepth: manifest.limits.max_depth,
      maxBytes: manifest.limits.max_bytes,
      maxChildren: manifest.limits.max_children,
      maxIdLength: manifest.limits.max_id_length,
      maxPathSegments: manifest.limits.max_path_segments,
    });
  });

  it('places a component only where a layout may hold it', () => {
    expect(allowedParents('Button')).toEqual([null, 'Stack', 'Row']);
    expect(allowedParents('Select')).toEqual([null, 'Stack', 'Card']);
    expect(allowedParents('SlotPicker')).toEqual([null, 'Stack']);
  });

  it('degrades an odd prop instead of failing the element', () => {
    expect(CHAT_PROP_SCHEMAS.Select.parse({ label: 'Service', options: ['A', 'A', 7, null, 'B'] })).toEqual({
      label: 'Service',
      options: ['A', '7', 'B'],
    });
    expect(CHAT_PROP_SCHEMAS.Callout.parse({ tone: 'neon', text: 'Heads up' })).toEqual({ tone: 'info', text: 'Heads up' });
    expect(CHAT_PROP_SCHEMAS.BarChart.parse({ title: 'Replies', bars: [{ label: 'Mon', value: -3 }, { label: 'Tue', value: '4' }] }).bars).toEqual([
      { label: 'Mon', value: 0 },
      { label: 'Tue', value: 4 },
    ]);
  });
});
