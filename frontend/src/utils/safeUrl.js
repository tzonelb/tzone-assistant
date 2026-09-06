// Defense-in-depth for any href/src built from stored or externally-sourced
// data (message attachment URLs, customer document links, etc). React
// escapes text nodes but NOT attribute values — a `javascript:`/`data:` URI
// in an href/src executes when clicked/loaded. Only http(s) URLs (or a
// same-origin relative path) are allowed through; anything else is
// rejected so the attribute is simply omitted rather than rendered unsafe.
export function safeHttpUrl(value) {
  const url = String(value || "").trim();
  if (!url) return null;
  // A bare relative/same-origin path (no scheme) is fine.
  if (url.startsWith("/") && !url.startsWith("//")) return url;
  try {
    const parsed = new URL(url, window.location.origin);
    if (parsed.protocol === "http:" || parsed.protocol === "https:") {
      return url;
    }
  } catch {
    // Not a parseable absolute URL — fall through to reject.
  }
  return null;
}
