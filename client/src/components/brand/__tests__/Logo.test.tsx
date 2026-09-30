import { afterEach, beforeEach, describe, expect, it } from 'vitest';
import { render, screen } from '@testing-library/react';
import { OcLogo, OcMark } from '../Logo';
import { MARK_CORE, MARK_MEMBERS, MARK_PATH, MARK_RING, MARK_VIEWBOX, WORDMARK_PATH } from '../geometry';
import { resetLogoIntroForTests } from '../logoIntro';
import { installWaapiStub } from '../../../test/waapi';

function markPaths(svg: Element): string[] {
  return Array.from(svg.querySelectorAll('path')).map((path) => path.getAttribute('d') ?? '');
}

describe('OcLogo', () => {
  it('reads as one "OpenCompany" label with the wordmark shown', () => {
    const { container } = render(<OcLogo />);
    const [mark, word] = Array.from(container.querySelectorAll('svg'));
    expect(mark).toHaveAttribute('aria-hidden', 'true');
    expect(screen.getAllByRole('img')).toEqual([word]);
    expect(word).toHaveAccessibleName('OpenCompany');
    expect(word.querySelector('path')).toHaveAttribute('d', WORDMARK_PATH);
  });

  it('labels the mark itself when the wordmark is hidden', () => {
    const { container } = render(<OcLogo wordmark={false} />);
    expect(container.querySelectorAll('svg')).toHaveLength(1);
    expect(screen.getByRole('img', { name: 'OpenCompany' })).toBe(container.querySelector('svg'));
  });

  it.each([
    ['sidebar', 24, 23, 99.6, 19.2],
    ['header', 21, 20, 93.4, 18],
    ['settings', 17, 16, 80.9, 15.6],
  ] as const)('%s size draws the mark at %dx%d and the wordmark at %sx%s', (size, width, height, wordW, wordH) => {
    const { container } = render(<OcLogo size={size} />);
    const [mark, word] = Array.from(container.querySelectorAll('svg'));
    expect(mark).toHaveAttribute('width', String(width));
    expect(mark).toHaveAttribute('height', String(height));
    expect(mark).toHaveAttribute('viewBox', MARK_VIEWBOX);
    expect(word).toHaveAttribute('width', String(wordW));
    expect(word).toHaveAttribute('height', String(wordH));
  });

  it('is one colour, the text colour', () => {
    const { container } = render(<OcLogo />);
    expect(container.firstElementChild).toHaveClass('text-fg-default');
    for (const svg of container.querySelectorAll('svg')) expect(svg).toHaveAttribute('fill', 'currentColor');
    expect(container.querySelector('[fill]:not([fill="currentColor"]), [stroke], linearGradient')).toBeNull();
  });

  it('draws the ring, the core, then each member body and head, as the icons do', () => {
    const { container } = render(<OcLogo />);
    const paths = markPaths(container.querySelector('svg')!);
    expect(paths).toEqual([MARK_RING, MARK_CORE, ...MARK_MEMBERS.flatMap((m) => [m.body, m.head])]);
    expect(paths.join('')).toBe(MARK_PATH);
  });

  it('shares the mark with the static OcMark, which is decorative and sized by its box', () => {
    const { container } = render(
      <>
        <OcMark className="w-3/5" />
        <OcLogo wordmark={false} />
      </>,
    );
    const [mark, logo] = Array.from(container.querySelectorAll('svg'));
    expect(mark).toHaveAttribute('aria-hidden', 'true');
    expect(mark).not.toHaveAttribute('width');
    expect(mark).toHaveClass('w-3/5', 'text-fg-default');
    expect(markPaths(mark)).toEqual(markPaths(logo));
  });
});

describe('OcLogo motion', () => {
  let waapi: ReturnType<typeof installWaapiStub>;

  beforeEach(() => {
    waapi = installWaapiStub();
    resetLogoIntroForTests();
  });

  afterEach(() => {
    waapi.restore();
  });

  it('plays the intro once per page load, on the mark and the wordmark', () => {
    const first = render(<OcLogo intro />);
    const [mark, word] = Array.from(first.container.querySelectorAll('svg'));
    const targets = waapi.calls.map((c) => c.target);
    // Ring, three heads, three bodies, wordmark.
    expect(waapi.calls).toHaveLength(8);
    expect(targets).toContain(word);
    expect(targets.every((t) => mark.contains(t) || t === word)).toBe(true);
    const ring = waapi.calls.find((c) => c.target.getAttribute('d') === MARK_RING);
    expect(ring?.keyframes).toEqual([
      { transform: 'scale(0.2) rotate(-180deg)', opacity: 0 },
      { transform: 'none', opacity: 1 },
    ]);
    const heads = waapi.calls.filter((c) => MARK_MEMBERS.some((m) => m.head === c.target.getAttribute('d')));
    expect(heads.map((c) => c.options.delay)).toEqual([760, 860, 960]);

    render(<OcLogo intro />);
    expect(waapi.calls).toHaveLength(8);
  });

  it('does not animate without intro', () => {
    render(<OcLogo />);
    expect(waapi.calls).toHaveLength(0);
  });

  it('pulses the heads and spins the ring when the nonce changes', () => {
    const { rerender } = render(<OcLogo pulseNonce={0} />);
    expect(waapi.calls).toHaveLength(0);
    rerender(<OcLogo pulseNonce={1} />);
    expect(waapi.calls).toHaveLength(4);
    expect(waapi.calls.every((c) => c.options.fill === 'none')).toBe(true);
    rerender(<OcLogo pulseNonce={1} />);
    expect(waapi.calls).toHaveLength(4);
    rerender(<OcLogo pulseNonce={2} />);
    expect(waapi.calls).toHaveLength(8);
  });
});
