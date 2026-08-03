/**
 * ui.js — HTML-string builders, for server rendering and non-React hosts.
 *
 * Rules enforced here:
 *   - No literal text, number or data inside a builder. Everything is a prop.
 *   - Every class is tz- prefixed.
 *   - No colour, size or font literal — the classes read tokens.
 *
 * For React, use react.jsx instead.
 */

const esc = (value) =>
  String(value == null ? '' : value)
    .replace(/&/g, '&amp;')
    .replace(/</g, '&lt;')
    .replace(/>/g, '&gt;')
    .replace(/"/g, '&quot;');

function attrs(extra) {
  if (!extra) return '';
  let out = '';
  for (const key of Object.keys(extra)) {
    const value = extra[key];
    if (value === true) out += ' ' + key;
    else if (value !== null && value !== undefined && value !== false) {
      out += ' ' + key + '="' + esc(value) + '"';
    }
  }
  return out;
}

function cx(...parts) {
  return parts.filter(Boolean).join(' ');
}

/* ---------- buttons ---------- */

/**
 * @param {{label:string, variant?:'primary'|'secondary'|'ghost', block?:boolean,
 *          tag?:string, disabled?:boolean, className?:string, attrs?:object}} props
 */
export function button(props = {}) {
  const tag = props.tag || 'button';
  const cls = cx('tz-btn', 'tz-btn-' + (props.variant || 'secondary'),
    props.block && 'tz-btn-block', props.className);
  const extra = Object.assign({}, props.attrs, props.disabled ? { disabled: true } : null);
  return '<' + tag + ' class="' + cls + '"' + attrs(extra) + '>' + esc(props.label) + '</' + tag + '>';
}

/** @param {{icon:string, label:string, variant?:string, attrs?:object}} props */
export function iconButton(props = {}) {
  const cls = cx('tz-btn', 'tz-btn-' + (props.variant || 'secondary'), 'tz-btn-icon');
  return '<button class="' + cls + '" aria-label="' + esc(props.label) + '"' +
    attrs(props.attrs) + '>' + (props.icon || '') + '</button>';
}

/* ---------- tags & chips ---------- */

/** @param {{label:string, tone?:'neutral'|'outline'|'accent'|'accent-2'}} props */
export function tag(props = {}) {
  return '<span class="' + cx('tz-tag', 'tz-tag-' + (props.tone || 'neutral')) + '">' +
    esc(props.label) + '</span>';
}

/** @param {{label:string, active?:boolean, attrs?:object}} props */
export function chip(props = {}) {
  return '<button class="' + cx('tz-chip', props.active && 'tz-chip-on') + '"' +
    attrs(props.attrs) + '>' + esc(props.label) + '</button>';
}

/* ---------- type ---------- */

/** @param {{text:string, className?:string}} props */
export function kicker(props = {}) {
  return '<span class="' + cx('tz-kick', props.className) + '">' + esc(props.text) + '</span>';
}

/** @param {{value:string|number}} props */
export function figure(props = {}) {
  return '<span class="tz-fig tz-num">' + esc(props.value) + '</span>';
}

/** @param {{value:string|number}} props */
export function num(props = {}) {
  return '<span class="tz-num">' + esc(props.value) + '</span>';
}

export function divider() {
  return '<hr class="tz-hr">';
}

/* ---------- card ---------- */

/**
 * @param {{kicker?:string, title?:string, body?:string, meta?:string,
 *          children?:string}} props
 */
export function card(props = {}) {
  let inner = '';
  if (props.kicker) inner += '<span class="tz-card-kicker">' + esc(props.kicker) + '</span>';
  if (props.title) inner += '<div class="tz-card-title">' + esc(props.title) + '</div>';
  if (props.body) inner += '<p class="tz-card-body">' + esc(props.body) + '</p>';
  if (props.children) inner += props.children;
  if (props.meta) inner += '<div class="tz-card-meta">' + esc(props.meta) + '</div>';
  return '<div class="tz-card">' + inner + '</div>';
}

/* ---------- forms ---------- */

/**
 * @param {{label:string, name:string, value?:string, type?:string,
 *          placeholder?:string, dir?:string}} props
 */
export function field(props = {}) {
  return '<label class="tz-field"><span>' + esc(props.label) + '</span>' +
    '<input class="tz-input" type="' + esc(props.type || 'text') + '"' +
    ' name="' + esc(props.name) + '" value="' + esc(props.value) + '"' +
    ' placeholder="' + esc(props.placeholder) + '"' +
    ' dir="' + esc(props.dir || 'auto') + '"></label>';
}

/** @param {{options:string[], value?:string, name?:string}} props */
export function segmented(props = {}) {
  const opts = (props.options || []).map((option) =>
    '<span class="' + cx('tz-seg-opt', option === props.value && 'tz-seg-opt-on') + '"' +
    ' data-value="' + esc(option) + '">' + esc(option) + '</span>').join('');
  return '<div class="tz-seg"' + attrs(props.name ? { 'data-name': props.name } : null) + '>' +
    opts + '</div>';
}

/* ---------- avatar ---------- */

/**
 * @param {{initial?:string, src?:string, alt?:string,
 *          size?:'sm'|'md'|'lg', accent?:boolean}} props
 */
export function avatar(props = {}) {
  const cls = cx('tz-avatar', 'tz-avatar-' + (props.size || 'md'), props.accent && 'tz-avatar-accent');
  const inner = props.src
    ? '<img src="' + esc(props.src) + '" alt="' + esc(props.alt) + '">'
    : esc(props.initial);
  return '<span class="' + cls + '">' + inner + '</span>';
}

/* ---------- stats ---------- */

/** @param {{label:string, value:string|number, note?:string}} props */
export function stat(props = {}) {
  return '<div class="tz-stat">' +
    kicker({ text: props.label }) +
    '<div class="tz-fig tz-num">' + esc(props.value) + '</div>' +
    (props.note ? '<div class="tz-text-muted">' + esc(props.note) + '</div>' : '') +
    '</div>';
}

/** @param {{cells:Array<{label:string,value:string|number,note?:string}>}} props */
export function statGrid(props = {}) {
  return '<div class="tz-stat-grid">' + (props.cells || []).map(stat).join('') + '</div>';
}

/* ---------- table ---------- */

/** @param {{columns:string[], rows:Array<Array<string|number>>}} props */
export function table(props = {}) {
  const head = (props.columns || []).map((column) => '<th>' + esc(column) + '</th>').join('');
  const body = (props.rows || []).map((row) =>
    '<tr>' + row.map((cell) => '<td>' + esc(cell) + '</td>').join('') + '</tr>').join('');
  return '<div class="tz-tablewrap"><table class="tz-table">' +
    '<thead><tr>' + head + '</tr></thead><tbody>' + body + '</tbody></table></div>';
}

/* ---------- rail ---------- */

/**
 * @param {{label:string, href?:string, active?:boolean, icon?:string,
 *          badge?:string|number}} props
 */
export function railLink(props = {}) {
  return '<a class="tz-rail-link" href="' + esc(props.href || '#') + '">' +
    (props.active ? '<span class="tz-mark"></span>' : '') +
    (props.icon || '') +
    '<span>' + esc(props.label) + '</span>' +
    (props.badge ? '<span class="tz-num">' + esc(props.badge) + '</span>' : '') +
    '</a>';
}

/* ---------- empty state ---------- */

/**
 * @param {{icon?:string, title:string, description?:string,
 *          actionLabel?:string, actionAttrs?:object}} props
 */
export function emptyState(props = {}) {
  return '<div class="tz-empty">' +
    (props.icon ? '<span class="tz-empty-icon">' + props.icon + '</span>' : '') +
    '<h3 class="tz-empty-title">' + esc(props.title) + '</h3>' +
    (props.description ? '<p class="tz-empty-body">' + esc(props.description) + '</p>' : '') +
    (props.actionLabel
      ? button({ label: props.actionLabel, variant: 'primary', attrs: props.actionAttrs })
      : '') +
    '</div>';
}

/* ---------- skeleton ---------- */

/** @param {{width?:string, height?:string, shape?:'text'|'block'|'circle'}} props */
export function skeleton(props = {}) {
  const shape = props.shape || 'block';
  const cls = cx('tz-skeleton',
    shape === 'text' && 'tz-skeleton-text',
    shape === 'circle' && 'tz-skeleton-circle');
  const style = [];
  if (props.width) style.push('width:' + props.width);
  if (props.height) style.push('height:' + props.height);
  return '<span class="' + cls + '"' +
    (style.length ? ' style="' + style.join(';') + '"' : '') + '></span>';
}

/** @param {{count?:number, avatarSize?:string}} props */
export function skeletonRows(props = {}) {
  const count = props.count || 5;
  const size = props.avatarSize || '36px';
  let out = '';
  for (let i = 0; i < count; i += 1) {
    out += '<div class="tz-skeleton-row">' +
      skeleton({ shape: 'circle', width: size, height: size }) +
      '<span>' +
      skeleton({ shape: 'text', width: '60%' }) +
      skeleton({ shape: 'text', width: '85%' }) +
      '</span></div>';
  }
  return out;
}

/* ---------- drawer (static markup; behaviour lives in react.jsx) ---------- */

/**
 * @param {{title?:string, children?:string, footer?:string,
 *          side?:'start'|'end', closeLabel?:string}} props
 */
export function drawer(props = {}) {
  return '<div class="tz-drawer-backdrop" data-tz-drawer-backdrop></div>' +
    '<aside class="' + cx('tz-drawer', props.side === 'start' && 'tz-drawer-start') + '"' +
    ' role="dialog" aria-modal="true" aria-label="' + esc(props.title) + '">' +
    '<div class="tz-drawer-head">' +
    kicker({ text: props.title }) +
    '<button class="tz-btn tz-btn-ghost" data-tz-drawer-close' +
    ' aria-label="' + esc(props.closeLabel) + '">&#10005;</button>' +
    '</div>' +
    '<div class="tz-drawer-body">' + (props.children || '') + '</div>' +
    (props.footer ? '<div class="tz-drawer-foot">' + props.footer + '</div>' : '') +
    '</aside>';
}

/* ---------- toast ---------- */

/** @param {{message:string, tone?:'neutral'|'accent'|'success', actionLabel?:string}} props */
export function toast(props = {}) {
  const cls = cx('tz-toast', props.tone && props.tone !== 'neutral' && 'tz-toast-' + props.tone);
  return '<div class="' + cls + '" role="status">' +
    '<span class="tz-toast-text">' + esc(props.message) + '</span>' +
    (props.actionLabel ? button({ label: props.actionLabel, variant: 'ghost' }) : '') +
    '</div>';
}

/** @param {{toasts:Array<object>}} props */
export function toastStack(props = {}) {
  return '<div class="tz-toast-stack">' + (props.toasts || []).map(toast).join('') + '</div>';
}
