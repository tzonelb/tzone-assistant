/**
 * react.jsx — the T-ZONE component set for React.
 *
 * Rules:
 *   - PascalCase components, camelCase props.
 *   - No literal copy, number or data inside any component. Everything arrives
 *     as a prop; a component that renders nothing without props is correct.
 *   - No colour, spacing, font or radius literal — classes read tokens.css.
 *   - Every class is tz- prefixed.
 *
 * Load the stylesheets once in your entry file:
 *
 *   import './tzone-theme/tokens.css';
 *   import './tzone-theme/theme.css';
 */

import React, { useCallback, useEffect, useRef, useState } from 'react';
import { runtimeStyle } from './tokens.js';
import { injectFonts } from './css.js';

const cx = (...parts) => parts.filter(Boolean).join(' ');

/* ============================================================
   ThemeProvider
   ============================================================ */

/**
 * @param {{theme?:object, dir?:'ltr'|'rtl', lang?:string,
 *          className?:string, style?:object, children?:React.ReactNode}} props
 */
export function ThemeProvider({ theme, dir, lang, className, style, children }) {
  useEffect(() => { injectFonts(); }, []);
  return (
    <div
      className={cx('tz-root', className)}
      dir={dir}
      lang={lang}
      style={{ ...runtimeStyle(theme), ...style }}
    >
      {children}
    </div>
  );
}

/* ============================================================
   Buttons
   ============================================================ */

/**
 * @param {{variant?:'primary'|'secondary'|'ghost', block?:boolean,
 *          className?:string, children?:React.ReactNode}} props
 */
export function Button({ variant = 'secondary', block, className, children, ...rest }) {
  return (
    <button className={cx('tz-btn', 'tz-btn-' + variant, block && 'tz-btn-block', className)} {...rest}>
      {children}
    </button>
  );
}

/** @param {{icon:React.ReactNode, label:string, variant?:string}} props */
export function IconButton({ icon, label, variant = 'secondary', className, ...rest }) {
  return (
    <button
      className={cx('tz-btn', 'tz-btn-' + variant, 'tz-btn-icon', className)}
      aria-label={label}
      title={label}
      {...rest}
    >
      {icon}
    </button>
  );
}

/* ============================================================
   Tags, chips
   ============================================================ */

/** @param {{tone?:'neutral'|'outline'|'accent'|'accent-2', children?:React.ReactNode}} props */
export function Tag({ tone = 'neutral', className, children, ...rest }) {
  return <span className={cx('tz-tag', 'tz-tag-' + tone, className)} {...rest}>{children}</span>;
}

/** @param {{active?:boolean, children?:React.ReactNode}} props */
export function Chip({ active, className, children, ...rest }) {
  return (
    <button className={cx('tz-chip', active && 'tz-chip-on', className)} aria-pressed={!!active} {...rest}>
      {children}
    </button>
  );
}

/* ============================================================
   Type
   ============================================================ */

/** @param {{children?:React.ReactNode}} props */
export function Kicker({ className, children, ...rest }) {
  return <span className={cx('tz-kick', className)} {...rest}>{children}</span>;
}

/** @param {{children?:React.ReactNode}} props */
export function Figure({ className, children, ...rest }) {
  return <span className={cx('tz-fig', 'tz-num', className)} {...rest}>{children}</span>;
}

/** @param {{children?:React.ReactNode}} props */
export function Num({ className, children, ...rest }) {
  return <span className={cx('tz-num', className)} {...rest}>{children}</span>;
}

export function Divider({ className }) {
  return <hr className={cx('tz-hr', className)} />;
}

/* ============================================================
   Card
   ============================================================ */

/**
 * @param {{kicker?:React.ReactNode, title?:React.ReactNode, body?:React.ReactNode,
 *          meta?:React.ReactNode, children?:React.ReactNode}} props
 */
export function Card({ kicker, title, body, meta, className, children, ...rest }) {
  return (
    <div className={cx('tz-card', className)} {...rest}>
      {kicker != null && <span className="tz-card-kicker">{kicker}</span>}
      {title != null && <div className="tz-card-title">{title}</div>}
      {body != null && <p className="tz-card-body">{body}</p>}
      {children}
      {meta != null && <div className="tz-card-meta">{meta}</div>}
    </div>
  );
}

