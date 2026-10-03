/**
 * RFC 6902 patches on an activity's content (`activity.delta`,
 * docs-internal/chat_protocol.md): `add`, `replace` and `remove`, the three
 * the server writes, applied without touching the input. A path through
 * `__proto__`, `constructor` or `prototype`, or one that leads nowhere,
 * leaves the content as it was; so does any other op. The same rules as
 * server/services/genui/patches.py `apply_patch`.
 */

import { isForbiddenSegment, unescapePointer } from '@/lib/jsonRender/paths';

export interface PatchOp {
  op: string;
  path: string;
  value?: unknown;
}

function isRecord(value: unknown): value is Record<string, unknown> {
  return typeof value === 'object' && value !== null && !Array.isArray(value);
}

function segmentsOf(path: string): string[] | null {
  if (path === '') return [];
  if (!path.startsWith('/')) return null;
  const segments = path.slice(1).split('/').map(unescapePointer);
  return segments.some(isForbiddenSegment) ? null : segments;
}

/** `content` with one operation applied; the same object when it does not apply. */
export function applyPatchOp(content: unknown, op: PatchOp): unknown {
  if (op.op !== 'add' && op.op !== 'replace' && op.op !== 'remove') return content;
  const segments = segmentsOf(op.path);
  if (segments === null) return content;
  if (segments.length === 0) return op.op === 'remove' ? content : op.value;

  let refused = false;
  const write = (node: unknown, index: number): unknown => {
    const key = segments[index];
    const last = index === segments.length - 1;
    if (Array.isArray(node)) {
      const at = key === '-' ? node.length : Number(key);
      if (!Number.isInteger(at) || at < 0 || at > node.length || (key === '-' && op.op !== 'add')) {
        refused = true;
        return node;
      }
      const copy = node.slice();
      if (!last) {
        if (at >= node.length) {
          refused = true;
          return node;
        }
        copy[at] = write(node[at], index + 1);
        return copy;
      }
      if (op.op === 'add') copy.splice(at, 0, op.value);
      else if (at >= node.length) refused = true;
      else if (op.op === 'replace') copy[at] = op.value;
      else copy.splice(at, 1);
      return refused ? node : copy;
    }
    if (!isRecord(node)) {
      refused = true;
      return node;
    }
    const has = Object.prototype.hasOwnProperty.call(node, key);
    if (!last) {
      if (!has) {
        refused = true;
        return node;
      }
      return { ...node, [key]: write(node[key], index + 1) };
    }
    if (op.op === 'remove') {
      if (!has) {
        refused = true;
        return node;
      }
      const copy = { ...node };
      delete copy[key];
      return copy;
    }
    if (op.op === 'replace' && !has) {
      refused = true;
      return node;
    }
    return { ...node, [key]: op.value };
  };
  const next = write(content, 0);
  return refused ? content : next;
}

/** `content` with every operation applied in order, each one that does not
 *  apply skipped. */
export function applyPatch(content: unknown, ops: readonly PatchOp[]): unknown {
  return ops.reduce(applyPatchOp, content);
}
