/**
 * tokens.js — THE single source of truth for every design value.
 *
 * tokens.css is generated from this file (node tzone-theme/build.js). Nothing
 * else in the package may declare :root, and no component may contain a literal
 * colour, size, font or radius.
 *
 * All custom properties are namespaced --tz-* so the package can drop into an
 * existing project without colliding.
 */

/* ---------- ramp generation ---------- */

const WHITE = [255, 255, 255];
const DARK = [6, 19, 27];
const LIGHT_STEPS = { 100: 0.9, 200: 0.76, 300: 0.55, 400: 0.28 };
const DEEP_STEPS = { 600: 0.22, 700: 0.42, 800: 0.6, 900: 0.75 };

const toPair = (n) => n.toString(16).padStart(2, '0');
const parseHex = (h) => [
  parseInt(h.slice(1, 3), 16),
  parseInt(h.slice(3, 5), 16),
  parseInt(h.slice(5, 7), 16),
];
const blend = (a, b, t) => '#' + a.map((v, i) => toPair(Math.round(v + (b[i] - v) * t))).join('');

/** Build a 100..900 ramp from one base hex. 500 IS the base. */
export function ramp(baseHex) {
  const base = parseHex(baseHex);
  const out = {};
  for (const step of Object.keys(LIGHT_STEPS)) out[step] = blend(base, WHITE, LIGHT_STEPS[step]);
  out[500] = baseHex;
  for (const step of Object.keys(DEEP_STEPS)) out[step] = blend(base, DARK, DEEP_STEPS[step]);
  return out;
}

/* ---------- brand ---------- */

export const ACCENT_BASE = '#1b9be0';
export const ACCENT_2_BASE = '#3fb552';

export const ACCENT = ramp(ACCENT_BASE);
export const ACCENT_2 = ramp(ACCENT_2_BASE);

/* Frozen copies of the generated ramps, for reference and tests. */
export const ACCENT_EXPECTED = { 100: '#e8f5fc', 200: '#c8e7f8', 300: '#98d2f1', 400: '#5bb7e9', 500: '#1b9be0', 600: '#167db5', 700: '#12628d', 800: '#0e496a', 900: '#0b354c' };
export const ACCENT_2_EXPECTED = { 100: '#ecf8ee', 200: '#d1edd5', 300: '#a9deb1', 400: '#75ca82', 500: '#3fb552', 600: '#329146', 700: '#27713b', 800: '#1d5431', 900: '#143c29' };

/* ---------- ground ---------- */

export const COLORS = {
  bg: '#f4f5f7',
  surface: '#e9ebef',
  text: '#18202a',
  divider: 'color-mix(in srgb, #18202a 15%, transparent)',
  rail: '#111922',
  railText: '#f3f5f7',
  accent: ACCENT_BASE,
  accent2: ACCENT_2_BASE,
};

export const NEUTRAL = {
  100: '#f6f7f9', 200: '#e8eaee', 300: '#d5d9df', 400: '#b8bec8',
  500: '#99a1ad', 600: '#7b8492', 700: '#5f6875', 800: '#434b56',
  900: '#2b323b',
};

export const CHANNELS = {
  whatsapp: '#3fb552',
  messenger: '#1b9be0',
  instagram: '#b06ab3',
  telegram: '#2ca7e6',
  website: '#7b8492',
};

/* ---------- type ---------- */

export const FONTS = {
  heading: '"Cormorant Garamond", "Noto Naskh Arabic", system-ui, serif',
  body: '"Lora", "Noto Naskh Arabic", system-ui, serif',
  headingWeight: '600',
};

export const GOOGLE_FONTS_HREF =
  'https://fonts.googleapis.com/css2' +
  '?family=Cormorant+Garamond:ital,wght@0,300..700;1,300..700' +
  '&family=Lora:ital,wght@0,400..700;1,400..700' +
  '&family=Noto+Naskh+Arabic:wght@400..700' +
  '&display=swap';

