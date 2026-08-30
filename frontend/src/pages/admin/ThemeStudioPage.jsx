import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { useNavigate } from "react-router-dom";
import {
  createUiThemeDraftRequest,
  getPlatformUiConfigRequest,
  listUiThemesRequest,
  publishUiThemeRequest,
  restoreUiThemeRequest,
  updateUiThemeDraftRequest,
} from "../../api/client";
import { generateAccentRamp } from "../../contexts/ThemeContext";
import "./ThemeStudioPage.css";

// Real feature per CLAUDE_CODE_THEME_SPEC.md §5 — platform scope only
// (a company owner's own tenant override is a separate, smaller screen
// this doesn't attempt yet). Backend already built and tested this
// session: backend/services/platform_ui_service.py.
const SECTIONS = ["Brand & colour", "Typography", "Shape & elevation", "Layout & density", "Modules & menu", "Scope & publish"];

const ALLOWED_FONTS = ["Inter", "Cormorant Garamond", "Lora", "IBM Plex Sans", "Manrope", "Cairo"];

// Curated starting points named in the spec. Not arbitrary — Classical
// mirrors the Design project's own "Ink & Gold" palette; T-ZONE Modern
// mirrors the platform's actual current look (a deliberate "reset").
const PRESETS = {
  "Classical": {
    color: { accent: "#b68235", accent2: "#7d5411", mode: "light", rail: "paper" },
    type: { headingFont: "Cormorant Garamond", bodyFont: "Lora", baseSize: 15, headingScale: 1.0 },
    shape: { radius: 4, buttons: "outline", cardFill: false, shadow: "sm" },
    layout: { density: 1.0, railWidth: 222, direction: "auto" },
  },
  "T-ZONE Modern": {
    color: { accent: "#4F63F0", accent2: "#22C07D", mode: "light", rail: "paper" },
    type: { headingFont: "Inter", bodyFont: "Inter", baseSize: 15, headingScale: 1.0 },
    shape: { radius: 16, buttons: "solid", cardFill: true, shadow: "sm" },
    layout: { density: 1.0, railWidth: 236, direction: "auto" },
  },
  "Console Dark": {
    color: { accent: "#5b7a8c", accent2: "#7aa38f", mode: "dark", rail: "ink" },
    type: { headingFont: "IBM Plex Sans", bodyFont: "IBM Plex Sans", baseSize: 14, headingScale: 1.0 },
    shape: { radius: 6, buttons: "soft", cardFill: true, shadow: "none" },
    layout: { density: 0.9, railWidth: 210, direction: "auto" },
  },
  "Arabic First": {
    color: { accent: "#1f8a52", accent2: "#2fa8e8", mode: "light", rail: "paper" },
    type: { headingFont: "Cairo", bodyFont: "Cairo", baseSize: 16, headingScale: 1.0 },
    shape: { radius: 10, buttons: "solid", cardFill: true, shadow: "sm" },
    layout: { density: 1.05, railWidth: 236, direction: "rtl" },
  },
};

function mergeSection(base, override) {
  const merged = JSON.parse(JSON.stringify(base));
  for (const [section, values] of Object.entries(override || {})) {
    merged[section] = { ...(merged[section] || {}), ...values };
  }
  return merged;
}

