/**
 * useAppTheme — canvas + map theme accessor.
 *
 * Returns a flat colour pack with the same shape the canvas + map +
 * grandfathered-modal call sites expect (`theme.colors.X`,
 * `theme.isDarkMode`). Under light/dark this is identity-pass; under
 * Atomic the base pack (light or dark, picked by `DARK_FAMILY`) gets a
 * small overlay of the theme's accents (primary, action colours) so
 * canvas selection rings and action button colours match the theme's
 * identity. Canvas EDGE strokes are
 * no longer part of this pack — the design-handoff edge migration
 * moved them to CSS tokens (--edge-stroke + semantic status recolors
 * in styles/canvasAnimations.ts); do not reintroduce edge* keys here.
 *
 * This is the production realisation of MIGRATION_PLAYBOOK Wave 1 —
 * the upstream playbook proposes a full per-theme NodePack record;
 * the overlay form here is the same idea expressed without forking
 * the existing 50-key Colors shape, so the existing 14+ call sites
 * keep compiling and the visual delta lives in one place.
 *
 * Adding a new theme override: drop an entry in `THEME_OVERRIDES`
 * with whichever subset of `Colors` keys you want to override. Empty
 * overrides are a no-op (theme falls back to pure light/dark).
 */

import { useMemo } from 'react';
import { useTheme, type ThemeName } from '../contexts/ThemeContext';
import { theme as baseTheme, lightColors, darkColors } from '../styles/theme';

type Colors = typeof lightColors;
/** Loose form for override entries — `lightColors` is `as const`, which
 *  narrows each value to a literal hex string. Themes need to substitute
 *  arbitrary hex / rgba strings, so the override map widens to `string`. */
type ColorOverride = Partial<Record<keyof Colors, string>>;

/** Theme-specific overlays. Each entry is a partial Colors object —
 *  whatever keys appear here override the chosen base pack. Missing
 *  keys fall through to lightColors / darkColors. Hex values come
 *  from the matching client/src/themes/<theme>.css token block. */
const THEME_OVERRIDES: Partial<Record<ThemeName, ColorOverride>> = {
  atomic: {
    primary: '#e85a26',           // atomic orange
    focus: '#e85a26',
    focusRing: 'rgba(232, 90, 38, 0.4)',
    actionRun: '#5a8a5a',
    actionDeploy: '#e85a26',
    actionStop: '#e85a26',
    actionSave: '#3a9aa0',         // turquoise
  },
};

export const useAppTheme = () => {
  const { theme, isDarkMode } = useTheme();

  return useMemo(() => {
    const base = isDarkMode ? darkColors : lightColors;
    const overrides = THEME_OVERRIDES[theme] ?? {};
    return {
      ...baseTheme,
      colors: { ...base, ...overrides } as Colors,
      isDarkMode,
    };
  }, [theme, isDarkMode]);
};
