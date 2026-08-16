/** Shared presentation primitives. Modules build their screens from these so the app looks
 * like one product no matter how many teams add modules to it. */

import type { ChangeEvent, ReactNode } from "react";
import { useI18n } from "./i18n";
import { formatMoney, type Currency } from "./money";

export function Page({
  title,
  subtitle,
  actions,
  children,
}: {
  title: string;
  subtitle?: string;
  actions?: ReactNode;
  children: ReactNode;
}) {
  return (
    <section className="page">
      <header className="page-head">
        <div>
          <h1>{title}</h1>
          {subtitle ? <p className="muted">{subtitle}</p> : null}
        </div>
        {actions ? <div className="page-actions">{actions}</div> : null}
      </header>
      {children}
    </section>
  );
}

export function Card({
  title,
  actions,
  children,
}: {
  title?: string;
  actions?: ReactNode;
  children: ReactNode;
}) {
  return (
    <div className="card">
      {title || actions ? (
        <div className="card-head">
          {title ? <h2>{title}</h2> : <span />}
          {actions}
        </div>
      ) : null}
      <div className="card-body">{children}</div>
    </div>
  );
}

export function Field({
  label,
  children,
  hint,
}: {
  label: string;
  children: ReactNode;
  hint?: string;
}) {
  return (
    <label className="field">
      <span className="field-label">{label}</span>
      {children}
      {hint ? <span className="field-hint">{hint}</span> : null}
    </label>
  );
}

export function Select<T extends string>({
  value,
  onChange,
  options,
  placeholder,
}: {
  value: T | "";
  onChange: (value: T) => void;
  options: Array<{ value: T; label: string }>;
  placeholder?: string;
}) {
  return (
    <select
      value={value}
      onChange={(event: ChangeEvent<HTMLSelectElement>) => onChange(event.target.value as T)}
    >
      {placeholder ? <option value="">{placeholder}</option> : null}
      {options.map((option) => (
        <option key={option.value} value={option.value}>
          {option.label}
        </option>
      ))}
    </select>
  );
}

export function Money({ value, currency }: { value: number; currency: Currency }) {
  return (
    <span className={value < 0 ? "money negative" : "money"} dir="ltr">
      {formatMoney(value, currency)}
    </span>
  );
}

export function Badge({ tone, children }: { tone: string; children: ReactNode }) {
  return <span className={`badge badge-${tone}`}>{children}</span>;
}

export function EmptyState({ message }: { message: string }) {
  return <div className="empty">{message}</div>;
}

export function Toolbar({ children }: { children: ReactNode }) {
  return <div className="toolbar">{children}</div>;
}

export function StatusBadge({ status }: { status: string }) {
  const { t } = useI18n();
  const tone =
    status === "posted" || status === "paid"
      ? "ok"
      : status === "draft"
        ? "warn"
        : status === "void"
          ? "danger"
          : "info";
  return <Badge tone={tone}>{t(`status.${status}`)}</Badge>;
}

export function DataTable<T>({
  rows,
  columns,
  empty,
  onRowClick,
}: {
  rows: T[];
  columns: Array<{ key: string; header: string; render: (row: T) => ReactNode; align?: "end" }>;
  empty: string;
  onRowClick?: (row: T) => void;
}) {
  if (!rows.length) return <EmptyState message={empty} />;
  return (
    <div className="table-scroll">
      <table className="data-table">
        <thead>
          <tr>
            {columns.map((column) => (
              <th key={column.key} className={column.align === "end" ? "align-end" : undefined}>
                {column.header}
              </th>
            ))}
          </tr>
        </thead>
        <tbody>
          {rows.map((row, index) => (
            <tr
              key={index}
              onClick={onRowClick ? () => onRowClick(row) : undefined}
              className={onRowClick ? "clickable" : undefined}
            >
              {columns.map((column) => (
                <td key={column.key} className={column.align === "end" ? "align-end" : undefined}>
                  {column.render(row)}
                </td>
              ))}
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}
