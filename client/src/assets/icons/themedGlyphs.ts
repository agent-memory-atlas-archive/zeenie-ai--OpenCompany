/**
 * themedGlyphs — per-theme SVG glyph set ported from
 * `design_handoff_machinaos_themes/app/icons.js`.
 *
 * Atomic declares its own glyph language for the 29 conceptual keys
 * (agent, trigger, tool, memory, output, send, file, folder, settings,
 * credentials, run, save, stop, plus, close, search, chevron, check,
 * warning, error, info, sidebar, upload, download, copy, moon, deploy,
 * grid, shield). Light and dark have no set.
 *
 * Each value is a complete `<svg>` string with a plain 32x32 viewBox.
 *
 * `width="100%" height="100%"` lets the SVG fill the wrapper that
 * `<NodeIcon>` puts around it, so Tailwind sizing classes on the
 * wrapper (`h-6 w-6`, etc.) stay authoritative.
 *
 * Themes that omit a key (`Partial<Record<IconKey, string>>`) fall
 * through to the existing `<NodeIcon>` dispatch (lucide / lobehub /
 * asset / emoji).
 *
 * SECURITY: every value here is author-trusted markup committed to
 * the repo, never user input. `<NodeIcon>` injects via
 * `dangerouslySetInnerHTML`; that's safe for this constant set
 * because nothing in this file is reachable from network or user
 * state. Do NOT extend `THEMED_GLYPHS` with values built from
 * runtime input.
 */

import type { ThemeName } from '../../contexts/ThemeContext';

/** The 29 conceptual icon keys. Any of these may be passed as the
 *  `icon` prop to `<NodeIcon>` to opt into per-theme glyph dispatch. */
export type IconKey =
  | 'agent' | 'trigger' | 'tool' | 'memory' | 'output' | 'send'
  | 'file' | 'folder' | 'settings' | 'credentials'
  | 'run' | 'save' | 'stop'
  | 'plus' | 'close' | 'search' | 'chevron' | 'check'
  | 'warning' | 'error' | 'info'
  | 'sidebar' | 'upload' | 'download' | 'copy'
  | 'moon' | 'deploy' | 'grid' | 'shield';

/** Set of all valid `IconKey` strings, exposed for fast `.has()`
 *  membership checks at the resolver call site. */
export const ICON_KEYS: ReadonlySet<IconKey> = new Set<IconKey>([
  'agent', 'trigger', 'tool', 'memory', 'output', 'send',
  'file', 'folder', 'settings', 'credentials',
  'run', 'save', 'stop',
  'plus', 'close', 'search', 'chevron', 'check',
  'warning', 'error', 'info',
  'sidebar', 'upload', 'download', 'copy',
  'moon', 'deploy', 'grid', 'shield',
]);

/** Outer wrapper, baked into each entry below: a plain
 *  `viewBox="0 0 32 32" fill="none"`. */

const SVG_OPEN = '<svg viewBox="0 0 32 32" fill="none" width="100%" height="100%" xmlns="http://www.w3.org/2000/svg">';

const wrap = (inner: string): string => `${SVG_OPEN}${inner}</svg>`;

