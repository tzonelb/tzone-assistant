/**
 * css.js — COMPATIBILITY LAYER ONLY.
 *
 * The primary way to load the theme is to import the real stylesheets, because
 * this package is consumed by Vite:
 *
 *     import './tzone-theme/tokens.css';
 *     import './tzone-theme/theme.css';
 *
 * This module exists for the two cases where that is not possible: a page with
 * no bundler, and server-side rendering that must inline the CSS. It reads the
 * SAME .css files (Vite's ?inline query), so there is no second copy of the
 * stylesheet and no second :root declaration anywhere in the package.
 */

import tokensCss from './tokens.css?inline';
import themeCss from './theme.css?inline';
import { GOOGLE_FONTS_HREF } from './tokens.js';

export const TOKENS_CSS = tokensCss;
export const THEME_CSS = themeCss;

/** Both layers in the required order. */
export function stylesheet() {
  return TOKENS_CSS + '\n' + THEME_CSS;
}

/** Inject the font link once. Safe to call repeatedly. */
export function injectFonts(doc) {
  const d = doc || document;
  if (d.getElementById('tz-fonts')) return;
  const link = d.createElement('link');
  link.id = 'tz-fonts';
  link.rel = 'stylesheet';
  link.href = GOOGLE_FONTS_HREF;
  d.head.appendChild(link);
}

/**
 * Legacy runtime injection. Prefer importing the .css files.
 * @param {Document} [doc]
 */
export function injectStyles(doc) {
  const d = doc || document;
  injectFonts(d);
  if (d.getElementById('tz-theme')) return;
  const style = d.createElement('style');
  style.id = 'tz-theme';
  style.textContent = stylesheet();
  d.head.appendChild(style);
}
