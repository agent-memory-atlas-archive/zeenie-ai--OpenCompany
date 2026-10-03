/**
 * An employee's message in the thread, as markdown (lists, links, bold),
 * in the type and colour of the bubble around it. Its own chunk: the talk
 * thread loads it lazily, so the markdown stack stays out of Home's first
 * load. Links open in a new tab, and images from outside the workspace wait
 * for the owner (CHAT_MARKDOWN_COMPONENTS).
 */

import ReactMarkdown from 'react-markdown';
import remarkBreaks from 'remark-breaks';
import remarkGfm from 'remark-gfm';

import { CHAT_MARKDOWN_COMPONENTS } from '@/features/chat';

export default function ThreadMarkdown({ text }: { text: string }) {
  return (
    <div className="chat-markdown min-w-0">
      <ReactMarkdown remarkPlugins={[remarkGfm, remarkBreaks]} components={CHAT_MARKDOWN_COMPONENTS}>
        {text}
      </ReactMarkdown>
    </div>
  );
}
