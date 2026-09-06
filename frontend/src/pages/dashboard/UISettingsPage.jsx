import { ArrowBackOutlined, SearchOutlined } from "@mui/icons-material";
import { useEffect, useMemo, useState } from "react";
import { useNavigate } from "react-router-dom";
import { useAuth } from "../../contexts/AuthContext";
import { readNotificationPreferences, saveNotificationPreferences } from "../../utils/notificationPreferences";
import { getNotificationPreferencesRequest, updateNotificationPreferencesRequest, twoFactorStatusRequest, twoFactorEnrollStartRequest, twoFactorEnrollConfirmRequest, twoFactorDisableRequest } from "../../api/client";
import { SUPPORTED_CHANNELS } from "../../utils/channels";

// The one place this list is decided. The design writes the four channels out
// again and ends with `website: "Website"` — a toggle for a channel this
// platform has never been able to connect, switching a notification no message
// could ever produce. `tests/test_channel_catalogue.py` fails the build on it
// by name (`test_website_is_gone_from_the_frontend`), which is why this is the
// one line of the design's page not taken verbatim: the rest of the file is
// byte-identical to the design branch so it can be re-synced.
const CHANNEL_LABELS = Object.fromEntries(
  SUPPORTED_CHANNELS.map((channel) => [
    channel,
    channel === "whatsapp"
      ? "WhatsApp"
      : channel.charAt(0).toUpperCase() + channel.slice(1),
  ]),
);

const DEFAULT_CATEGORY_PREFERENCES = {
  notify_new_message: "all",
  notify_ai_escalation: true,
  notify_mentions: true,
  notify_tasks: true,
};

const FONT_OPTIONS = [
  { value: "Inter, system-ui, sans-serif", label: "Inter / System" },
  { value: "Arial, sans-serif", label: "Arial" },
  { value: "Tahoma, sans-serif", label: "Tahoma (Arabic friendly)" },
  { value: "'Segoe UI', sans-serif", label: "Segoe UI" },
];
const SECTIONS = [
  ["appearance", "Appearance"],
  ["notifications", "Notifications & sound"],
  ["language", "Language & region"],
  ["session", "Session & security"],
];
function Toggle({ checked, onChange }) { return <button type="button" className={checked ? "settings-toggle settings-toggle-on" : "settings-toggle"} aria-pressed={checked} onClick={() => onChange(!checked)}><span /></button>; }

