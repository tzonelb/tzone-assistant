import { CloseOutlined } from "@mui/icons-material";

import AppButton from "./AppButton";


export default function ConfirmDialog({
  open,
  title = "Confirm action",
  message,
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

  function handleCancel() {
    if (loading) {
      return;
    }

    onCancel?.();
  }

  return (
    <div
      className="tz-dialog-backdrop"
      role="presentation"
      onMouseDown={(event) => {
        if (event.target === event.currentTarget) {
          handleCancel();
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
            disabled={loading}
            onClick={handleCancel}
          >
            <CloseOutlined fontSize="small" />
          </button>
        </header>

        <div className="tz-dialog-body">
          {message}
        </div>

        <footer className="tz-dialog-actions">
          <AppButton
            variant="secondary"
            disabled={loading}
            onClick={handleCancel}
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