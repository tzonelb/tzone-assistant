function hasTimezone(value) {
  return /(?:Z|[+-]\d{2}:?\d{2})$/i.test(String(value || ""));
}

export function parsePlatformDate(value) {
  if (!value) return null;
  const raw = String(value).trim();
  const normalized = hasTimezone(raw) ? raw : `${raw.replace(" ", "T")}Z`;
  const date = new Date(normalized);
  return Number.isNaN(date.getTime()) ? null : date;
}

export function getUserTimezone() {
  return localStorage.getItem("tzone_ui_timezone") || "Asia/Beirut";
}

export function formatPlatformDateTime(value, options = {}) {
  const date = parsePlatformDate(value);
  if (!date) return value ? String(value) : "—";
  const timezone = options.timeZone || getUserTimezone();
  return new Intl.DateTimeFormat(undefined, {
    dateStyle: options.dateStyle || "medium",
    timeStyle: options.timeStyle || "short",
    timeZone: timezone,
  }).format(date);
}

export function platformTimestamp(value) {
  return parsePlatformDate(value)?.getTime() || 0;
}
