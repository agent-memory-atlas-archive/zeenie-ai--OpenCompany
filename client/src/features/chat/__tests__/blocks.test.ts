/**
 * A streaming answer cut into blocks: at blank lines, never inside fenced
 * code, so each finished block renders once.
 */

import { describe, expect, it } from 'vitest';
import { markdownBlocks } from '../markdown/blocks';

describe('markdownBlocks', () => {
  it('cuts at blank lines', () => {
    expect(markdownBlocks('One.\nStill one.\n\n- a\n- b\n\n\nThree')).toEqual(['One.\nStill one.', '- a\n- b', 'Three']);
  });

  it('keeps a fenced block whole, blank lines and all, and the still-open one too', () => {
    const fenced = 'Run this:\n\n```bash\necho one\n\necho two\n```\n\nDone.';
    expect(markdownBlocks(fenced)).toEqual(['Run this:', '```bash\necho one\n\necho two\n```', 'Done.']);
    expect(markdownBlocks('```py\nprint(1)\n\nprint(2)')).toEqual(['```py\nprint(1)\n\nprint(2)']);
    // A shorter or different fence does not close it.
    expect(markdownBlocks('````\n```\n\nx\n````\n\nafter')).toEqual(['````\n```\n\nx\n````', 'after']);
    expect(markdownBlocks('~~~\na\n\n```\n~~~')).toEqual(['~~~\na\n\n```\n~~~']);
  });

  it('gives nothing for nothing', () => {
    expect(markdownBlocks('')).toEqual([]);
    expect(markdownBlocks('\n\n  \n')).toEqual([]);
  });
});
