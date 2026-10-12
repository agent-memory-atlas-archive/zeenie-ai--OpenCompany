/**
 * Keeps `<html data-page-hidden>` and the `pageActivity` store in step with
 * whether the page can be seen.
 *
 * Why the attribute exists (Wave 33): browsers keep advancing CSS keyframe
 * timing on hidden tabs, and when the tab returns every paused frame of
 * every executing node's three-layer `node-pulse` (plus Cyber's
 * full-viewport flicker/roll) flushes at once. The compositor stalls for
 * 100-200 ms and the first click after returning is swallowed. base.css
 * pauses every CSS animation while the attribute is present. Script-driven
 * motion (lib/motion, the Home orb) reads `pageActivity` for the same signal.
 *
 * "Can be seen" is the Page Visibility API alone. Chrome also reports a
 * minimised or fully covered window as hidden, so a window that is only
 * unfocused (the app beside an editor, or DevTools focused) stays visible
 * and keeps animating. On resume the attribute is removed two frames later,
 * so the first input dispatches before the composite resumes.
 *
 * Moved from Dashboard so it runs on both screens, with two fixes: the
 * resume frames are cancelled on unmount and on every new signal (they
 * leaked and could fire after a later hide), and unmounting clears the
 * attribute (it could stay set and freeze every CSS animation).
 */

import { useEffect } from 'react';
import { pageActivity } from '../lib/pageActivity';

export function usePageActivitySync(): void {
  useEffect(() => {
    const root = document.documentElement;
    let outer = 0;
    let inner = 0;

    const cancelResume = () => {
      if (outer) cancelAnimationFrame(outer);
      if (inner) cancelAnimationFrame(inner);
      outer = 0;
      inner = 0;
    };

    const apply = () => {
      cancelResume();
      if (document.hidden) {
        root.setAttribute('data-page-hidden', '');
        pageActivity.set(false);
        return;
      }
      outer = requestAnimationFrame(() => {
        outer = 0;
        inner = requestAnimationFrame(() => {
          inner = 0;
          if (document.hidden) return;
          root.removeAttribute('data-page-hidden');
          pageActivity.set(true);
        });
      });
    };

    // A page opened in a background tab starts hidden.
    if (document.hidden) apply();
    document.addEventListener('visibilitychange', apply);
    return () => {
      document.removeEventListener('visibilitychange', apply);
      cancelResume();
      root.removeAttribute('data-page-hidden');
      pageActivity.set(true);
    };
  }, []);
}