export const THEMED_GLYPHS: Partial<Record<ThemeName, Partial<Record<IconKey, string>>>> = {
  // ─────────── Atomic Modern — boomerang & starburst, Eames cartoon ───────────
  atomic: {
    agent: wrap(`<g stroke="#2a3a4a" stroke-width="2" fill="#e85a26" stroke-linejoin="round"><circle cx="16" cy="11" r="4"/><path d="M8 25 Q8 18, 16 18 Q24 18, 24 25"/></g>`),
    trigger: wrap(`<g stroke="#2a3a4a" stroke-width="2" fill="#d8a838" stroke-linejoin="round"><path d="M16 3 L11 16 L16 16 L13 29 L23 13 L17 13 L21 3 Z"/></g>`),
    tool: wrap(`<g stroke="#2a3a4a" stroke-width="2" fill="#3a9aa0" stroke-linejoin="round"><path d="M5 25 L18 12 M22 8 L26 12 M22 8 Q28 2, 22 4 L18 8 Z"/></g>`),
    memory: wrap(`<g stroke="#2a3a4a" stroke-width="2" fill="#3a9aa0"><rect x="5" y="9" width="22" height="14" rx="3"/><path d="M5 14 L27 14 M5 19 L27 19" stroke="#2a3a4a"/></g>`),
    output: wrap(`<g stroke="#2a3a4a" stroke-width="2.4" fill="none" stroke-linecap="round" stroke-linejoin="round"><path d="M5 16 L23 16 L18 11 M23 16 L18 21"/></g>`),
    send: wrap(`<g stroke="#2a3a4a" stroke-width="2" fill="#e85a26" stroke-linejoin="round"><path d="M4 28 L28 4 L18 14 L12 11 L11 19 Z"/></g>`),
    file: wrap(`<g stroke="#2a3a4a" stroke-width="2" fill="#ffffff" stroke-linejoin="round"><path d="M9 4 L20 4 L24 8 L24 28 L9 28 Z"/><path d="M20 4 L20 8 L24 8" fill="#d8a838"/></g>`),
    folder: wrap(`<g stroke="#2a3a4a" stroke-width="2" fill="#d8a838" stroke-linejoin="round"><path d="M4 11 L13 11 L15 13 L28 13 L28 25 L4 25 Z"/></g>`),
    settings: wrap(`<g stroke="#2a3a4a" stroke-width="2" fill="#3a9aa0"><circle cx="16" cy="16" r="4"/><path d="M16 3 L16 8 M16 24 L16 29 M3 16 L8 16 M24 16 L29 16" stroke-linecap="round"/></g>`),
    credentials: wrap(`<g stroke="#2a3a4a" stroke-width="2" fill="#d8a838"><circle cx="12" cy="16" r="4"/><path d="M16 16 L28 16 L28 20 M23 16 L23 21 M19 16 L19 19"/></g>`),
    run: wrap(`<g stroke="#2a3a4a" stroke-width="2" fill="#5a8a5a" stroke-linejoin="round"><path d="M9 5 L26 16 L9 27 Z"/></g>`),
    save: wrap(`<g stroke="#2a3a4a" stroke-width="2" fill="#e85a26" stroke-linejoin="round"><path d="M16 4 L20 13 L29 14 L22 20 L24 29 L16 25 L8 29 L10 20 L3 14 L12 13 Z"/></g>`),
    stop: wrap(`<g stroke="#2a3a4a" stroke-width="2" fill="#e85a26"><rect x="7" y="7" width="18" height="18" rx="2"/></g>`),
    plus: wrap(`<g stroke="#2a3a4a" stroke-width="3" stroke-linecap="round"><path d="M16 6 L16 26 M6 16 L26 16"/></g>`),
    close: wrap(`<g stroke="#2a3a4a" stroke-width="3" stroke-linecap="round"><path d="M8 8 L24 24 M24 8 L8 24"/></g>`),
    search: wrap(`<g stroke="#2a3a4a" stroke-width="2.4" fill="#d8a838" stroke-linecap="round"><circle cx="13" cy="13" r="6"/><path d="M18 18 L26 26"/></g>`),
    chevron: wrap(`<g stroke="#2a3a4a" stroke-width="2.4" fill="none" stroke-linecap="round" stroke-linejoin="round"><path d="M11 9 L20 16 L11 23"/></g>`),
    check: wrap(`<g stroke="#5a8a5a" stroke-width="3" fill="none" stroke-linecap="round" stroke-linejoin="round"><path d="M6 16 L13 23 L26 8"/></g>`),
    warning: wrap(`<g stroke="#2a3a4a" stroke-width="2" fill="#d8a838" stroke-linejoin="round"><path d="M16 4 L28 26 L4 26 Z"/><path d="M16 13 L16 20" stroke-linecap="round"/></g>`),
    error: wrap(`<g stroke="#2a3a4a" stroke-width="2" fill="#e85a26"><circle cx="16" cy="16" r="10"/><path d="M11 11 L21 21 M21 11 L11 21" stroke="#ffffff" stroke-linecap="round"/></g>`),
    info: wrap(`<g stroke="#2a3a4a" stroke-width="2" fill="#3a9aa0"><circle cx="16" cy="16" r="10"/><path d="M16 14 L16 22 M16 10 L16 11" stroke="#ffffff" stroke-linecap="round"/></g>`),
    sidebar: wrap(`<g stroke="#2a3a4a" stroke-width="2" fill="#3a9aa0"><rect x="5" y="6" width="22" height="20" rx="2"/><path d="M12 6 L12 26"/></g>`),
    upload: wrap(`<g stroke="#2a3a4a" stroke-width="2.4" fill="none" stroke-linecap="round" stroke-linejoin="round"><path d="M16 24 L16 7 M9 14 L16 7 L23 14"/></g>`),
    download: wrap(`<g stroke="#2a3a4a" stroke-width="2.4" fill="none" stroke-linecap="round" stroke-linejoin="round"><path d="M16 8 L16 25 M9 18 L16 25 L23 18"/></g>`),
    copy: wrap(`<g stroke="#2a3a4a" stroke-width="2" fill="#d8a838" stroke-linejoin="round"><rect x="8" y="8" width="13" height="16" rx="1"/><path d="M12 4 L25 4 L25 20 L21 20"/></g>`),
    moon: wrap(`<g stroke="#2a3a4a" stroke-width="2" fill="#d8a838" stroke-linejoin="round"><path d="M24 18 Q24 25, 16 25 Q8 25, 8 16 Q8 8, 16 8 Q12 12, 12 16 Q12 22, 18 22 Q22 22, 24 18 Z"/></g>`),
    deploy: wrap(`<g stroke="#2a3a4a" stroke-width="2" fill="#e85a26" stroke-linejoin="round"><path d="M16 3 L23 11 L23 23 L18 28 L14 28 L9 23 L9 11 Z"/><circle cx="16" cy="13" r="2.5" fill="#ffffff"/></g>`),
    grid: wrap(`<g stroke="#2a3a4a" stroke-width="2" fill="#d8a838"><rect x="6" y="6" width="8" height="8" rx="1"/><rect x="18" y="6" width="8" height="8" rx="1"/><rect x="6" y="18" width="8" height="8" rx="1"/><rect x="18" y="18" width="8" height="8" rx="1"/></g>`),
    shield: wrap(`<g stroke="#2a3a4a" stroke-width="2" fill="#3a9aa0" stroke-linejoin="round"><path d="M16 3 L26 7 L26 16 Q26 24, 16 29 Q6 24, 6 16 L6 7 Z"/><path d="M11 16 L14 19 L21 12" stroke="#ffffff" stroke-linecap="round"/></g>`),
  },
};
