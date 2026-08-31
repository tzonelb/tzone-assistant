import { useWorkspaceConfig } from "../contexts/WorkspaceConfigContext";
import { isNativeApp, isWebOnlyModule } from "../utils/platformSurface";


/**
 * Blocks a screen whose module the platform administrator switched off.
 *
 * This mirrors the API, which refuses the same requests through
 * `backend/services/module_access.require_module`. It is not the enforcement —
 * the server is — but a hidden link the user reached by typing the URL should
 * say so plainly rather than render a screen whose every request 403s.
 *
 * It shows a notice instead of redirecting: with several modules off, a redirect
 * can bounce between two disabled screens, and an employee then sees a flicker
 * with no explanation at all.
 */
export default function ModuleRoute({ module, children }) {
  const { moduleEnabled, loading } = useWorkspaceConfig();

  if (loading) {
    return (
      <main className="full-screen-state">
        <div className="loading-spinner" />
      </main>
    );
  }

  if (!moduleEnabled(module)) {
    return (
      <main className="full-screen-state">
        <strong>This module is not enabled for your company.</strong>
        <p>Ask your platform administrator to switch it on.</p>
      </main>
    );
  }

  // Desk-only screens (the publisher, broadcast, company settings) are not
  // carried by the phone app -- task #77. The nav does not link them there, but
  // a deep link or a bookmark could still land here, so it says why rather than
  // rendering a screen the app is not meant to run.
  if (isNativeApp() && isWebOnlyModule(module)) {
    return (
      <main className="full-screen-state">
        <strong>This screen is available on the web app.</strong>
        <p>Open it from a computer — the phone app is for the inbox and live conversations.</p>
      </main>
    );
  }

  return children;
}
