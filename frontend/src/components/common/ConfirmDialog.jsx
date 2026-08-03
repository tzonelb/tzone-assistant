import { CloseOutlined } from "@mui/icons-material";

export default function ConfirmDialog({
  open,
  title = "Confirm action",
  message,
  error,
  confirmLabel = "Confirm",
  cancelLabel = "Cancel",
  // confirmVariant is accepted for backward compatibility but no longer used —
  // this design system has no red/danger button; the dialog's own title/message
  // conveys the seriousness of a destructive action instead.
  confirmVariant: _confirmVariant,
  loading = false,
  onConfirm,
  onCancel,
}) {
  if (!open) {
    return null;
  }

  return (
    <div
      className="dialog-backdrop"
      role="presentation"
      onMouseDown={(event) => {
        if (event.target === event.currentTarget) {
          onCancel?.();
        }
      }}
    >
      <section
        className="dialog"
        role="dialog"
        aria-modal="true"
        aria-labelledby="tz-confirm-dialog-title"
      >
        <header style={{ display: "flex", alignItems: "center", justifyContent: "space-between", gap: "var(--space-2)" }}>
          <span className="dialog-title" id="tz-confirm-dialog-title">
            {title}
          </span>

          <button
            type="button"
            className="btn btn-ghost btn-icon"
            aria-label="Close dialog"
            onClick={onCancel}
          >
            <CloseOutlined fontSize="small" />
          </button>
        </header>

        <div className="dialog-body">
          {message}
          {error ? <p style={{ color: "var(--color-accent-700)" }}>{error}</p> : null}
        </div>

        <div className="dialog-actions">
          <button
            type="button"
            className="btn btn-secondary"
            disabled={loading}
            onClick={onCancel}
          >
            {cancelLabel}
          </button>

          <button
            type="button"
            className="btn btn-primary"
            disabled={loading}
            onClick={onConfirm}
          >
            {loading ? "Working…" : confirmLabel}
          </button>
        </div>
      </section>
    </div>
  );
}