/* ============================================================
   Forms
   ============================================================ */

/**
 * @param {{label?:React.ReactNode, children?:React.ReactNode}} props
 */
export function Field({ label, className, children }) {
  return (
    <label className={cx('tz-field', className)}>
      {label != null && <span>{label}</span>}
      {children}
    </label>
  );
}

/** @param {{dir?:string, multiline?:boolean}} props */
export function Input({ multiline, dir = 'auto', className, ...rest }) {
  const Tag = multiline ? 'textarea' : 'input';
  return <Tag className={cx('tz-input', className)} dir={dir} {...rest} />;
}

/** @param {{options:string[], value?:string, onChange?:(value:string)=>void}} props */
export function Segmented({ options = [], value, onChange, className }) {
  return (
    <div className={cx('tz-seg', className)} role="tablist">
      {options.map((option) => (
        <span
          key={option}
          role="tab"
          aria-selected={option === value}
          className={cx('tz-seg-opt', option === value && 'tz-seg-opt-on')}
          onClick={onChange ? () => onChange(option) : undefined}
        >
          {option}
        </span>
      ))}
    </div>
  );
}

/** @param {{checked?:boolean, onChange?:Function, label?:React.ReactNode}} props */
export function Radio({ checked, onChange, label, className, ...rest }) {
  return (
    <label className={cx('tz-radio', className)}>
      <input type="checkbox" checked={!!checked} onChange={onChange} {...rest} />
      <span className="tz-dot" />
      {label}
    </label>
  );
}

/* ============================================================
   Avatar
   ============================================================ */

/**
 * @param {{initial?:string, src?:string, alt?:string,
 *          size?:'sm'|'md'|'lg', accent?:boolean}} props
 */
export function Avatar({ initial, src, alt, size = 'md', accent, className, ...rest }) {
  return (
    <span
      className={cx('tz-avatar', 'tz-avatar-' + size, accent && 'tz-avatar-accent', className)}
      {...rest}
    >
      {src ? <img src={src} alt={alt} /> : initial}
    </span>
  );
}

/* ============================================================
   Stats
   ============================================================ */

/** @param {{label?:React.ReactNode, value?:React.ReactNode, note?:React.ReactNode}} props */
export function Stat({ label, value, note, className }) {
  return (
    <div className={cx('tz-stat', className)}>
      {label != null && <Kicker>{label}</Kicker>}
      <Figure>{value}</Figure>
      {note != null && <div className="tz-text-muted">{note}</div>}
    </div>
  );
}

/** @param {{cells:Array<{label:React.ReactNode,value:React.ReactNode,note?:React.ReactNode}>}} props */
export function StatGrid({ cells = [], className }) {
  return (
    <div className={cx('tz-stat-grid', className)}>
      {cells.map((cell, index) => (
        <Stat key={cell.label != null ? String(cell.label) : index} {...cell} />
      ))}
    </div>
  );
}

/* ============================================================
   Table
   ============================================================ */

/**
 * @param {{columns:Array<React.ReactNode>, rows:Array<Array<React.ReactNode>>,
 *          rowKey?:(row:Array<any>, index:number)=>string}} props
 */
