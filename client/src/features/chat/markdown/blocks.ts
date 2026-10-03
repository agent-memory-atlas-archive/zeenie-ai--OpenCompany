/**
 * A streaming answer cut into its blocks: at blank lines, never inside a
 * fenced code block. While the answer streams, ReplyMarkdown renders each
 * finished block once and re-renders only the last, which is still growing.
 */

const FENCE_OPEN = /^ {0,3}(`{3,}|~{3,})/;
const FENCE_CLOSE = /^ {0,3}(`{3,}|~{3,})\s*$/;

export function markdownBlocks(text: string): string[] {
  const blocks: string[] = [];
  let current: string[] = [];
  let fence: string | null = null;
  for (const line of text.split('\n')) {
    if (fence !== null) {
      current.push(line);
      const close = FENCE_CLOSE.exec(line)?.[1];
      if (close && close[0] === fence[0] && close.length >= fence.length) fence = null;
      continue;
    }
    const open = FENCE_OPEN.exec(line)?.[1];
    if (open) {
      fence = open;
      current.push(line);
      continue;
    }
    if (line.trim() === '') {
      if (current.length > 0) blocks.push(current.join('\n'));
      current = [];
      continue;
    }
    current.push(line);
  }
  if (current.length > 0) blocks.push(current.join('\n'));
  return blocks;
}