function PreviewPane({ tokens }) {
  const accentRamp = generateAccentRamp(tokens.color.accent);
  const accent2Ramp = generateAccentRamp(tokens.color.accent2);

  // Mirrors ThemeContext's applyTokens() button-style logic exactly, so
  // the studio's own preview actually shows what flipping "Buttons"
  // (outline/soft/solid) does instead of leaving the sample buttons
  // looking identical no matter which option is picked.
  let btnBg;
  let btnFg;
  if (tokens.shape.buttons === "solid") {
    btnBg = tokens.color.accent;
    btnFg = "#ffffff";
  } else if (tokens.shape.buttons === "soft") {
    btnBg = `color-mix(in srgb, ${tokens.color.accent} 12%, transparent)`;
    btnFg = tokens.color.accent;
  } else {
    btnBg = "transparent";
    btnFg = tokens.color.accent;
  }

  const style = {
    "--color-accent": tokens.color.accent,
    "--color-accent-dark": accentRamp[600],
    "--color-accent-soft": accentRamp[100],
    "--color-accent2": tokens.color.accent2,
    "--color-accent2-dark": accent2Ramp[600],
    "--color-accent2-soft": accent2Ramp[100],
    "--color-accent-100": accentRamp[100],
    "--color-accent-800": accentRamp[800],
    "--tz-btn-bg": btnBg,
    "--tz-btn-fg": btnFg,
    "--font-heading": `"${tokens.type.headingFont}", sans-serif`,
    "--font-body": `"${tokens.type.bodyFont}", sans-serif`,
    "--radius-md": `${tokens.shape.radius}px`,
    background: tokens.color.mode === "dark" ? "#0b1220" : "#fff",
    color: tokens.color.mode === "dark" ? "#f4f7fb" : "#16182B",
  };
  // No "tzv2" wrapper needed: the app has a single global button/card/tag
  // system (classical-styles.css + tzone-theme.css) that reads every token
  // this page edits (--tz-btn-bg/--tz-btn-fg/--tz-card-bg/--color-accent-*),
  // so the preview reflects "Buttons: outline/soft/solid" live.
  return (
    <div className="theme-preview-pane" style={style}>
      <div className="theme-preview-header" style={{ background: tokens.color.rail === "ink" ? "#141414" : tokens.color.rail === "accent" ? tokens.color.accent : "#f3f2f2" }}>
        <strong style={{ fontFamily: "var(--font-heading)", color: tokens.color.rail === "paper" ? "#16182B" : "#fff" }}>T-ZONE</strong>
      </div>
      <div className="theme-preview-body">
        <div className="tz-stat" style={{ borderRadius: `${tokens.shape.radius}px` }}>
          <span className="tz-kick">Conversations</span>
          <div className="tz-fig" style={{ fontFamily: "var(--font-heading)", fontSize: 28 }}>1,284</div>
        </div>
        <div className="theme-preview-row">
          <button type="button" className="btn btn-primary" style={{ borderRadius: `${tokens.shape.radius}px` }}>Primary action</button>
          <button type="button" className="btn btn-secondary" style={{ borderRadius: `${tokens.shape.radius}px` }}>Secondary</button>
          <span className="tag tag-accent">Open</span>
          <span className="tag tag-outline">Waiting</span>
        </div>
        <table className="table">
          <thead><tr><th>Customer</th><th>Channel</th><th>Status</th></tr></thead>
          <tbody>
            <tr><td>Nour Chami</td><td>WhatsApp</td><td><span className="tag tag-neutral">Open</span></td></tr>
          </tbody>
        </table>
      </div>
    </div>
  );
}

