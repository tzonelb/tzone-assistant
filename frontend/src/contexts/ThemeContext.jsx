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

// tzone-theme.css and classical-styles.css both ship a hand-picked
// 100..900 tint/shade ramp for --color-accent-* / --color-accent-2-* as
// their zero-config fallback (e.g. --color-accent-700:#0b6c9f next to
// --color-accent:#1b9be0) — used directly by .tag-accent, .tz-chip-on and
// the "-kick" label colour on every *PageV2.css screen. There is no other
// ramp-generation helper anywhere in the codebase (frontend or backend)
// to reuse, so this is one small color-mix formula: lighter tints toward
// white below 500, the picked colour itself at 500, darker shades toward
// black above it — the same shape as the hard-coded ramps it replaces.
export function generateAccentRamp(hex) {
  if (!hex) return {};
  return {
    100: `color-mix(in srgb, ${hex} 12%, white)`,
    200: `color-mix(in srgb, ${hex} 24%, white)`,
    300: `color-mix(in srgb, ${hex} 40%, white)`,
    400: `color-mix(in srgb, ${hex} 65%, white)`,
    500: hex,
    600: `color-mix(in srgb, ${hex} 85%, black)`,
    700: `color-mix(in srgb, ${hex} 68%, black)`,
    800: `color-mix(in srgb, ${hex} 50%, black)`,
    900: `color-mix(in srgb, ${hex} 35%, black)`,
  };
}

function applyTokens(tokens) {
  const root = document.documentElement.style;
  const { color, type, shape, layout } = tokens;

  // tzone-theme.css's own --color-divider (a 15%-opacity mix) is too faint
  // per owner feedback — CLAUDE.md forbids ever editing that file, so the
  // fix is a runtime override here (inline root properties always beat a
  // stylesheet's :root rule, load order aside), same mechanism as every
  // other token below. Stronger opacity, still derived from --color-text
  // so it keeps tracking whatever text color a future theme sets.
  root.setProperty("--color-divider", `color-mix(in srgb, ${color.mode === "dark" ? "#ffffff" : "#18202a"} 32%, transparent)`);

  root.setProperty("--color-accent", color.accent);
  root.setProperty("--color-accent-dark", `color-mix(in srgb, ${color.accent} 80%, black)`);
  root.setProperty("--color-accent-deep", `color-mix(in srgb, ${color.accent} 65%, black)`);
  root.setProperty("--color-accent-soft", `color-mix(in srgb, ${color.accent} 12%, white)`);
  for (const [stop, value] of Object.entries(generateAccentRamp(color.accent))) {
    root.setProperty(`--color-accent-${stop}`, value);
  }

  // Both spellings are live in this codebase: styles/theme.css (v1) reads
  // the unhyphenated --color-accent2(-dark/-soft), while
  // tzone-theme.css/classical-styles.css and every *PageV2.css screen
  // read the hyphenated --color-accent-2 / --color-accent-2-100..900
  // ramp. Set both so "accent 2" control actually reaches every consumer.
  root.setProperty("--color-accent2", color.accent2);
  root.setProperty("--color-accent2-dark", `color-mix(in srgb, ${color.accent2} 80%, black)`);
  root.setProperty("--color-accent2-soft", `color-mix(in srgb, ${color.accent2} 12%, white)`);
  root.setProperty("--color-accent-2", color.accent2);
  for (const [stop, value] of Object.entries(generateAccentRamp(color.accent2))) {
    root.setProperty(`--color-accent-2-${stop}`, value);
  }

  // color.rail (paper|ink|accent) was a real setting with no consumer —
  // SidebarV2 hard-coded a dark rail regardless of what Theme Studio
  // said. Compute the actual pair here so the rail really reflects it.
  if (color.rail === "ink") {
    root.setProperty("--tz-rail-bg", "#141414");
    root.setProperty("--tz-rail-text", "#f3f5f7");
  } else if (color.rail === "accent") {
    // Literal value from the source design's "Brand Blue" palette
    // (rail: "#1479b8") — not a computed approximation.
    root.setProperty("--tz-rail-bg", color.accent === "#1b9be0" ? "#1479b8" : `color-mix(in srgb, ${color.accent} 78%, black)`);
    root.setProperty("--tz-rail-text", "#f3f5f7");
  } else {
    root.setProperty("--tz-rail-bg", "#f8f7f5");
    root.setProperty("--tz-rail-text", "#201f1d");
  }

  root.setProperty("--font-heading", `${quoteFont(type.headingFont)}, Inter, ui-sans-serif, system-ui, sans-serif`);
  root.setProperty("--font-body", `${quoteFont(type.bodyFont)}, Inter, ui-sans-serif, system-ui, sans-serif`);
  root.setProperty("--font-base-size", `${type.baseSize}px`);
  root.setProperty("--font-heading-scale", String(type.headingScale));

  root.setProperty("--radius-md", `${shape.radius}px`);
  root.setProperty("--shadow-strength", String(SHADOW_STRENGTH[shape.shadow] ?? 1));

  // shape.buttons (outline|soft|solid) was a real, validated, stored
  // field with zero consumer — .btn-primary in tzone-theme.css already
  // reads var(--tz-btn-bg,transparent)/var(--tz-btn-fg,var(--color-accent)),
  // this just supplies the value. Every branch, including "outline" (the
  // platform's own approved default per CLAUDE.md: buttons outlined, not
  // filled), sets an EXPLICIT value rather than removeProperty()-ing back
  // to "let the stylesheet fallback win": frontend/src/theme/tokens.css
  // (also loaded globally, after tzone-theme.css) separately declares
  // --tz-btn-bg/--tz-btn-fg itself with a *static*, disconnected value
  // (`--tz-btn-fg: var(--tz-color-accent)`, a hard-coded #1b9be0 in its
  // own --tz-color-* namespace) — so removing our inline override would
  // silently fall through to that static colour instead of the live
  // accent the moment an admin switches back to outline. Setting it
  // explicitly here keeps outline correct regardless of that file.
  if (shape.buttons === "solid") {
    root.setProperty("--tz-btn-bg", color.accent);
    root.setProperty("--tz-btn-fg", "#ffffff");
  } else if (shape.buttons === "soft") {
    root.setProperty("--tz-btn-bg", `color-mix(in srgb, ${color.accent} 12%, transparent)`);
    root.setProperty("--tz-btn-fg", color.accent);
  } else {
    root.setProperty("--tz-btn-bg", "transparent");
    root.setProperty("--tz-btn-fg", color.accent);
  }

  // shape.cardFill is the same story as shape.buttons: a real, validated
  // field (the "Fill cards with background colour" checkbox) that .card
  // in tzone-theme.css already has a hook for (var(--tz-card-bg,transparent))
  // but nothing ever set. Reuses the existing --color-surface token — no
  // new hex literal. Set explicitly both ways for the same reason as
  // above (theme/tokens.css also declares a static --tz-card-bg).
  if (shape.cardFill) {
    root.setProperty("--tz-card-bg", "var(--color-surface)");
  } else {
    root.setProperty("--tz-card-bg", "transparent");
  }

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
