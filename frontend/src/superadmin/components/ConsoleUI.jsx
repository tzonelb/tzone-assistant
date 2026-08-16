import { useState } from "react";
import {
  ContentCopyOutlined,
  ErrorOutlineOutlined,
  KeyOutlined,
} from "@mui/icons-material";

import { humanize } from "../format";


export function ConsolePage({ eyebrow, title, description, actions, children }) {
  return (
    <div className="sa-page">
      <header className="sa-page-head">
        <div>
          {eyebrow ? <span className="sa-eyebrow">{eyebrow}</span> : null}
          <h1>{title}</h1>
          {description ? <p>{description}</p> : null}
        </div>

        {actions ? <div className="sa-page-actions">{actions}</div> : null}
      </header>

      {children}
    </div>
  );
}

export function ConsolePanel({ title, description, actions, children, className = "" }) {
  return (
    <section className={`sa-panel ${className}`.trim()}>
      {title || actions ? (
        <header className="sa-panel-head">
          <div>
            <h2>{title}</h2>
            {description ? <p>{description}</p> : null}
          </div>

          {actions ? <div className="sa-panel-actions">{actions}</div> : null}
        </header>
      ) : null}

      <div className="sa-panel-body">{children}</div>
    </section>
  );
}

export function ConsoleBanner({ tone = "error", children }) {
  if (!children) {
    return null;
  }

  return (
    <div className={`sa-banner is-${tone}`} role={tone === "error" ? "alert" : "status"}>
      {tone === "error" ? <ErrorOutlineOutlined fontSize="small" /> : null}
      <span>{children}</span>
    </div>
  );
}

export function ConsoleLoading({ label = "Loading..." }) {
  return (
    <div className="sa-loading">
      <span className="sa-spinner" />
      <strong>{label}</strong>
    </div>
  );
}

export function ConsoleEmpty({ title, description }) {
  return (
    <div className="sa-empty">
      <strong>{title}</strong>
      {description ? <span>{description}</span> : null}
    </div>
  );
}

export function StatusChip({ status }) {
  const value = String(status || "unknown").toLowerCase();
  const tone = value === "active" ? "ok" : value === "suspended" ? "danger" : "muted";

  return <span className={`sa-chip is-${tone}`}>{humanize(value)}</span>;
}

export function ConsoleButton({
  variant = "secondary",
  loading = false,
  disabled = false,
  type = "button",
  children,
  ...rest
}) {
  return (
    <button
      type={type}
      className={`sa-button is-${variant}`}
      disabled={disabled || loading}
      {...rest}
    >
      {loading ? <span className="sa-button-spinner" /> : null}
      {children}
    </button>
  );
}

export function ConfirmDialog({
  open,
  title,
  message,
  confirmLabel = "Confirm",
  cancelLabel = "Cancel",
  loading = false,
  onConfirm,
  onCancel,
}) {
  if (!open) {
    return null;
  }

  return (
    <div
      className="sa-dialog-backdrop"
      role="presentation"
      onMouseDown={(event) => {
        if (event.target === event.currentTarget) {
          onCancel?.();
        }
      }}
    >
      <section
        className="sa-dialog"
        role="dialog"
        aria-modal="true"
        aria-label={title}
      >
        <h3>{title}</h3>
        <div className="sa-dialog-body">{message}</div>

        <footer className="sa-dialog-actions">
          <ConsoleButton onClick={onCancel} disabled={loading}>
            {cancelLabel}
          </ConsoleButton>

          <ConsoleButton variant="danger" loading={loading} onClick={onConfirm}>
            {confirmLabel}
          </ConsoleButton>
        </footer>
      </section>
    </div>
  );
}

/*
 * A workspace code exists in readable form only in the response that created it.
 * It is rendered here as text the operator can select as well as copy, because a
 * clipboard write that silently fails on an insecure origin would lose a secret
 * that cannot be asked for again.
 */
export function WorkspaceCodeReveal({ code, notice, children }) {
  const [copyState, setCopyState] = useState("");

  async function handleCopy() {
    try {
      await navigator.clipboard.writeText(code);
      setCopyState("Copied to the clipboard.");
    } catch {
      setCopyState("Copying was blocked. Select the code above and copy it manually.");
    }
  }

  return (
    <div className="sa-secret">
      <header>
        <KeyOutlined fontSize="small" />
        <strong>Workspace code — shown once</strong>
      </header>

      <code className="sa-secret-code">{code}</code>

      <div className="sa-secret-actions">
        <ConsoleButton variant="primary" onClick={handleCopy}>
          <ContentCopyOutlined fontSize="small" />
          Copy code
        </ConsoleButton>

        {children}
      </div>

      {copyState ? <span className="sa-secret-copy-state">{copyState}</span> : null}

      <p className="sa-secret-notice">
        {notice ||
          "This code is not stored in readable form. Once you leave this screen it cannot be retrieved from the database, a backup or support — a new one would have to be issued."}
      </p>
    </div>
  );
}
