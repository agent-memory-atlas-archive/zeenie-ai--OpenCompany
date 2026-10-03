/**
 * Markdown in chat replies: images from outside the workspace wait for the
 * owner (a reply could otherwise leak data through an image URL the moment
 * it renders), workspace images load, and links open in a new tab.
 */

import { describe, expect, it } from 'vitest';
import { render, screen } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import ReactMarkdown from 'react-markdown';

import { CHAT_MARKDOWN_COMPONENTS, isWorkspaceImage } from '..';

function renderReply(text: string) {
  return render(<ReactMarkdown components={CHAT_MARKDOWN_COMPONENTS}>{text}</ReactMarkdown>);
}

describe('isWorkspaceImage', () => {
  const origin = window.location.origin;

  it.each([
    ['/api/workspace/wf-1/files/shot.png', true],
    [`${origin}/api/workspace/wf-1/files/shot.png`, true],
    ['https://elsewhere.example/api/workspace/wf-1/files/shot.png', false],
    ['https://tracker.example/pixel.png?d=secret', false],
    ['/api/other/thing.png', false],
    ['', false],
    [undefined, false],
  ])('%j -> %s', (src, expected) => {
    expect(isWorkspaceImage(src)).toBe(expected);
  });
});

describe('chat markdown', () => {
  it('loads a workspace image straight away', () => {
    renderReply('![chart](/api/workspace/wf-1/files/chart.png)');
    const img = screen.getByRole('img', { name: 'chart' });
    expect(img).toHaveAttribute('src', '/api/workspace/wf-1/files/chart.png');
    expect(img).toHaveAttribute('referrerpolicy', 'no-referrer');
  });

  it('holds a remote image until the owner asks for it', async () => {
    const user = userEvent.setup();
    renderReply('![x](https://tracker.example/pixel.png?d=secret)');

    expect(screen.queryByRole('img')).toBeNull();
    const reveal = screen.getByRole('button', { name: 'Show image from tracker.example' });

    await user.click(reveal);

    const img = screen.getByRole('img', { name: 'x' });
    expect(img).toHaveAttribute('src', 'https://tracker.example/pixel.png?d=secret');
    expect(img).toHaveAttribute('referrerpolicy', 'no-referrer');
  });

  it('opens links in a new tab without an opener', () => {
    renderReply('[docs](https://example.com/docs)');
    const link = screen.getByRole('link', { name: 'docs' });
    expect(link).toHaveAttribute('target', '_blank');
    expect(link).toHaveAttribute('rel', 'noopener noreferrer');
  });
});
