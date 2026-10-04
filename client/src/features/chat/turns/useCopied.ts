/**
 * Copy text, and say so for a moment ("Copied", a check) on the button that
 * did it. A clipboard the page may not write to copies nothing and says
 * nothing.
 */

import { useCallback, useEffect, useState } from 'react';

export const COPIED_MS = 1600;

export function useCopied(): [boolean, (text: string) => void] {
  const [copied, setCopied] = useState(false);
  useEffect(() => {
    if (!copied) return;
    const timer = window.setTimeout(() => setCopied(false), COPIED_MS);
    return () => window.clearTimeout(timer);
  }, [copied]);
  const copy = useCallback((text: string) => {
    void navigator.clipboard?.writeText(text).then(
      () => setCopied(true),
      () => undefined,
    );
  }, []);
  return [copied, copy];
}
