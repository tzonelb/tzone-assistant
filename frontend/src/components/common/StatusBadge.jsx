const statusToneMap = {
  active: "success",
  online: "success",
  connected: "success",
  completed: "success",
  paid: "success",
  success: "success",

  pending: "warning",
  waiting: "warning",
  processing: "warning",
  warning: "warning",

  inactive: "danger",
  disconnected: "danger",
  failed: "danger",
  expired: "danger",
  cancelled: "danger",
  error: "danger",

  open: "info",
  new: "info",
  info: "info",
};


export default function StatusBadge({
  status,
  label,
  tone,
  showDot = true,
}) {
  const normalizedStatus = String(status || "")
    .trim()
    .toLowerCase();

  const resolvedTone =
    tone ||
    statusToneMap[normalizedStatus] ||
    "neutral";

  const displayLabel =
    label ||
    status ||
    "Unknown";

  return (
    <span
      className={`tz-status-badge tz-status-${resolvedTone}`}
    >
      {showDot ? (
        <span className="tz-status-dot" />
      ) : null}

      <span>{displayLabel}</span>
    </span>
  );
}