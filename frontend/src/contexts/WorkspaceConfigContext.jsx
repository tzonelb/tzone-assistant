import {
  createContext,
  useCallback,
  useContext,
  useEffect,
  useMemo,
  useState,
} from "react";

import { getWorkspaceConfigRequest } from "../api/client";
import { useAuth } from "./AuthContext";


// Branding field -> the theme token it overrides. Kept here rather than in CSS
// because the server owns the field names (platform_service.BRANDING_FIELDS) and
// a mapping split across two files drifts the first time one side gains a field.
const COLOR_TOKENS = {
  primary_color: "--tz-primary",
  accent_color: "--tz-secondary",
  sidebar_color: "--tz-sidebar",
  surface_color: "--tz-surface",
  text_color: "--tz-text-primary",
};

const WorkspaceConfigContext = createContext(null);


function applyBranding(branding) {
  const root = document.documentElement;

  Object.entries(COLOR_TOKENS).forEach(([field, token]) => {
    const value = branding?.[field];

    if (value) {
      root.style.setProperty(token, value);
    } else {
      root.style.removeProperty(token);
    }
  });
}


export function WorkspaceConfigProvider({ children }) {
  const { authenticated } = useAuth();

  const [modules, setModules] = useState(null);
  const [branding, setBranding] = useState({});
  const [layout, setLayout] = useState({});
  const [loading, setLoading] = useState(true);

  const load = useCallback(async () => {
    if (!authenticated) {
      setModules(null);
      setBranding({});
      setLayout({});
      setLoading(false);
      applyBranding({});
      return;
    }

    try {
      const result = await getWorkspaceConfigRequest();

      setModules(result?.modules || {});
      setBranding(result?.branding || {});
      setLayout(result?.layout || {});
      applyBranding(result?.branding);
    } catch {
      // A configuration that cannot be read must not blank the navigation: the
      // API enforces the same switches, so falling back to "everything visible"
      // costs a 403 on a disabled module rather than an employee locked out of
      // their whole workspace.
      setModules(null);
    } finally {
      setLoading(false);
    }
  }, [authenticated]);

  useEffect(() => {
    load();
  }, [load]);

  const value = useMemo(
    () => ({
      modules,
      branding,
      layout,
      loading,
      // Unknown or not-yet-loaded means visible. The screen never hides a
      // module the server would have allowed.
      moduleEnabled: (key) =>
        !key || !modules || modules[key] !== false,
      refreshWorkspaceConfig: load,
    }),
    [modules, branding, layout, loading, load],
  );

  return (
    <WorkspaceConfigContext.Provider value={value}>
      {children}
    </WorkspaceConfigContext.Provider>
  );
}


export function useWorkspaceConfig() {
  const context = useContext(WorkspaceConfigContext);

  if (!context) {
    throw new Error(
      "useWorkspaceConfig must be used inside WorkspaceConfigProvider.",
    );
  }

  return context;
}
