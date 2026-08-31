// The redesigned interface is now the ONLY interface. The old layout and its
// screens have been removed from the platform, so there is nothing to switch
// back to: `isUiV2Enabled()` is always true, and any stale `tzone_ui_v2="0"`
// left in a browser from the old toggle is ignored. These two functions are
// kept only so the remaining import sites keep working while they are removed;
// they no longer read or write storage.

export function isUiV2Enabled() {
  return true;
}

export function setUiV2Enabled() {
  // No-op. There is no classic interface to return to.
}
