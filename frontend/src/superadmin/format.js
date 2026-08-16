function hasTimezone(value) {
  return /(?:Z|[+-]\d{2}:?\d{2})$/i.test(String(value || ""));
}

// The control plane stores timestamps as naive UTC ISO strings, so a value
// without an offset is read as UTC rather than as the operator's local time.
export function formatTimestamp(value, options = {}) {
  if (!value) return "—";

  const raw = String(value).trim();
  const date = new Date(hasTimezone(raw) ? raw : `${raw.replace(" ", "T")}Z`);

  if (Number.isNaN(date.getTime())) {
    return raw;
  }

  return new Intl.DateTimeFormat(undefined, {
    dateStyle: options.dateStyle || "medium",
    timeStyle: options.timeStyle || "short",
  }).format(date);
}

export function formatDate(value) {
  return formatTimestamp(value, { timeStyle: undefined, dateStyle: "medium" });
}

export function formatBytes(value) {
  const bytes = Number(value || 0);

  if (!Number.isFinite(bytes) || bytes <= 0) {
    return "0 B";
  }

  const units = ["B", "KB", "MB", "GB", "TB"];
  const exponent = Math.min(
    units.length - 1,
    Math.floor(Math.log(bytes) / Math.log(1024)),
  );
  const size = bytes / 1024 ** exponent;

  return `${size.toFixed(exponent === 0 ? 0 : 1)} ${units[exponent]}`;
}

export function formatCount(value) {
  return new Intl.NumberFormat().format(Number(value || 0));
}

export function humanize(value) {
  return String(value ?? "")
    .replaceAll("_", " ")
    .replace(/\b\w/g, (letter) => letter.toUpperCase());
}
