import { useWorkspaceConfig } from "../contexts/WorkspaceConfigContext";


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

  return children;
}
