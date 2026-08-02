// Local, per-browser opt-in for the redesigned UI (CLAUDE_CODE_UI_IMPLEMENTATION.md
// §1: "Ship each step behind a feature flag (ui_v2) so the old UI stays
// reachable until QA signs off"). Not a real experiment/rollout system —
// just a reversible toggle a tester or developer can flip locally, with
// no effect on anyone else's session. AppLayout.jsx reads this to switch
// between the v1 and v2 shell.
const UI_V2_KEY = "tzone_ui_v2";

export function isUiV2Enabled() {
  try {
    return localStorage.getItem(UI_V2_KEY) === "1";
  } catch {
    return false;
  }
}

export function setUiV2Enabled(enabled) {
  try {
    if (enabled) {
      localStorage.setItem(UI_V2_KEY, "1");
    } else {
      localStorage.removeItem(UI_V2_KEY);
    }
  } catch {
    // Storage unavailable (private mode, etc.) — flag simply won't persist.
  }
  // localStorage's own "storage" event only fires in OTHER tabs, never
  // the one that made the change — this lets AppLayout react immediately
  // in the same tab without a full page reload.
  window.dispatchEvent(new CustomEvent("tzone:ui-v2-changed"));
}
