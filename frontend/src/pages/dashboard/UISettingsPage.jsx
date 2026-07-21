import { ArrowBackOutlined, SearchOutlined } from "@mui/icons-material";
import { useEffect, useMemo, useState } from "react";
import { useNavigate } from "react-router-dom";
import { useAuth } from "../../contexts/AuthContext";
import { readNotificationPreferences, saveNotificationPreferences } from "../../utils/notificationPreferences";

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
  const [saved, setSaved] = useState(false);

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
  function saveAll() {
    saveNotificationPreferences(user, notifications);
    localStorage.setItem("tzone_ui_language", language);
    localStorage.setItem("tzone_ui_timezone", timezone);
    localStorage.setItem("tzone_auto_logout", autoLogout);
    window.dispatchEvent(new CustomEvent("tzone:notification-settings-changed", { detail: notifications }));
    window.dispatchEvent(new CustomEvent("tzone:timezone-changed", { detail: timezone }));
    setSaved(true);
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

          {active === "notifications" ? <section className="settings-section-card"><h3>Notifications & sound</h3><p>Choose which notifications appear and how they are delivered.</p><div className="settings-notification-list">{notificationRows.map(([key, title, description]) => <div className="settings-notification-row" key={key}><div><strong>{title}</strong><span>{description}</span></div><Toggle checked={notifications[key]} onChange={(value) => { setNotifications((current) => ({ ...current, [key]: value })); setSaved(false); }} /></div>)}</div><h3>Channels</h3><div className="settings-notification-list">{Object.entries({ messenger: "Messenger", whatsapp: "WhatsApp", instagram: "Instagram", telegram: "Telegram", website: "Website" }).map(([key, label]) => <div className="settings-notification-row" key={key}><div><strong>{label}</strong><span>Receive notifications from this channel.</span></div><Toggle checked={notifications.channels?.[key] !== false} onChange={(value) => { setNotifications((current) => ({ ...current, channels: { ...(current.channels || {}), [key]: value } })); setSaved(false); }} /></div>)}</div><button type="button" className="secondary-action" onClick={requestBrowserPermission}>Enable browser permission</button></section> : null}

          {active === "language" ? <section className="settings-section-card"><h3>Language & region</h3><p>The selected timezone controls all conversation, notification and timeline timestamps.</p><div className="settings-form-grid"><label><strong>Language</strong><select value={language} onChange={(e) => setLanguage(e.target.value)}><option value="en">English</option><option value="ar">Arabic</option><option value="tr">Turkish</option></select></label><label><strong>Timezone</strong><select value={timezone} onChange={(e) => { setTimezone(e.target.value); setSaved(false); }}><option value="Asia/Beirut">Beirut</option><option value="Asia/Qatar">Qatar</option><option value="UTC">UTC</option></select></label></div></section> : null}

          {active === "session" ? <section className="settings-section-card"><h3>Session & security</h3><p>Automatic logout and account security.</p><div className="settings-form-grid"><label><strong>Auto logout after inactivity</strong><select value={autoLogout} onChange={(e) => setAutoLogout(e.target.value)}><option value="10">10 minutes</option><option value="30">30 minutes</option><option value="60">1 hour</option><option value="never">Never</option></select></label><button type="button" className="secondary-action">Change password</button><button type="button" className="secondary-action">View active sessions</button></div></section> : null}

          <div className="settings-save-bar"><button type="button" className="primary-action" onClick={saveAll}>{saved ? "Settings saved" : "Save settings"}</button></div>
        </div>
      </main>
    </section>
  );
}
