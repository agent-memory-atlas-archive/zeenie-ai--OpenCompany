/**
 * OpenCompany logo: the Open Council mark and the wordmark.
 *
 * The mark is a C (an open ring) around a core, with three members at it:
 * each a head and a crescent sweeping clockwise along the ring. The wordmark
 * is "OpenCompany" in a serif, drawn as outlines. Both are one colour, the
 * text colour, so they are black on light themes and white on dark ones.
 * The paths live in ./geometry.ts, which the favicon and the desktop icon
 * embed too.
 */

import { useEffect, useLayoutEffect, useRef } from 'react';
import { animate } from '@/lib/motion';
import { cn } from '@/lib/utils';
import {
  MARK_CORE,
  MARK_HEIGHT,
  MARK_MEMBERS,
  MARK_RING,
  MARK_VIEWBOX,
  MARK_WIDTH,
  WORDMARK_HEIGHT_EM,
  WORDMARK_PATH,
  WORDMARK_VIEWBOX,
  WORDMARK_WIDTH_EM,
} from './geometry';
import { claimLogoIntro } from './logoIntro';

/** Mark heights and wordmark em sizes (px). Below 16px use the app icon instead. */
const SIZES = {
  sidebar: { mark: 23, em: 16, gap: 'gap-2.25' },
  header: { mark: 20, em: 15, gap: 'gap-2' },
  settings: { mark: 16, em: 13, gap: 'gap-2' },
} as const;

export type LogoSize = keyof typeof SIZES;

/** Intro and pulse timings (ms). */
const MOTION = {
  ring: 780,
  headPop: 520,
  headDelays: [760, 860, 960],
  bodyFade: 420,
  bodyLag: 80,
  wordmark: 520,
  wordmarkDelay: 300,
  pulseHead: 560,
  pulseStagger: 90,
  pulseRing: 900,
} as const;

/** The C spins about the ring's centre, which sits this far from its box's
 *  top left corner (the box of a C open on the right: outer radius, twice). */
const RING_ORIGIN = 'origin-[258.5px_258.5px] [transform-box:fill-box]';

interface MarkShapesProps {
  ringRef?: (el: SVGPathElement | null) => void;
  headRef?: (i: number) => (el: SVGPathElement | null) => void;
  bodyRef?: (i: number) => (el: SVGPathElement | null) => void;
}

/** The mark's shapes, shared by the logo and the static mark. */
function MarkShapes({ ringRef, headRef, bodyRef }: MarkShapesProps) {
  return (
    <>
      <path ref={ringRef} d={MARK_RING} className={RING_ORIGIN} />
      <path d={MARK_CORE} />
      {MARK_MEMBERS.map((member, i) => (
        <g key={member.head}>
          <path ref={bodyRef?.(i)} d={member.body} />
          <path ref={headRef?.(i)} d={member.head} className="origin-center [transform-box:fill-box]" />
        </g>
      ))}
    </>
  );
}

/** The mark alone, filling the width of its box and never animated: the
 *  orb's stand-in where WebGL or motion is unavailable. Decorative. */
export function OcMark({ className }: { className?: string }) {
  return (
    <svg
      viewBox={MARK_VIEWBOX}
      fill="currentColor"
      className={cn('block h-auto text-fg-default', className)}
      aria-hidden
      focusable="false"
    >
      <MarkShapes />
    </svg>
  );
}

/** The wordmark alone at `em` px, labelled as the product name. */
function Wordmark({ em, svgRef }: { em: number; svgRef?: (el: SVGSVGElement | null) => void }) {
  return (
    <svg
      ref={svgRef}
      viewBox={WORDMARK_VIEWBOX}
      width={round1(em * WORDMARK_WIDTH_EM)}
      height={round1(em * WORDMARK_HEIGHT_EM)}
      fill="currentColor"
      className="shrink-0"
      role="img"
      aria-label="OpenCompany"
      focusable="false"
    >
      <path d={WORDMARK_PATH} />
    </svg>
  );
}