export default function UISettingsPage() {
  const navigate = useNavigate();
  const { user } = useAuth();
  const [query, setQuery] = useState("");
  const [active, setActive] = useState("appearance");
  const [fontFamily, setFontFamily] = useState(() => localStorage.getItem("tzone_ui_font") || FONT_OPTIONS[0].value);
  const [fontSize, setFontSize] = useState(() => Number(localStorage.getItem("tzone_ui_font_size") || 100));
  const [density, setDensity] = useState(() => localStorage.getItem("tzone_ui_density") || "comfortable");
  const [theme, setTheme] = useState(() => localStorage.getItem("tzone_ui_theme") || "light");
  const [language, setLanguage] = useState(() => localStorage.getItem("tzone_ui_language") || "en");
  const [timezone, setTimezone] = useState(() => localStorage.getItem("tzone_ui_timezone") || "Asia/Beirut");
  const [autoLogout, setAutoLogout] = useState(() => localStorage.getItem("tzone_auto_logout") || "30");
  const [notifications, setNotifications] = useState(() => readNotificationPreferences(user));
  const [categoryPrefs, setCategoryPrefs] = useState(DEFAULT_CATEGORY_PREFERENCES);
  const [saved, setSaved] = useState(false);
  const [saving, setSaving] = useState(false);
  const [saveError, setSaveError] = useState("");

  const [twoFactorEnabled, setTwoFactorEnabled] = useState(false);
  const [twoFactorEnroll, setTwoFactorEnroll] = useState(null); // { secret, otpauth_uri }
  const [twoFactorCode, setTwoFactorCode] = useState("");
  const [disablePassword, setDisablePassword] = useState("");
  const [disableCode, setDisableCode] = useState("");
  const [showDisable, setShowDisable] = useState(false);
  const [twoFactorError, setTwoFactorError] = useState("");
  const [twoFactorBusy, setTwoFactorBusy] = useState(false);

  useEffect(() => {
    twoFactorStatusRequest()
      .then((data) => setTwoFactorEnabled(Boolean(data?.enabled)))
      .catch(() => {});
  }, []);

  async function startTwoFactorEnroll() {
    setTwoFactorError("");
    setTwoFactorBusy(true);
    try {
      const data = await twoFactorEnrollStartRequest();
      setTwoFactorEnroll(data);
      setTwoFactorCode("");
    } catch (e) {
      setTwoFactorError(e.message || "Could not start setup.");
    } finally {
      setTwoFactorBusy(false);
    }
  }

  async function confirmTwoFactorEnroll() {
    setTwoFactorError("");
    setTwoFactorBusy(true);
    try {
      await twoFactorEnrollConfirmRequest(twoFactorCode.trim());
      setTwoFactorEnabled(true);
      setTwoFactorEnroll(null);
      setTwoFactorCode("");
    } catch (e) {
      setTwoFactorError(e.message || "Invalid code.");
    } finally {
      setTwoFactorBusy(false);
    }
  }

  async function submitDisableTwoFactor() {
    setTwoFactorError("");
    setTwoFactorBusy(true);
    try {
      await twoFactorDisableRequest(disablePassword, disableCode.trim());
      setTwoFactorEnabled(false);
      setShowDisable(false);
      setDisablePassword("");
      setDisableCode("");
    } catch (e) {
      setTwoFactorError(e.message || "Could not disable two-factor authentication.");
    } finally {
      setTwoFactorBusy(false);
    }
  }

  useEffect(() => {
    let active = true;
    getNotificationPreferencesRequest()
      .then((data) => { if (active && data) setCategoryPrefs({ ...DEFAULT_CATEGORY_PREFERENCES, ...data }); })
      .catch(() => {});
    return () => { active = false; };
  }, []);

  useEffect(() => {
    const resolvedTheme = theme === "auto"
      ? (window.matchMedia("(prefers-color-scheme: dark)").matches ? "dark" : "light")
      : theme;
    document.documentElement.style.fontFamily = fontFamily;
    document.documentElement.style.fontSize = `${fontSize}%`;
    document.body.dataset.uiDensity = density;
    document.documentElement.dataset.theme = resolvedTheme;
    document.documentElement.style.colorScheme = resolvedTheme;
    localStorage.setItem("tzone_ui_font", fontFamily);
    localStorage.setItem("tzone_ui_font_size", String(fontSize));
    localStorage.setItem("tzone_ui_density", density);
    localStorage.setItem("tzone_ui_theme", theme);
    window.dispatchEvent(new CustomEvent("tzone:appearance-changed"));
  }, [fontFamily, fontSize, density, theme]);

  const visibleSections = useMemo(() => SECTIONS.filter(([, label]) => label.toLowerCase().includes(query.toLowerCase())), [query]);
  async function saveAll() {
    setSaving(true);
    setSaveError("");
    try {
      await updateNotificationPreferencesRequest(categoryPrefs);
      saveNotificationPreferences(user, notifications);
      localStorage.setItem("tzone_ui_language", language);
      localStorage.setItem("tzone_ui_timezone", timezone);
      localStorage.setItem("tzone_auto_logout", autoLogout);
      window.dispatchEvent(new CustomEvent("tzone:notification-settings-changed", { detail: notifications }));
      window.dispatchEvent(new CustomEvent("tzone:timezone-changed", { detail: timezone }));
      setSaved(true);
    } catch (requestError) {
      setSaveError(requestError.message || "Could not save settings.");
    } finally {
      setSaving(false);
    }
  }
  async function requestBrowserPermission() { if ("Notification" in window) await Notification.requestPermission(); }
  const notificationRows = [
    ["enabled", "Enable notifications", "Keep badges and activity notifications enabled."],
    ["inAppPopup", "In-app popup", "Show one alert inside T-ZONE."],
    ["desktop", "Windows / desktop notification", "Use browser and Windows notifications."],
    ["sound", "Notification sound", "Play a sound for allowed notifications."],
    ["suppressActiveConversation", "Silence the open conversation", "Do not popup for the conversation currently on screen."],
    ["groupRepeated", "Group repeated messages", "Combine repeated notifications from the same customer."],
    ["showPreview", "Show message preview", "Include message text inside popup notifications."],
  ];

  return (
    <section className="user-settings-shell">
      <aside className="user-settings-nav">
        <button type="button" className="company-settings-back" onClick={() => navigate(-1)}><ArrowBackOutlined /> Back to platform</button>
        <div className="company-settings-nav-heading"><span>PERSONAL CONTROL</span><h1>User Settings</h1></div>
        <label className="settings-search"><SearchOutlined /><input value={query} onChange={(e) => setQuery(e.target.value)} placeholder="Search settings..." /></label>
        <nav>{visibleSections.map(([id, label]) => <button type="button" key={id} className={active === id ? "is-active" : ""} onClick={() => setActive(id)}>{label}</button>)}</nav>
      </aside>

      <main className="user-settings-content">
        <div className="user-settings-content-scroll">
          <header><span>USER SETTINGS</span><h2>{SECTIONS.find(([id]) => id === active)?.[1]}</h2><p>Personal preferences for your own account and workspace.</p></header>

          {active === "appearance" ? <section className="settings-section-card"><h3>Appearance</h3><p>Theme, font and display density.</p><div className="settings-form-grid"><label><strong>Theme</strong><select value={theme} onChange={(e) => setTheme(e.target.value)}><option value="light">Light</option><option value="dark">Dark</option><option value="auto">System</option></select></label><label><strong>Font</strong><select value={fontFamily} onChange={(e) => setFontFamily(e.target.value)}>{FONT_OPTIONS.map((item) => <option value={item.value} key={item.value}>{item.label}</option>)}</select></label><label><strong>Text size: {fontSize}%</strong><input type="range" min="85" max="125" step="5" value={fontSize} onChange={(e) => setFontSize(Number(e.target.value))} /></label><label><strong>Density</strong><select value={density} onChange={(e) => setDensity(e.target.value)}><option value="comfortable">Comfortable</option><option value="compact">Compact</option></select></label></div></section> : null}

          {active === "notifications" ? <section className="settings-section-card"><h3>Notifications & sound</h3><p>Choose which notifications appear and how they are delivered.</p><div className="settings-notification-list">{notificationRows.map(([key, title, description]) => <div className="settings-notification-row" key={key}><div><strong>{title}</strong><span>{description}</span></div><Toggle checked={notifications[key]} onChange={(value) => { setNotifications((current) => ({ ...current, [key]: value })); setSaved(false); }} /></div>)}</div><h3>Channels</h3><div className="settings-notification-list">{Object.entries(CHANNEL_LABELS).map(([key, label]) => <div className="settings-notification-row" key={key}><div><strong>{label}</strong><span>Receive notifications from this channel.</span></div><Toggle checked={notifications.channels?.[key] !== false} onChange={(value) => { setNotifications((current) => ({ ...current, channels: { ...(current.channels || {}), [key]: value } })); setSaved(false); }} /></div>)}</div><h3>Notify me about</h3><p>Choose which activity creates a notification for your account. Everyone on the team can tune this to their own taste.</p><div className="settings-notification-list"><div className="settings-notification-row"><div><strong>New customer messages</strong><span>Get pinged while the AI is handling a conversation, or turn it off.</span></div><select value={categoryPrefs.notify_new_message} onChange={(e) => { setCategoryPrefs((current) => ({ ...current, notify_new_message: e.target.value })); setSaved(false); }}><option value="all">Every conversation</option><option value="none">Off</option></select></div>{[["notify_ai_escalation", "AI asked for human help", "The AI couldn't resolve something and handed off to a person."], ["notify_mentions", "Mentions in notes", "A teammate @mentioned you on a conversation note."], ["notify_tasks", "Tasks", "Task assignments and reminders."]].map(([key, title, description]) => <div className="settings-notification-row" key={key}><div><strong>{title}</strong><span>{description}</span></div><Toggle checked={categoryPrefs[key] !== false} onChange={(value) => { setCategoryPrefs((current) => ({ ...current, [key]: value })); setSaved(false); }} /></div>)}</div><button type="button" className="secondary-action" onClick={requestBrowserPermission}>Enable browser permission</button></section> : null}

          {active === "language" ? <section className="settings-section-card"><h3>Language & region</h3><p>The selected timezone controls all conversation, notification and timeline timestamps. The dashboard's own screens (buttons, labels) stay in English regardless of this setting — only the AI's replies to customers already adapt to their language automatically.</p><div className="settings-form-grid"><label><strong>Language</strong><select value={language} onChange={(e) => { setLanguage(e.target.value); setSaved(false); }}><option value="en">English</option><option value="ar">Arabic</option><option value="tr">Turkish</option></select></label><label><strong>Timezone</strong><select value={timezone} onChange={(e) => { setTimezone(e.target.value); setSaved(false); }}><option value="Asia/Beirut">Beirut</option><option value="Asia/Qatar">Qatar</option><option value="UTC">UTC</option></select></label></div></section> : null}

          {active === "session" ? <section className="settings-section-card"><h3>Session & security</h3><p>Automatic logout and account security.</p><div className="settings-form-grid"><label><strong>Auto logout after inactivity</strong><select value={autoLogout} onChange={(e) => { setAutoLogout(e.target.value); setSaved(false); }}><option value="10">10 minutes</option><option value="30">30 minutes</option><option value="60">1 hour</option><option value="never">Never</option></select></label><button type="button" className="secondary-action" disabled title="Not built yet - contact T-ZONE support to reset your password">Change password (coming soon)</button><button type="button" className="secondary-action" disabled title="Not built yet">View active sessions (coming soon)</button></div>
            <div className="settings-2fa" style={{ marginTop: "1.5rem", borderTop: "1px solid rgba(0,0,0,0.08)", paddingTop: "1.25rem" }}>
              <h3>Two-factor authentication</h3>
              <p>Add a one-time code from an authenticator app (Google Authenticator, Authy, 1Password) to every sign-in.</p>
              <p><strong>Status: </strong>{twoFactorEnabled ? "Enabled" : "Disabled"}</p>
              {twoFactorError ? <div className="login-error" style={{ marginBottom: "0.75rem" }}>{twoFactorError}</div> : null}

              {!twoFactorEnabled && !twoFactorEnroll ? (
                <button type="button" className="primary-action" disabled={twoFactorBusy} onClick={startTwoFactorEnroll}>Enable two-factor authentication</button>
              ) : null}

              {!twoFactorEnabled && twoFactorEnroll ? (
                <div className="settings-form-grid">
                  <p>Add this account to your authenticator app using manual key entry:</p>
                  <label><strong>Setup key (base32)</strong><input type="text" readOnly value={twoFactorEnroll.secret} onFocus={(e) => e.target.select()} /></label>
                  <label><strong>otpauth URI</strong><input type="text" readOnly value={twoFactorEnroll.otpauth_uri} onFocus={(e) => e.target.select()} /></label>
                  <label><strong>Enter the 6-digit code to confirm</strong><input type="text" inputMode="numeric" maxLength={6} value={twoFactorCode} placeholder="123456" onChange={(e) => setTwoFactorCode(e.target.value.replace(/\D/g, ""))} /></label>
                  <div>
                    <button type="button" className="primary-action" disabled={twoFactorBusy || twoFactorCode.length !== 6} onClick={confirmTwoFactorEnroll}>Confirm & activate</button>
                    <button type="button" className="secondary-action" onClick={() => { setTwoFactorEnroll(null); setTwoFactorError(""); }}>Cancel</button>
                  </div>
                </div>
              ) : null}

              {twoFactorEnabled && !showDisable ? (
                <button type="button" className="secondary-action" onClick={() => { setShowDisable(true); setTwoFactorError(""); }}>Disable two-factor authentication</button>
              ) : null}

              {twoFactorEnabled && showDisable ? (
                <div className="settings-form-grid">
                  <p>Confirm your password and a current authenticator code to disable.</p>
                  <label><strong>Password</strong><input type="password" value={disablePassword} onChange={(e) => setDisablePassword(e.target.value)} /></label>
                  <label><strong>Authentication code</strong><input type="text" inputMode="numeric" maxLength={6} value={disableCode} placeholder="123456" onChange={(e) => setDisableCode(e.target.value.replace(/\D/g, ""))} /></label>
                  <div>
                    <button type="button" className="primary-action" disabled={twoFactorBusy || disableCode.length !== 6 || !disablePassword} onClick={submitDisableTwoFactor}>Confirm disable</button>
                    <button type="button" className="secondary-action" onClick={() => { setShowDisable(false); setDisablePassword(""); setDisableCode(""); setTwoFactorError(""); }}>Cancel</button>
                  </div>
                </div>
              ) : null}
            </div>
            </section> : null}

          <div className="settings-save-bar">
            {saveError ? <span style={{ color: "#c0392b", marginRight: 12 }}>{saveError}</span> : null}
            <button type="button" className="primary-action" disabled={saving} onClick={saveAll}>{saving ? "Saving..." : saved ? "Settings saved" : "Save settings"}</button>
          </div>
        </div>
      </main>
    </section>
  );
}
