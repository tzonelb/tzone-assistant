import { CloseOutlined } from "@mui/icons-material";

import AppButton from "./AppButton";


export default function ConfirmDialog({
  open,
  title = "Confirm action",
  message,
  error,
  confirmLabel = "Confirm",
  cancelLabel = "Cancel",
  confirmVariant = "danger",
  loading = false,
  onConfirm,
  onCancel,
}) {
  if (!open) {
    return null;
  }

  return (
    <div
      className="tz-dialog-backdrop"
      role="presentation"
      onMouseDown={(event) => {
        if (event.target === event.currentTarget) {
          onCancel?.();
        }
      }}
    >
      <section
        className="tz-dialog"
        role="dialog"
        aria-modal="true"
        aria-labelledby="tz-dialog-title"
      >
        <header className="tz-dialog-header">
          <h3 id="tz-dialog-title">
            {title}
          </h3>

          <button
            type="button"
            className="tz-dialog-close"
            aria-label="Close dialog"
            onClick={onCancel}
          >
            <CloseOutlined fontSize="small" />
          </button>
        </header>

        <div className="tz-dialog-body">
          {message}
          {error ? <p className="customer-segment-error">{error}</p> : null}
        </div>

        <footer className="tz-dialog-actions">
          <AppButton
            variant="secondary"
            disabled={loading}
            onClick={onCancel}
          >
            {cancelLabel}
          </AppButton>

          <AppButton
            variant={confirmVariant}
            loading={loading}
            onClick={onConfirm}
          >
            {confirmLabel}
          </AppButton>
        </footer>
      </section>
    </div>
  );
}