function round1(value: number): number {
  return Math.round(value * 10) / 10;
}

export interface OcLogoProps {
  size?: LogoSize;
  /** Draw the wordmark next to the mark. When hidden, the mark itself is labelled. */
  wordmark?: boolean;
  /** Play the intro animation (once per page load). */
  intro?: boolean;
  /** Change this number to play the pulse (e.g. after a hire). 0 = never. */
  pulseNonce?: number;
  className?: string;
}

export function OcLogo({ size = 'sidebar', wordmark = true, intro = false, pulseNonce = 0, className }: OcLogoProps) {
  const spec = SIZES[size];
  const height = spec.mark;
  const width = Math.round((height * MARK_WIDTH) / MARK_HEIGHT);

  const ringRef = useRef<SVGPathElement | null>(null);
  const heads = useRef<(SVGPathElement | null)[]>([]);
  const bodies = useRef<(SVGPathElement | null)[]>([]);
  const wordRef = useRef<SVGSVGElement | null>(null);

  useLayoutEffect(() => {
    if (!intro || !claimLogoIntro()) return;
    animate(
      ringRef.current,
      [
        { transform: 'scale(0.2) rotate(-180deg)', opacity: 0 },
        { transform: 'none', opacity: 1 },
      ],
      { duration: MOTION.ring, easing: 'overshoot' },
    );
    MOTION.headDelays.forEach((delay, i) => {
      animate(heads.current[i], [{ transform: 'scale(0)' }, { transform: 'scale(1)' }], {
        duration: MOTION.headPop,
        delay,
        easing: 'overshoot',
      });
      animate(bodies.current[i], [{ opacity: 0 }, { opacity: 1 }], {
        duration: MOTION.bodyFade,
        delay: delay + MOTION.bodyLag,
        easing: 'reveal',
      });
    });
    animate(
      wordRef.current,
      [
        { opacity: 0, transform: 'translateX(-6px)' },
        { opacity: 1, transform: 'none' },
      ],
      { duration: MOTION.wordmark, delay: MOTION.wordmarkDelay, easing: 'spring' },
    );
  }, [intro]);

  useEffect(() => {
    if (!pulseNonce) return;
    heads.current.forEach((head, i) => {
      animate(
        head,
        [{ transform: 'scale(1)' }, { transform: 'scale(1.3)', offset: 0.4 }, { transform: 'scale(1)' }],
        { duration: MOTION.pulseHead, delay: MOTION.pulseStagger * i, easing: 'spring', fill: 'none' },
      );
    });
    animate(
      ringRef.current,
      [
        { transform: 'rotate(0deg) scale(1)' },
        { transform: 'rotate(180deg) scale(1.08)', offset: 0.5 },
        { transform: 'rotate(360deg) scale(1)' },
      ],
      { duration: MOTION.pulseRing, easing: 'spring', fill: 'none' },
    );
  }, [pulseNonce]);

  const labelled = !wordmark;

  return (
    <span className={cn('inline-flex shrink-0 items-center text-fg-default', spec.gap, className)}>
      <svg
        viewBox={MARK_VIEWBOX}
        width={width}
        height={height}
        fill="currentColor"
        className="shrink-0 overflow-visible"
        role={labelled ? 'img' : undefined}
        aria-label={labelled ? 'OpenCompany' : undefined}
        aria-hidden={labelled ? undefined : true}
        focusable="false"
      >
        <MarkShapes
          ringRef={(el) => {
            ringRef.current = el;
          }}
          headRef={(i) => (el) => {
            heads.current[i] = el;
          }}
          bodyRef={(i) => (el) => {
            bodies.current[i] = el;
          }}
        />
      </svg>
      {wordmark && (
        <Wordmark
          em={spec.em}
          svgRef={(el) => {
            wordRef.current = el;
          }}
        />
      )}
    </span>
  );
}

export default OcLogo;
