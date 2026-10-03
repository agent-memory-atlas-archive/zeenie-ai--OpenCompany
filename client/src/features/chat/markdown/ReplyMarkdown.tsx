/**
 * An answer's text as markdown: paragraphs, lists, quotes, tables, and code
 * blocks in the theme's syntax colours (`.chat-markdown .token.*`). Its own
 * chunk: the thread loads it lazily, so the markdown stack stays out of the
 * first load. Links open in a new tab, and images from outside the workspace
 * wait for the owner (CHAT_MARKDOWN_COMPONENTS).
 *
 * While the answer streams (`streaming`), it is rendered block by block
 * (markdown/blocks.ts): a finished block renders once, only the growing last
 * one again with each delta, and a caret follows the text
 * (`.chat-markdown[data-streaming]` in index.css).
 */

import Prism from 'prismjs';
import 'prismjs/components/prism-bash';
import 'prismjs/components/prism-json';
import 'prismjs/components/prism-markdown';
import 'prismjs/components/prism-python';
import 'prismjs/components/prism-typescript';
import 'prismjs/components/prism-yaml';
import { memo, useMemo } from 'react';
import ReactMarkdown, { type Components } from 'react-markdown';
import remarkBreaks from 'remark-breaks';
import remarkGfm from 'remark-gfm';
import { cn } from '@/lib/utils';
import { markdownBlocks } from './blocks';
import { CHAT_MARKDOWN_COMPONENTS } from './components';

const REMARK_PLUGINS = [remarkGfm, remarkBreaks];

/** The names a fence uses for a language Prism knows by another. */
const LANGUAGE_ALIASES: Record<string, string> = {
  js: 'javascript',
  jsx: 'javascript',
  ts: 'typescript',
  tsx: 'typescript',
  py: 'python',
  sh: 'bash',
  shell: 'bash',
  zsh: 'bash',
  yml: 'yaml',
  md: 'markdown',
  html: 'markup',
  xml: 'markup',
  svg: 'markup',
};

/** A fenced block in a language Prism knows gets its colours; inline code
 *  (which never has a language class) and anything else stays as written.
 *  Prism escapes the text it tokenizes, so its markup is safe to insert. */
const CodeBlock: Components['code'] = ({ node: _node, className, children, ...props }) => {
  const fence = /(?:^|\s)language-([\w-]+)/.exec(className ?? '')?.[1]?.toLowerCase();
  const language = fence ? (LANGUAGE_ALIASES[fence] ?? fence) : undefined;
  const grammar = language ? Prism.languages[language] : undefined;
  if (!language || !grammar || typeof children !== 'string') {
    return (
      <code className={className} {...props}>
        {children}
      </code>
    );
  }
  const html = Prism.highlight(children.replace(/\n$/, ''), grammar, language);
  return <code className={className} {...props} dangerouslySetInnerHTML={{ __html: html }} />;
};

const COMPONENTS: Components = { ...CHAT_MARKDOWN_COMPONENTS, code: CodeBlock };

const Block = memo(function Block({ text }: { text: string }) {
  return (
    <ReactMarkdown remarkPlugins={REMARK_PLUGINS} components={COMPONENTS}>
      {text}
    </ReactMarkdown>
  );
});

export default function ReplyMarkdown({ text, streaming = false, className }: { text: string; streaming?: boolean; className?: string }) {
  const blocks = useMemo(() => (streaming ? markdownBlocks(text) : [text]), [text, streaming]);
  return (
    <div className={cn('chat-markdown min-w-0', className)} data-streaming={streaming || undefined}>
      {blocks.map((block, index) => (
        <Block key={index} text={block} />
      ))}
    </div>
  );
}
