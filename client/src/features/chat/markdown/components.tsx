/**
 * Element overrides for markdown in chat replies, shared by Home's thread and
 * Dev's chat pane: links open in a new tab, so following one never leaves the
 * conversation, and images go through SafeImage.
 */

import type { Components } from 'react-markdown';

import { SafeImage } from './SafeImage';

export const CHAT_MARKDOWN_COMPONENTS: Components = {
  a: ({ node: _node, ...props }) => <a {...props} target="_blank" rel="noopener noreferrer" />,
  img: SafeImage,
};
