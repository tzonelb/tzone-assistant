// The redesigned UI (CLAUDE_CODE_UI_IMPLEMENTATION.md) is now on by
// default for everyone — the owner asked for the full theme to actually
// show, not stay hidden behind an opt-in toggle. isUiV2Enabled() returns
// true unless a user explicitly turned it off for their own browser
// (Settings -> Appearance), so anyone who prefers the old layout while
// the remaining screens are still being rebuilt still can.
const UI_V2_KEY = "tzone_ui_v2";

export function isUiV2Enabled() {
  try {
    return localStorage.getItem(UI_V2_KEY) !== "0";
  } catch {
    return true;
  }
}

export function setUiV2Enabled(enabled) {
  try {
    if (enabled) {
      localStorage.removeItem(UI_V2_KEY);
    } else {
      localStorage.setItem(UI_V2_KEY, "0");
    }
  } catch {
    // Storage unavailable (private mode, etc.) — flag simply won't persist.
  }
  // localStorage's own "storage" event only fires in OTHER tabs, never
  // the one that made the change — this lets AppLayout react immediately
  // in the same tab without a full page reload.
  window.dispatchEvent(new CustomEvent("tzone:ui-v2-changed"));
}
