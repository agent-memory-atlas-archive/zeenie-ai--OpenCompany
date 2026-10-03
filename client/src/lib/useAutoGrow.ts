/**
 * Grow a textarea with its text up to its CSS max-height, then scroll.
 * Runs on every value change, so text set from outside (a template, a
 * suggestion) sizes the box too. Shared by the hire composer and the chat
 * composer.
 */

import { useLayoutEffect, type RefObject } from 'react';

export function useAutoGrow(ref: RefObject<HTMLTextAreaElement | null>, value: string): void {
  useLayoutEffect(() => {
    const box = ref.current;
    if (!box) return;
    box.style.height = 'auto';
    box.style.height = `${box.scrollHeight}px`;
    box.style.overflowY = box.scrollHeight > box.clientHeight ? 'auto' : 'hidden';
  }, [ref, value]);
}
