/**
 * Citations in an answer (docs-internal/chat_protocol.md, "Sources"): the
 * employee cites a source it was given as `[n]`, n being the source's number
 * in the conversation. A reply shows its sources numbered 1, 2, ... in the
 * order it first cites them, so `[7]` in the text reads as the chip "1".
 *
 * `citationOrder` says which display number each cited source gets;
 * `linkCitations` turns each `[n]` of a known source into a markdown link
 * (`#cite-<n>`) that ReplyMarkdown draws as a chip. Code (inline or fenced)
 * is left as written.
 */

const CITATION = /\[(\d{1,4})\]/g;
export const CITE_HREF_PREFIX = '#cite-';

/** The text outside code: `[segment, isCode]` pairs in order. */
function segments(text: string): [string, boolean][] {
  const out: [string, boolean][] = [];
  const pattern = /(```[\s\S]*?(?:```|$)|`[^`\n]*`)/g;
  let last = 0;
  for (const match of text.matchAll(pattern)) {
    const start = match.index ?? 0;
    if (start > last) out.push([text.slice(last, start), false]);
    out.push([match[0], true]);
    last = start + match[0].length;
  }
  if (last < text.length) out.push([text.slice(last), false]);
  return out;
}

/** Each cited source number (of those in `known`) with its display number,
 *  in the order the text first cites them. */
export function citationOrder(text: string, known: ReadonlySet<number>): Map<number, number> {
  const order = new Map<number, number>();
  for (const [segment, code] of segments(text)) {
    if (code) continue;
    for (const match of segment.matchAll(CITATION)) {
      const n = Number(match[1]);
      if (known.has(n) && !order.has(n)) order.set(n, order.size + 1);
    }
  }
  return order;
}

/** The text with each `[n]` of a known source made a `#cite-n` link. A
 *  `[n]` already followed by `(` (a markdown link) is left alone. */
export function linkCitations(text: string, known: ReadonlySet<number>): string {
  if (known.size === 0) return text;
  return segments(text)
    .map(([segment, code]) =>
      code
        ? segment
        : segment.replace(CITATION, (whole, digits: string, offset: number, source: string) =>
            known.has(Number(digits)) && source[offset + whole.length] !== '(' ? `[${digits}](${CITE_HREF_PREFIX}${digits})` : whole,
          ),
    )
    .join('');
}

/** The source number a `#cite-n` link names, or null. */
export function citedNumber(href: string | undefined): number | null {
  if (!href?.startsWith(CITE_HREF_PREFIX)) return null;
  const n = Number(href.slice(CITE_HREF_PREFIX.length));
  return Number.isInteger(n) && n > 0 ? n : null;
}
