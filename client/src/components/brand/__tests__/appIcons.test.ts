/**
 * The app icons draw the logo's mark: the favicon (black, white under a dark
 * colour scheme) and the desktop icon (white on a dark tile, rendered to
 * build/icon.png by desktop/scripts/gen-icons.ts). So do the icon-only logo
 * files in media/brand/. All of them embed MARK_PATH from ../geometry.ts
 * verbatim; after changing the mark, paste the new path into each file and
 * render media/brand's PNGs again.
 */
import { readFileSync } from 'node:fs';
import { join } from 'node:path';
import { describe, expect, it } from 'vitest';
import { MARK_PATH, MARK_VIEWBOX } from '../geometry';

const CLIENT_DIR = join(__dirname, '..', '..', '..', '..');
const FAVICON = join(CLIENT_DIR, 'public', 'opencompany-icon.svg');
const DESKTOP_ICON = join(CLIENT_DIR, '..', 'desktop', 'build', 'icon.svg');
const BRAND_DIR = join(CLIENT_DIR, '..', 'media', 'brand');

describe('app icons', () => {
  it('the favicon draws the mark, black and white under a dark scheme', () => {
    const svg = readFileSync(FAVICON, 'utf-8');
    expect(svg).toContain(`d="${MARK_PATH}"`);
    expect(svg).toContain('path{fill:#000}@media (prefers-color-scheme:dark){path{fill:#fff}}');
  });

  it('the desktop icon draws the mark', () => {
    expect(readFileSync(DESKTOP_ICON, 'utf-8')).toContain(`d="${MARK_PATH}"`);
  });

  it.each([
    ['opencompany-mark.svg', '#000'],
    ['opencompany-mark-white.svg', '#fff'],
  ])('the icon-only logo %s draws the mark in %s, cropped to its box', (file, fill) => {
    const svg = readFileSync(join(BRAND_DIR, file), 'utf-8');
    expect(svg).toContain(`viewBox="${MARK_VIEWBOX}"`);
    expect(svg).toContain(`<path fill="${fill}" d="${MARK_PATH}"/>`);
  });

  it('the favicon is the page icon', () => {
    const html = readFileSync(join(CLIENT_DIR, 'index.html'), 'utf-8');
    expect(html).toContain('<link rel="icon" type="image/svg+xml" href="/opencompany-icon.svg" />');
  });
});