/* ---------- geometry ---------- */

export const SPACE = { 1: '4.6px', 2: '9.2px', 3: '13.8px', 4: '18.4px', 6: '27.6px', 8: '36.8px' };
export const RADIUS = { sm: '2px', md: '4px', lg: '7px' };
export const SHADOW = {
  sm: '0 1px 2px color-mix(in srgb, #2d2b2b 14%, transparent)',
  md: '0 3px 10px color-mix(in srgb, #2d2b2b 16%, transparent)',
  lg: '0 12px 32px color-mix(in srgb, #2d2b2b 22%, transparent)',
};

/* ---------- runtime knobs ---------- */

export const RUNTIME_DEFAULTS = {
  'tz-base-size': '15px',
  'tz-line-height': '1.55',
  'tz-letter-spacing': '0',
  'tz-heading-scale': '1',
  'tz-kick-case': 'uppercase',
  'tz-tnum': '"tnum"',
  'tz-btn-size': '14px',
  'tz-line': '1px',
  'tz-btn-bg': 'transparent',
  'tz-btn-fg': 'var(--tz-color-accent)',
  'tz-card-bg': 'transparent',
  'tz-input-border': '1px',
  'tz-input-radius': '4px',
  'tz-focus': '2px',
  'tz-speed': '140ms',
  'tz-avatar': '50%',
  'tz-rail-width': '222px',
  'tz-topbar': '54px',
  'tz-drawer-width': '320px',
};

/* ---------- helpers ---------- */

/** cssVar('color-accent') -> 'var(--tz-color-accent)' */
export const cssVar = (name) => 'var(--tz-' + name + ')';

/** The full :root block. tokens.css is this string, written to disk. */
export function rootVars() {
  const lines = [];
  const push = (name, value) => lines.push('  --tz-' + name + ': ' + value + ';');

  push('color-bg', COLORS.bg);
  push('color-surface', COLORS.surface);
  push('color-text', COLORS.text);
  push('color-divider', COLORS.divider);
  push('color-rail', COLORS.rail);
  push('color-rail-text', COLORS.railText);
  push('color-accent', COLORS.accent);
  push('color-accent-2', COLORS.accent2);

  for (const step of Object.keys(ACCENT)) push('color-accent-' + step, ACCENT[step]);
  for (const step of Object.keys(ACCENT_2)) push('color-accent-2-' + step, ACCENT_2[step]);
  for (const step of Object.keys(NEUTRAL)) push('color-neutral-' + step, NEUTRAL[step]);
  for (const key of Object.keys(CHANNELS)) push('ch-' + key, CHANNELS[key]);

  push('font-heading', FONTS.heading);
  push('font-body', FONTS.body);
  push('font-heading-weight', FONTS.headingWeight);

  for (const step of Object.keys(SPACE)) push('space-' + step, SPACE[step]);
  for (const key of Object.keys(RADIUS)) push('radius-' + key, RADIUS[key]);
  for (const key of Object.keys(SHADOW)) push('shadow-' + key, SHADOW[key]);
  for (const key of Object.keys(RUNTIME_DEFAULTS)) {
    lines.push('  --' + key + ': ' + RUNTIME_DEFAULTS[key] + ';');
  }

  return ':root {\n' + lines.join('\n') + '\n}\n';
}

/** Per-tenant overrides as an inline style string. */
export function runtimeVars(overrides) {
  const merged = Object.assign({}, RUNTIME_DEFAULTS, overrides || {});
  return Object.keys(merged).map((k) => '--' + k + ':' + merged[k]).join(';');
}

/** Per-tenant overrides as a React style object. */
export function runtimeStyle(overrides) {
  const merged = Object.assign({}, RUNTIME_DEFAULTS, overrides || {});
  const out = {};
  for (const k of Object.keys(merged)) out['--' + k] = merged[k];
  return out;
}
