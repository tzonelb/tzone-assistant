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
    ai_teaching: { visible: true, label: null, order: 9 },
    saved_replies: { visible: true, label: null, order: 10 },
    reply_flows: { visible: true, label: null, order: 11 },
    community: { visible: true, label: null, order: 12 },
    catalogue: { visible: true, label: null, order: 13 },
    analytics: { visible: true, label: null, order: 14 },
    company_settings: { visible: true, label: null, order: 15 },
    roles_permissions: { visible: true, label: null, order: 16 },
    settings: { visible: true, label: null, order: 17 },
    platform_admin: { visible: true, label: null, order: 18 },
    theme_studio: { visible: true, label: null, order: 19 },
  },
  brand: { name: "T-ZONE", logoUrl: "/tzone-logo.png" },
};
