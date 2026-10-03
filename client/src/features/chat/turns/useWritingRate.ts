/**
 * How fast an answer is streaming ("· 42 tok/s"), measured from when its
 * text first showed in this tab; null when it is not streaming.
 */

import { useEffect, useState } from 'react';
import { writingRate } from './runCopy';

export function useWritingRate(text: string, streaming: boolean, now: Date): string | null {
  const [since, setSince] = useState<{ at: number; characters: number } | null>(null);

  useEffect(() => {
    if (!streaming) setSince(null);
    else if (since === null && text.length > 0) setSince({ at: Date.now(), characters: text.length });
  }, [streaming, text.length, since]);

  if (!streaming || !since) return null;
  return writingRate(text.length - since.characters, now.getTime() - since.at);
}
