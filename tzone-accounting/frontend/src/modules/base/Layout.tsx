/** The application shell. Its navigation is whatever the installed modules registered. */

import { NavLink, Outlet } from "react-router-dom";
import { useI18n } from "../../core/i18n";
import { getRegistry } from "../../core/registry";
import { useAuth } from "./auth";
import { SyncIndicator } from "./SyncIndicator";

export function Layout() {
  const { t, locale, setLocale } = useI18n();
  const { user, deviceCode, logout } = useAuth();
  const menu = getRegistry().menu;

  const sections = new Map<string, typeof menu>();
  for (const item of menu) {
    const section = item.section ?? "general";
    sections.set(section, [...(sections.get(section) ?? []), item]);
  }

  return (
    <div className="shell">
      <aside className="sidebar">
        <div className="brand">
          <span className="brand-mark">T</span>
          <div>
            <strong>{t("app.name")}</strong>
            <small>{deviceCode ? t("app.terminal", { code: deviceCode }) : t("app.local")}</small>
          </div>
        </div>
        <nav>
          {[...sections.entries()].map(([section, items]) => (
            <div key={section} className="nav-section">
              <span className="nav-section-title">{t(`menu.section.${section}`)}</span>
              {items.map((item) => (
                <NavLink key={item.path} to={item.path} className="nav-link">
                  <span className="nav-icon" aria-hidden>
                    {item.icon}
                  </span>
                  {t(item.labelKey)}
                </NavLink>
              ))}
            </div>
          ))}
        </nav>
      </aside>

      <div className="main">
        <header className="topbar">
          <SyncIndicator />
          <div className="topbar-right">
            <button
              type="button"
              className="ghost"
              onClick={() => setLocale(locale === "ar" ? "en" : "ar")}
            >
              {locale === "ar" ? "English" : "العربية"}
            </button>
            <span className="user-chip">{user?.display_name || user?.username}</span>
            <button type="button" className="ghost" onClick={logout}>
              {t("action.logout")}
            </button>
          </div>
        </header>
        <main className="content">
          <Outlet />
        </main>
      </div>
    </div>
  );
}
