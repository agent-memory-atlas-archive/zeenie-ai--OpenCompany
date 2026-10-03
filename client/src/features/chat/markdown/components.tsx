/**
 * Element overrides for markdown in chat replies, shared by Home's thread and
 * Dev's chat pane: links open in a new tab, so following one never leaves the
 * conversation; a citation link (`#cite-<n>`, markdown/citations.ts) is a
 * citation chip; and images go through SafeImage.
 */

import type { Components } from 'react-markdown';

import { CitationChip } from './CitationChip';
import { citedNumber } from './citations';
import { SafeImage } from './SafeImage';

export const CHAT_MARKDOWN_COMPONENTS: Components = {
  a: ({ node: _node, ...props }) => {
    const cited = citedNumber(props.href);
    if (cited !== null) return <CitationChip n={cited} />;
    return <a {...props} target="_blank" rel="noopener noreferrer" />;
  },
  img: SafeImage,
};