export default function ThemeStudioPage() {
  const navigate = useNavigate();
  const [activeSection, setActiveSection] = useState(SECTIONS[0]);
  const [publishedConfig, setPublishedConfig] = useState(null);
  const [themes, setThemes] = useState([]);
  const [draft, setDraft] = useState(null);
  const [effectiveTokens, setEffectiveTokens] = useState(null);
  const [effectiveModules, setEffectiveModules] = useState(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState("");
  const [saving, setSaving] = useState(false);
  const [publishReason, setPublishReason] = useState("");
  const [publishing, setPublishing] = useState(false);
  const debounceRef = useRef(null);

  const load = useCallback(async () => {
    setLoading(true);
    setError("");
    try {
      const [config, themeList] = await Promise.all([
        getPlatformUiConfigRequest(),
        listUiThemesRequest("platform", null),
      ]);
      setPublishedConfig(config);
      setThemes(themeList.themes || []);
      const existingDraft = (themeList.themes || []).find((t) => t.status === "draft") || null;
      setDraft(existingDraft);
      setEffectiveTokens(mergeSection(config.tokens, existingDraft?.tokens));
      setEffectiveModules({ ...config.modules, ...(existingDraft?.modules || {}) });
    } catch (requestError) {
      setError(requestError.message || "Could not load Theme Studio.");
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => { load(); }, [load]);

  async function persistPatch(tokenPatch) {
    setSaving(true);
    setError("");
    try {
      if (!draft) {
        const created = await createUiThemeDraftRequest({ scopeType: "platform", tokens: tokenPatch, modules: {} });
        setDraft(created);
      } else {
        const updated = await updateUiThemeDraftRequest(draft.id, { tokens: tokenPatch });
        setDraft(updated);
      }
    } catch (requestError) {
      setError(requestError.message || "Could not save the draft.");
    } finally {
      setSaving(false);
    }
  }

  function updateToken(section, key, value) {
    setEffectiveTokens((current) => ({ ...current, [section]: { ...current[section], [key]: value } }));
    if (debounceRef.current) window.clearTimeout(debounceRef.current);
    debounceRef.current = window.setTimeout(() => {
      persistPatch({ [section]: { [key]: value } });
    }, 150);
  }

  async function applyPreset(name) {
    const preset = PRESETS[name];
    setEffectiveTokens(mergeSection(publishedConfig.tokens, preset));
    await persistPatch(preset);
  }

  async function handlePublish() {
    if (!draft || !publishReason.trim()) return;
    setPublishing(true);
    setError("");
    try {
      await publishUiThemeRequest(draft.id, publishReason.trim());
      setPublishReason("");
      await load();
    } catch (requestError) {
      setError(requestError.message || "Could not publish.");
    } finally {
      setPublishing(false);
    }
  }

  async function handleRestore(themeId) {
    setSaving(true);
    setError("");
    try {
      await restoreUiThemeRequest(themeId);
      await load();
    } catch (requestError) {
      setError(requestError.message || "Could not restore that version.");
    } finally {
      setSaving(false);
    }
  }

  const diff = useMemo(() => {
    if (!draft || !publishedConfig) return [];
    const changes = [];
    for (const [section, values] of Object.entries(effectiveTokens || {})) {
      for (const [key, value] of Object.entries(values)) {
        const before = publishedConfig.tokens[section]?.[key];
        if (before !== value) changes.push(`${section}.${key}: ${before} → ${value}`);
      }
    }
    return changes;
  }, [draft, publishedConfig, effectiveTokens]);

  const history = useMemo(() => themes.filter((t) => t.status !== "draft").sort((a, b) => b.version - a.version), [themes]);

  if (loading) return <div className="theme-studio-page tzv2"><p>Loading Theme Studio…</p></div>;
  if (!effectiveTokens) return <div className="theme-studio-page tzv2"><p className="theme-studio-error">{error || "Could not load."}</p></div>;

  const t = effectiveTokens;

  return (
    <div className="theme-studio-page tzv2">
      <header className="theme-studio-header">
        <button type="button" className="btn btn-ghost" onClick={() => navigate("/platform-admin")}>← Platform Admin</button>
        <h1>Theme Studio</h1>
        {saving ? <span className="tz-kick">Saving…</span> : null}
      </header>

      {error ? <p className="theme-studio-error">{error}</p> : null}

      <div className="theme-studio-layout">
        <nav className="theme-studio-nav">
          {SECTIONS.map((section) => (
            <button type="button" key={section} className={activeSection === section ? "is-active" : ""} onClick={() => setActiveSection(section)}>
              {section}
            </button>
          ))}
          <div className="theme-studio-presets">
            <span className="tz-kick">Presets</span>
            {Object.keys(PRESETS).map((name) => (
              <button type="button" key={name} className="btn btn-secondary btn-block" onClick={() => applyPreset(name)}>{name}</button>
            ))}
          </div>
        </nav>

        <div className="theme-studio-controls">
          {activeSection === "Brand & colour" ? (
            <div className="field-grid">
              <label className="field"><span>Accent</span><input type="color" value={t.color.accent} onChange={(e) => updateToken("color", "accent", e.target.value)} /></label>
              <label className="field"><span>Accent 2 (status colour)</span><input type="color" value={t.color.accent2} onChange={(e) => updateToken("color", "accent2", e.target.value)} /></label>
              <label className="field"><span>Mode</span><select className="input" value={t.color.mode} onChange={(e) => updateToken("color", "mode", e.target.value)}><option value="light">Light</option><option value="dark">Dark</option></select></label>
              <label className="field"><span>Side rail</span><select className="input" value={t.color.rail} onChange={(e) => updateToken("color", "rail", e.target.value)}><option value="paper">Paper — light, hairline edge</option><option value="ink">Ink — near-black</option><option value="accent">Accent — the palette's own tone</option></select></label>
            </div>
          ) : null}

          {activeSection === "Typography" ? (
            <div className="field-grid">
              <label className="field"><span>Heading font</span><select className="input" value={t.type.headingFont} onChange={(e) => updateToken("type", "headingFont", e.target.value)}>{ALLOWED_FONTS.map((f) => <option value={f} key={f}>{f}</option>)}</select></label>
              <label className="field"><span>Body font</span><select className="input" value={t.type.bodyFont} onChange={(e) => updateToken("type", "bodyFont", e.target.value)}>{ALLOWED_FONTS.map((f) => <option value={f} key={f}>{f}</option>)}</select></label>
              <label className="field"><span>Base size · {t.type.baseSize}px</span><input type="range" min="12" max="18" value={t.type.baseSize} onChange={(e) => updateToken("type", "baseSize", Number(e.target.value))} /></label>
              <label className="field"><span>Heading scale · {t.type.headingScale}</span><input type="range" min="0.85" max="1.25" step="0.01" value={t.type.headingScale} onChange={(e) => updateToken("type", "headingScale", Number(e.target.value))} /></label>
            </div>
          ) : null}

          {activeSection === "Shape & elevation" ? (
            <div className="field-grid">
              <label className="field"><span>Corner radius · {t.shape.radius}px</span><input type="range" min="0" max="24" value={t.shape.radius} onChange={(e) => updateToken("shape", "radius", Number(e.target.value))} /></label>
              <label className="field"><span>Buttons</span><select className="input" value={t.shape.buttons} onChange={(e) => updateToken("shape", "buttons", e.target.value)}><option value="outline">Outline</option><option value="soft">Soft</option><option value="solid">Solid</option></select></label>
              <label className="field"><span>Shadow</span><select className="input" value={t.shape.shadow} onChange={(e) => updateToken("shape", "shadow", e.target.value)}><option value="none">None</option><option value="sm">Small</option><option value="md">Medium</option></select></label>
              <label className="radio"><input type="checkbox" checked={t.shape.cardFill} onChange={(e) => updateToken("shape", "cardFill", e.target.checked)} /> <span>Fill cards with background colour</span></label>
            </div>
          ) : null}

          {activeSection === "Layout & density" ? (
            <div className="field-grid">
              <label className="field"><span>Density · {t.layout.density}</span><input type="range" min="0.75" max="1.2" step="0.01" value={t.layout.density} onChange={(e) => updateToken("layout", "density", Number(e.target.value))} /></label>
              <label className="field"><span>Rail width · {t.layout.railWidth}px</span><input type="range" min="180" max="300" step="2" value={t.layout.railWidth} onChange={(e) => updateToken("layout", "railWidth", Number(e.target.value))} /></label>
              <label className="field"><span>Direction</span><select className="input" value={t.layout.direction} onChange={(e) => updateToken("layout", "direction", e.target.value)}><option value="auto">Auto — follow interface language</option><option value="ltr">Left to right</option><option value="rtl">Right to left</option></select></label>
            </div>
          ) : null}

          {activeSection === "Modules & menu" ? (
            <div className="theme-studio-modules">
              {Object.entries(effectiveModules || {}).sort((a, b) => (a[1].order ?? 0) - (b[1].order ?? 0)).map(([key, entry]) => (
                <div className="theme-studio-module-row" key={key}>
                  <span>{entry.label || key.replaceAll("_", " ")}</span>
                  <label className="radio">
                    <input
                      type="checkbox"
                      checked={entry.visible !== false}
                      onChange={(e) => {
                        const nextVisible = e.target.checked;
                        setEffectiveModules((current) => ({ ...current, [key]: { ...current[key], visible: nextVisible } }));
                        if (debounceRef.current) window.clearTimeout(debounceRef.current);
                        debounceRef.current = window.setTimeout(async () => {
                          setSaving(true);
                          try {
                            const updated = draft
                              ? await updateUiThemeDraftRequest(draft.id, { modules: { [key]: { visible: nextVisible } } })
                              : await createUiThemeDraftRequest({ scopeType: "platform", tokens: {}, modules: { [key]: { visible: nextVisible } } });
                            setDraft(updated);
                          } catch (requestError) {
                            setError(requestError.message || "Could not save module visibility.");
                          } finally {
                            setSaving(false);
                          }
                        }, 150);
                      }}
                    />
                    <span>Visible in menu</span>
                  </label>
                </div>
              ))}
            </div>
          ) : null}

          {activeSection === "Scope & publish" ? (
            <div className="theme-studio-publish">
              <p className="tz-kick">Scope: Platform — reaches every tenant with no company-level override.</p>
              {draft ? (
                <>
                  <h4>Changes in this draft</h4>
                  {diff.length ? <ul className="theme-studio-diff">{diff.map((line) => <li key={line}>{line}</li>)}</ul> : <p className="tz-kick">No changes yet.</p>}
                  <label className="field"><span>Reason for this publish</span><input className="input" value={publishReason} onChange={(e) => setPublishReason(e.target.value)} placeholder="e.g. Switch to the brand-blue accent" /></label>
                  <button type="button" className="btn btn-primary" disabled={!publishReason.trim() || publishing || diff.length === 0} onClick={handlePublish}>
                    {publishing ? "Publishing…" : "Publish to everyone"}
                  </button>
                </>
              ) : <p className="tz-kick">No draft yet — change a control to start one.</p>}

              <h4>Version history</h4>
              {history.length ? (
                <table className="table">
                  <thead><tr><th>Version</th><th>Status</th><th>Published</th><th /></tr></thead>
                  <tbody>
                    {history.map((theme) => (
                      <tr key={theme.id}>
                        <td className="tz-num">v{theme.version}</td>
                        <td><span className={`tag ${theme.status === "published" ? "tag-accent" : "tag-neutral"}`}>{theme.status}</span></td>
                        <td className="tz-num">{theme.published_at ? new Date(theme.published_at).toLocaleString() : "—"}</td>
                        <td>{theme.status === "archived" ? <button type="button" className="btn btn-ghost" onClick={() => handleRestore(theme.id)}>Restore as draft</button> : null}</td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              ) : <p className="tz-kick">Nothing published yet.</p>}
            </div>
          ) : null}
        </div>

        <div className="theme-studio-preview">
          <span className="tz-kick">Live preview</span>
          <PreviewPane tokens={t} />
        </div>
      </div>
    </div>
  );
}
