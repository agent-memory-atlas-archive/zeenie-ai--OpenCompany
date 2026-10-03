/**
 * What a reply shows besides its text (docs-internal/chat_protocol.md,
 * "Messages"): its parts. A saved reply carries them in `parts`; a run still
 * going builds them through its `activity.*` events. Read forgivingly: a
 * part this client cannot read is left out.
 */

import type { RunActivity } from '@/lib/agui/reduceRun';

/** A generated UI, saved or arriving. */
export interface UiPart {
  partId: string;
  /** json-render's flat spec, as the server checked it. */
  spec: unknown;
  /** What the owner last set; null when it is the spec's own. */
  state: Record<string, unknown> | null;
  stateRevision: number;
  /** Patches folded in so far, for a UI arriving now. */
  patches: number;
}

function isRecord(value: unknown): value is Record<string, unknown> {
  return typeof value === 'object' && value !== null && !Array.isArray(value);
}

/** The generated UIs of a saved reply's `parts`. */
export function savedUiParts(parts: Record<string, unknown> | null | undefined): UiPart[] {
  const raw = parts?.ui;
  if (!Array.isArray(raw)) return [];
  return raw.flatMap((item) => {
    if (!isRecord(item) || typeof item.part_id !== 'string' || !item.part_id || !isRecord(item.spec)) return [];
    return [
      {
        partId: item.part_id,
        spec: item.spec,
        state: isRecord(item.state) ? item.state : null,
        stateRevision: typeof item.state_revision === 'number' ? item.state_revision : 0,
        patches: 0,
      },
    ];
  });
}

/** A source a tool gave the employee, numbered in the conversation. */
export interface SourceItem {
  n: number;
  title: string;
  url: string | null;
  detail: string | null;
}

/** The sources a saved reply drew on (`parts.sources`), one per number. */
export function savedSources(parts: Record<string, unknown> | null | undefined): SourceItem[] {
  const raw = parts?.sources;
  if (!Array.isArray(raw)) return [];
  const seen = new Set<number>();
  return raw.flatMap((item) => {
    if (!isRecord(item) || typeof item.n !== 'number' || !Number.isInteger(item.n) || item.n < 1 || seen.has(item.n)) return [];
    seen.add(item.n);
    const url = typeof item.url === 'string' && /^https?:\/\//i.test(item.url) ? item.url : null;
    const title = typeof item.title === 'string' && item.title.trim() ? item.title.trim() : (url ?? `Source ${item.n}`);
    const detail = typeof item.detail === 'string' && item.detail.trim() ? item.detail.trim() : null;
    return [{ n: item.n, title, url, detail }];
  });
}

/** What the employee suggested the owner ask next: at most three short
 *  questions, blanks and repeats left out. */
export function savedFollowups(parts: Record<string, unknown> | null | undefined): string[] {
  const raw = parts?.followups;
  if (!Array.isArray(raw)) return [];
  const items = raw.flatMap((item) => (typeof item === 'string' && item.trim() ? [item.trim().slice(0, 160)] : []));
  return [...new Set(items)].slice(0, 3);
}

/** The generated UIs a run is streaming. */
export function liveUiParts(activities: readonly RunActivity[] | undefined): UiPart[] {
  return (activities ?? []).flatMap((activity) =>
    activity.activityType === 'json_render' && isRecord(activity.content)
      ? [{ partId: activity.messageId, spec: activity.content, state: null, stateRevision: 0, patches: activity.patches }]
      : [],
  );
}
