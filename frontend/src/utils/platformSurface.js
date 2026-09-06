// Which surface the app is running on, and what a phone does not carry.
//
// The Android build is a Capacitor shell (see src/native/androidShell.js),
// which stamps `native-app` on the document element at startup. That class is
// the signal here, not the screen width: "the phone" in task #77 means the
// installed app, not a desktop browser someone made narrow. The class is set
// once and never changes within a session, so a plain check at render is right
// -- no hook, no listener.
//
// `WEB_ONLY_NAV_MODULES` mirrors, in the navigation's own module keys, the
// groups `backend/services/permission_catalogue.py` marks `web_only`:
// publishing (the scheduler, broadcast and post comments) and administration
// (company settings, which is where channels, roles and the subscription are
// reached). Composing and approving posts, connecting a channel and editing
// roles are desk work; the app is the inbox and what sits around a live
// conversation. A permission an employee holds still means what it says -- the
// phone simply does not open the screens it would.

export function isNativeApp() {
  if (typeof document === "undefined") return false;

  return document.documentElement.classList.contains("native-app");
}

// Module identifiers that name a desk-only area. Both the navigation key
// (SidebarV2's NAV_GROUPS: `publish`) and the route's module prop
// (App.jsx / ModuleRoute: `scheduler`) are listed, because the two disagree
// for publishing and this set answers for both the nav filter and the route
// guard. Not permission codes.
export const WEB_ONLY_NAV_MODULES = new Set([
  "publish",
  "scheduler",
  "comments",
  "broadcast",
  "company_settings",
]);

export function isWebOnlyModule(moduleKey) {
  return WEB_ONLY_NAV_MODULES.has(moduleKey);
}

// True when this module's screens should be hidden here: it is desk-only and
// we are in the phone app.
export function isHiddenOnThisSurface(moduleKey) {
  return isNativeApp() && isWebOnlyModule(moduleKey);
}
