import { createContext, useContext, useEffect, useMemo, useState } from "react";
import { getPlatformUiConfigRequest } from "../api/client";
import { platformDefaults } from "../config/platformDefaults";

const ThemeContext = createContext(null);

const SHADOW_STRENGTH = { none: 0, sm: 1, md: 2 };

// Quote a font family name only if it needs it ("Cormorant Garamond"),
// leaving single-word names bare — matches normal CSS font-family syntax.
function quoteFont(name) {
  if (!name) return null;
  return name.includes(" ") ? `"${name}"` : name;
}

function applyTokens(tokens) {
  const root = document.documentElement.style;
  const { color, type, shape, layout } = tokens;

  root.setProperty("--color-accent", color.accent);
  root.setProperty("--color-accent-dark", `color-mix(in srgb, ${color.accent} 80%, black)`);
  root.setProperty("--color-accent-deep", `color-mix(in srgb, ${color.accent} 65%, black)`);
  root.setProperty("--color-accent-soft", `color-mix(in srgb, ${color.accent} 12%, white)`);
  root.setProperty("--color-accent2", color.accent2);
  root.setProperty("--color-accent2-dark", `color-mix(in srgb, ${color.accent2} 80%, black)`);
  root.setProperty("--color-accent2-soft", `color-mix(in srgb, ${color.accent2} 12%, white)`);

  root.setProperty("--font-heading", `${quoteFont(type.headingFont)}, Inter, ui-sans-serif, system-ui, sans-serif`);
  root.setProperty("--font-body", `${quoteFont(type.bodyFont)}, Inter, ui-sans-serif, system-ui, sans-serif`);
  root.setProperty("--font-base-size", `${type.baseSize}px`);
  root.setProperty("--font-heading-scale", String(type.headingScale));

  root.setProperty("--radius-md", `${shape.radius}px`);
  root.setProperty("--shadow-strength", String(SHADOW_STRENGTH[shape.shadow] ?? 1));

  root.setProperty("--space-density", String(layout.density));
  root.setProperty("--tz-rail-width", `${layout.railWidth}px`);

  // color.mode is intentionally NOT applied to document.documentElement
  // here: AppLayout.jsx / UISettingsPage.jsx already own light/dark via
  // a personal "tzone_ui_theme" preference (light/dark/auto). Reconciling
  // "platform default mode, personal choice overrides it" belongs to the
  // Phase 2 shell work, not this tokens-only pass.
  if (layout.direction === "ltr" || layout.direction === "rtl") {
    document.documentElement.dir = layout.direction;
  }
}

export function ThemeProvider({ children }) {
  const [config, setConfig] = useState(platformDefaults);
  const [loading, setLoading] = useState(true);

  useEffect(() => {
    let cancelled = false;
    function fetchConfig() {
      getPlatformUiConfigRequest()
        .then((resolved) => {
          if (!cancelled && resolved) setConfig(resolved);
        })
        .catch(() => {
          // Backend unreachable or the caller isn't authenticated yet —
          // platformDefaults (already applied below) keeps the UI usable.
        })
        .finally(() => {
          if (!cancelled) setLoading(false);
        });
    }
    fetchConfig();
    // Re-fetch once the user actually logs in: the initial mount often
    // happens on the login page before a token exists, so that first
    // request 401s and falls back to platformDefaults — without this a
    // freshly-published theme wouldn't show until a manual page reload.
    window.addEventListener("tzone:auth-changed", fetchConfig);
    return () => {
      cancelled = true;
      window.removeEventListener("tzone:auth-changed", fetchConfig);
    };
  }, []);

  useEffect(() => {
    applyTokens(config.tokens);
  }, [config]);

  const value = useMemo(() => ({
    version: config.version,
    tokens: config.tokens,
    modules: config.modules,
    brand: config.brand,
    loading,
    refresh: () => getPlatformUiConfigRequest().then((resolved) => resolved && setConfig(resolved)).catch(() => {}),
  }), [config, loading]);

  return <ThemeContext.Provider value={value}>{children}</ThemeContext.Provider>;
}

export function usePlatformTheme() {
  const context = useContext(ThemeContext);
  if (!context) {
    throw new Error("usePlatformTheme must be used inside ThemeProvider.");
  }
  return context;
}

// Convenience: is a given nav module visible for the current session?
// Defaults to visible if the key is missing from modules (fail-open, so
// an unknown/newer module never vanishes because a stale theme predates it).
export function useModuleVisible(moduleKey) {
  const { modules } = usePlatformTheme();
  return modules?.[moduleKey]?.visible !== false;
}
