/**
 * An employee's message in the thread, as markdown (lists, links, bold),
 * in the type and colour of the bubble around it. Its own chunk: the talk
 * thread loads it lazily, so the markdown stack stays out of Home's first
 * load. Links open in a new tab, so following one never leaves the
 * conversation.
 */

import ReactMarkdown, { type Components } from 'react-markdown';
import remarkBreaks from 'remark-breaks';
import remarkGfm from 'remark-gfm';

const COMPONENTS: Components = {
  a: ({ node: _node, ...props }) => <a {...props} target="_blank" rel="noopener noreferrer" />,
};

export default function ThreadMarkdown({ text }: { text: string }) {
  return (
    <div className="chat-markdown min-w-0">
      <ReactMarkdown remarkPlugins={[remarkGfm, remarkBreaks]} components={COMPONENTS}>
        {text}
      </ReactMarkdown>
    </div>
  );
}