export function Table({ columns = [], rows = [], rowKey, className }) {
  return (
    <div className={cx('tz-tablewrap', className)}>
      <table className="tz-table">
        <thead>
          <tr>{columns.map((column, index) => <th key={index}>{column}</th>)}</tr>
        </thead>
        <tbody>
          {rows.map((row, rowIndex) => (
            <tr key={rowKey ? rowKey(row, rowIndex) : rowIndex}>
              {row.map((cell, cellIndex) => <td key={cellIndex}>{cell}</td>)}
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}

/* ============================================================
   Rail
   ============================================================ */

/**
 * @param {{minimised?:boolean, children?:React.ReactNode}} props
 */
export function Rail({ minimised, className, children, ...rest }) {
  return (
    <aside className={cx('tz-aside', minimised && 'tz-min', className)} {...rest}>
      {children}
    </aside>
  );
}

/**
 * @param {{label:React.ReactNode, icon?:React.ReactNode, active?:boolean,
 *          badge?:React.ReactNode, onSelect?:Function, href?:string}} props
 */
export function RailLink({ label, icon, active, badge, onSelect, href, className, ...rest }) {
  const Tag = href ? 'a' : 'button';
  return (
    <Tag
      className={cx('tz-rail-link', className)}
      href={href}
      onClick={onSelect}
      aria-current={active ? 'page' : undefined}
      {...rest}
    >
      {active && <span className="tz-mark" />}
      {icon}
      <span>{label}</span>
      {badge != null && <Num>{badge}</Num>}
    </Tag>
  );
}

/* ============================================================
   Drawer
   ============================================================ */

/**
 * Slides in from the inline edge. Closes on Escape and on backdrop click,
 * traps nothing else, and follows the document direction (RTL-safe).
 *
 * @param {{open?:boolean, onClose?:Function, title?:React.ReactNode,
 *          side?:'start'|'end', closeLabel?:string, footer?:React.ReactNode,
 *          children?:React.ReactNode}} props
 */
export function Drawer({ open, onClose, title, side = 'end', closeLabel, footer, className, children }) {
  const panelRef = useRef(null);

  const close = useCallback(() => { if (onClose) onClose(); }, [onClose]);

  useEffect(() => {
    if (!open) return undefined;
    const onKeyDown = (event) => { if (event.key === 'Escape') close(); };
    document.addEventListener('keydown', onKeyDown);
    const previous = document.body.style.overflow;
    document.body.style.overflow = 'hidden';
    if (panelRef.current) panelRef.current.focus();
    return () => {
      document.removeEventListener('keydown', onKeyDown);
      document.body.style.overflow = previous;
    };
  }, [open, close]);

  if (!open) return null;

  return (
    <>
      <div className="tz-drawer-backdrop" onClick={close} />
      <aside
        ref={panelRef}
        tabIndex={-1}
        role="dialog"
        aria-modal="true"
        aria-label={typeof title === 'string' ? title : undefined}
        className={cx('tz-drawer', side === 'start' && 'tz-drawer-start', className)}
      >
        <div className="tz-drawer-head">
          {title != null && <Kicker style={{ flex: 1 }}>{title}</Kicker>}
          <Button variant="ghost" onClick={close} aria-label={closeLabel}>&#10005;</Button>
        </div>
        <div className="tz-drawer-body">{children}</div>
        {footer != null && <div className="tz-drawer-foot">{footer}</div>}
      </aside>
    </>
  );
}

/* ============================================================
   Dialog
   ============================================================ */

/**
 * @param {{open?:boolean, onClose?:Function, title?:React.ReactNode,
 *          actions?:React.ReactNode, children?:React.ReactNode}} props
 */
export function Dialog({ open, onClose, title, actions, children, className }) {
  useEffect(() => {
    if (!open) return undefined;
    const onKeyDown = (event) => { if (event.key === 'Escape' && onClose) onClose(); };
    document.addEventListener('keydown', onKeyDown);
    return () => document.removeEventListener('keydown', onKeyDown);
  }, [open, onClose]);

  if (!open) return null;

  return (
    <div className="tz-dialog-backdrop" onClick={onClose}>
      <div
        className={cx('tz-dialog', className)}
        role="dialog"
        aria-modal="true"
        onClick={(event) => event.stopPropagation()}
      >
        {title != null && <div className="tz-dialog-title">{title}</div>}
        <div className="tz-dialog-body">{children}</div>
        {actions != null && <div className="tz-dialog-actions">{actions}</div>}
      </div>
    </div>
  );
}

/* ============================================================
   EmptyState
   ============================================================ */

/**
 * @param {{icon?:React.ReactNode, title?:React.ReactNode,
 *          description?:React.ReactNode, actionLabel?:React.ReactNode,
 *          onAction?:Function}} props
 */
export function EmptyState({ icon, title, description, actionLabel, onAction, className }) {
  return (
    <div className={cx('tz-empty', className)}>
      {icon != null && <span className="tz-empty-icon">{icon}</span>}
      {title != null && <h3 className="tz-empty-title">{title}</h3>}
      {description != null && <p className="tz-empty-body">{description}</p>}
      {actionLabel != null && (
        <Button variant="primary" onClick={onAction}>{actionLabel}</Button>
      )}
    </div>
  );
}

/* ============================================================
   Skeleton
   ============================================================ */

/** @param {{width?:string, height?:string, shape?:'text'|'block'|'circle'}} props */
export function Skeleton({ width, height, shape = 'block', className, style }) {
  return (
    <span
      className={cx(
        'tz-skeleton',
        shape === 'text' && 'tz-skeleton-text',
        shape === 'circle' && 'tz-skeleton-circle',
        className,
      )}
      style={{ width, height, ...style }}
      aria-hidden="true"
    />
  );
}

/** @param {{count?:number, avatarSize?:string}} props */
export function SkeletonRows({ count = 5, avatarSize = '36px', className }) {
  return (
    <div className={className} aria-busy="true">
      {Array.from({ length: count }, (unused, index) => (
        <div className="tz-skeleton-row" key={index}>
          <Skeleton shape="circle" width={avatarSize} height={avatarSize} />
          <span>
            <Skeleton shape="text" width="60%" />
            <Skeleton shape="text" width="85%" />
          </span>
        </div>
      ))}
    </div>
  );
}

/* ============================================================
   Toast
   ============================================================ */

/**
 * @param {{message?:React.ReactNode, tone?:'neutral'|'accent'|'success',
 *          actionLabel?:React.ReactNode, onAction?:Function, onDismiss?:Function}} props
 */
export function Toast({ message, tone = 'neutral', actionLabel, onAction, onDismiss, className }) {
  return (
    <div
      className={cx('tz-toast', tone !== 'neutral' && 'tz-toast-' + tone, className)}
      role="status"
      onClick={onDismiss}
    >
      <span className="tz-toast-text">{message}</span>
      {actionLabel != null && (
        <Button variant="ghost" onClick={onAction}>{actionLabel}</Button>
      )}
    </div>
  );
}

/**
 * @param {{toasts:Array<{id:string|number, message:React.ReactNode, tone?:string,
 *          actionLabel?:React.ReactNode, onAction?:Function}>,
 *          onDismiss?:(id:string|number)=>void}} props
 */
export function ToastStack({ toasts = [], onDismiss }) {
  return (
    <div className="tz-toast-stack">
      {toasts.map((item) => (
        <Toast
          key={item.id}
          message={item.message}
          tone={item.tone}
          actionLabel={item.actionLabel}
          onAction={item.onAction}
          onDismiss={onDismiss ? () => onDismiss(item.id) : undefined}
        />
      ))}
    </div>
  );
}

/**
 * Queue helper: returns { toasts, push, dismiss }.
 * @param {{timeout?:number}} [options]
 */
export function useToasts(options) {
  const timeout = (options && options.timeout) || 6000;
  const [toasts, setToasts] = useState([]);
  const nextId = useRef(0);

  const dismiss = useCallback((id) => {
    setToasts((list) => list.filter((item) => item.id !== id));
  }, []);

  const push = useCallback((item) => {
    nextId.current += 1;
    const id = nextId.current;
    setToasts((list) => list.concat([{ ...item, id }]));
    if (timeout) setTimeout(() => dismiss(id), timeout);
    return id;
  }, [dismiss, timeout]);

  return { toasts, push, dismiss };
}

/* ============================================================
   Shell
   ============================================================ */

/**
 * Dark rail on the inline-start edge, screen on the other side.
 *
 * @param {{rail?:React.ReactNode, topBar?:React.ReactNode, theme?:object,
 *          dir?:'ltr'|'rtl', lang?:string, railMinimised?:boolean,
 *          children?:React.ReactNode}} props
 */
export function Shell({ rail, topBar, theme, dir, lang, railMinimised, children }) {
  return (
    <ThemeProvider theme={theme} dir={dir} lang={lang} style={{ display: 'flex', height: '100vh', overflow: 'hidden' }}>
      {rail != null && <Rail minimised={railMinimised}>{rail}</Rail>}
      <div style={{ flex: 1, minWidth: 0, display: 'flex', flexDirection: 'column' }}>
        {topBar}
        <main style={{ flex: 1, minHeight: 0, overflowY: 'auto' }}>{children}</main>
      </div>
    </ThemeProvider>
  );
}
