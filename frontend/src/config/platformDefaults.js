// Offline/pre-fetch fallback for GET /api/platform-ui/config — same
// shape as the endpoint response (CLAUDE_CODE_THEME_SPEC.md §2-3), so
// the customer UI still renders correctly if the backend is
// unreachable or hasn't answered yet. Token VALUES mirror T-ZONE's
// actual current look (frontend/src/styles/theme.css's var() fallbacks),
// not the illustrative sample in the spec doc — applying this object
// must be a visual no-op until an admin actually publishes a theme.
// Keep in sync with backend/services/platform_ui_service.py's
// DEFAULT_TOKENS/DEFAULT_MODULES/DEFAULT_BRAND.
export const platformDefaults = {
  version: 0,
  tokens: {
    color: { accent: "#4F63F0", accent2: "#22C07D", mode: "light", rail: "paper" },
    type: { headingFont: "Inter", bodyFont: "Inter", baseSize: 15, headingScale: 1.0 },
    shape: { radius: 16, buttons: "solid", cardFill: true, shadow: "sm" },
    layout: { density: 1.0, railWidth: 236, direction: "auto" },
  },
  modules: {
    dashboard: { visible: true, label: null, order: 0 },
    conversations: { visible: true, label: null, order: 1 },
    notifications: { visible: true, label: null, order: 2 },
    tasks: { visible: true, label: null, order: 3 },
    appointments: { visible: true, label: null, order: 4 },
    team_chat: { visible: true, label: null, order: 5 },
    customers: { visible: true, label: null, order: 6 },
    broadcast: { visible: true, label: null, order: 7 },
    calls: { visible: true, label: null, order: 8 },
    dialer: { visible: true, label: null, order: 9 },
    ai_teaching: { visible: true, label: null, order: 10 },
    test_ai: { visible: true, label: null, order: 11 },
    saved_replies: { visible: true, label: null, order: 12 },
    reply_flows: { visible: true, label: null, order: 13 },
    community: { visible: true, label: null, order: 14 },
    publish: { visible: true, label: null, order: 15 },
    comments: { visible: true, label: null, order: 16 },
    catalogue: { visible: true, label: null, order: 17 },
    analytics: { visible: true, label: null, order: 18 },
    company_settings: { visible: true, label: null, order: 19 },
    roles_permissions: { visible: true, label: null, order: 20 },
    settings: { visible: true, label: null, order: 21 },
    platform_admin: { visible: true, label: null, order: 22 },
    theme_studio: { visible: true, label: null, order: 23 },
  },
  brand: { name: "T-ZONE", logoUrl: "/tzone-logo.png" },
};

/* Reading a module entry, whichever shape it arrived in.
 *
 * This file describes a module as `{ visible, label, order }`, but the live
 * endpoint answers `modules` as a flat `{ key: boolean }` map
 * (backend/services/platform_service.py builds it that way). Code that read
 * `modules[key].visible` therefore evaluated `true.visible` -> `undefined`,
 * and `undefined !== false` is true -- so the operator's module gate hid
 * nothing and a custom label never applied. Both shapes are real, so both are
 * read here instead of in three places that could drift apart again.
 *
 * Unknown keys stay visible on purpose: a module added in a later release must
 * not disappear for a company whose stored config predates it.
 */
export function isModuleVisible(modules, moduleKey) {
  const entry = modules?.[moduleKey];

  if (entry === undefined || entry === null) {
    return true;
  }

  if (typeof entry === "boolean") {
    return entry;
  }

  return entry.visible !== false;
}

export function moduleLabel(modules, moduleKey) {
  const entry = modules?.[moduleKey];

  if (!entry || typeof entry === "boolean") {
    return null;
  }

  return entry.label ?? null;
}
