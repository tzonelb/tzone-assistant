import {
  AdminPanelSettingsOutlined,
  AutoAwesomeOutlined,
  CalendarMonthOutlined,
  CallOutlined,
  CampaignOutlined,
  ChatOutlined,
  DashboardOutlined,
  DialpadOutlined,
  EventNoteOutlined,
  ForumOutlined,
  GroupOutlined,
  Inventory2Outlined,
  NotificationsOutlined,
  PaletteOutlined,
  QueryStatsOutlined,
  QuickreplyOutlined,
  SendOutlined,
  SettingsOutlined,
  TaskAltOutlined,
  TuneOutlined,
} from "@mui/icons-material";
import { useEffect, useState } from "react";
import { NavLink } from "react-router-dom";
import { getCurrentUserRequest } from "../../api/client";
import { useNotifications } from "../../contexts/NotificationContext";
import { usePlatformTheme } from "../../contexts/ThemeContext";
import tzoneLogo from "../../assets/tzone-logo.png";
import "./SidebarV2.css";

// Groups match CLAUDE_CODE_UI_IMPLEMENTATION.md §2 exactly. moduleKey ties
// each item to the Theme Studio modules_json visibility/order/label map
// (backend/services/platform_ui_service.py MODULE_KEYS) — an item is
// rendered only when its permission check passes AND the resolved theme
// hasn't hidden it. theme_studio is deliberately absent: the admin
// screen it would link to isn't built yet (Phase 4), and a nav entry
// to a page that doesn't exist is exactly the "fake button" pattern
// this platform's audits have been removing all session.
const NAV_GROUPS = [
  {
    label: "Desk",
    items: [
      ["dashboard", "/dashboard", "Dashboard", DashboardOutlined],
      ["conversations", "/conversations", "Conversations", ChatOutlined],
      ["notifications", "/notifications", "Notification Center", NotificationsOutlined],
      ["tasks", "/tasks", "Tasks", TaskAltOutlined],
      ["appointments", "/appointments", "Appointments", CalendarMonthOutlined, ["modules.appointments"]],
      ["team_chat", "/team-chat", "Team Chat", EventNoteOutlined, ["modules.team_chat"]],
    ],
  },
  {
    label: "Customers",
    items: [
      ["customers", "/customers", "Customers", GroupOutlined],
      ["broadcast", "/broadcast", "Broadcast", CampaignOutlined, ["channels.view"]],
      ["calls", "/calls", "Calls", CallOutlined],
      ["dialer", "/dialer", "Dialer", DialpadOutlined, ["dialer.use"]],
    ],
  },
  {
    label: "Intelligence",
    items: [
      ["test_ai", "/test-ai", "Test & Train AI", AutoAwesomeOutlined],
      ["saved_replies", "/saved-replies", "Saved Replies", QuickreplyOutlined],
    ],
  },
  {
    label: "Growth",
    items: [
      ["publish", "/publish", "Publish", SendOutlined, ["channels.view"]],
      ["comments", "/comments", "Comments", ForumOutlined, ["modules.comments"]],
      ["catalogue", "/catalogue", "Master Catalogue", Inventory2Outlined],
      ["analytics", "/analytics", "Analytics", QueryStatsOutlined, ["analytics.view"]],
    ],
  },
  {
    label: "Administration",
    items: [
      ["company_settings", "/company-settings", "Company Settings", TuneOutlined],
      ["settings", "/settings", "Settings", SettingsOutlined],
    ],
  },
];

export default function SidebarV2({ open, collapsed, companyName, onClose, onToggleCollapsed }) {
  const [hovered, setHovered] = useState(false);
  const [isSuperAdmin, setIsSuperAdmin] = useState(false);
  const [isOwner, setIsOwner] = useState(false);
  const [permissionCodes, setPermissionCodes] = useState([]);
  const { modules } = usePlatformTheme();
  const { unreadCount } = useNotifications();
  const expanded = !collapsed || hovered;

  useEffect(() => {
    let cancelled = false;
    getCurrentUserRequest()
      .then((response) => {
        if (cancelled) return;
        setIsSuperAdmin(Boolean(response?.user?.is_super_admin));
        const activeCompanyId = response?.user?.active_company_id;
        const companies = Array.isArray(response?.companies) ? response.companies : [];
        const active = companies.find((company) => company.id === activeCompanyId) || companies[0];
        setIsOwner(active?.role_code === "owner");
        setPermissionCodes(active?.permission_codes || []);
      })
      .catch(() => {});
    return () => { cancelled = true; };
  }, []);

  function isAllowed(requiredPermissions) {
    return !requiredPermissions || isSuperAdmin || isOwner
      || requiredPermissions.some((code) => permissionCodes.includes(code));
  }

  function isModuleVisible(moduleKey) {
    return modules?.[moduleKey]?.visible !== false;
  }

  const groups = NAV_GROUPS
    .map((group) => ({
      ...group,
      items: group.items.filter(([moduleKey, , , , requiredPermissions]) => (
        isAllowed(requiredPermissions) && isModuleVisible(moduleKey)
      )),
    }))
    .filter((group) => group.items.length > 0);

  return (
    <>
      {open ? <button type="button" className="sidebar-overlay" aria-label="Close navigation" onClick={onClose} /> : null}
      <aside
        className={["sidebar-v2", open ? "sidebar-v2-mobile-open" : "", collapsed && !hovered ? "sidebar-v2-collapsed" : ""].filter(Boolean).join(" ")}
        onMouseEnter={() => setHovered(true)}
        onMouseLeave={() => setHovered(false)}
      >
        <div className="sidebar-v2-brand">
          <button type="button" className="sidebar-v2-collapse-toggle" onClick={onToggleCollapsed} aria-label={collapsed ? "Expand menu" : "Collapse menu"}>
            <img src={tzoneLogo} alt="T-ZONE" className="sidebar-v2-logo" />
          </button>
          {expanded ? (
            <div className="sidebar-v2-brand-copy">
              <span className="sidebar-v2-kicker">Workspace</span>
              <strong>{companyName || "T-ZONE"}</strong>
            </div>
          ) : null}
        </div>

        <nav className="sidebar-v2-nav">
          {groups.map((group) => (
            <div className="sidebar-v2-group" key={group.label}>
              {expanded ? <div className="sidebar-v2-kicker sidebar-v2-group-label">{group.label}</div> : null}
              {group.items.map(([moduleKey, path, defaultLabel, Icon]) => {
                const label = modules?.[moduleKey]?.label || defaultLabel;
                return (
                  <NavLink
                    key={path}
                    to={path}
                    title={!expanded ? label : undefined}
                    className={({ isActive }) => `sidebar-v2-link ${isActive ? "sidebar-v2-link-active" : ""}`}
                    onClick={onClose}
                  >
                    <Icon fontSize="small" />
                    <span>{label}</span>
                    {moduleKey === "notifications" && unreadCount > 0 ? (
                      <span className="sidebar-v2-badge">{unreadCount > 99 ? "99+" : unreadCount}</span>
                    ) : null}
                  </NavLink>
                );
              })}
            </div>
          ))}
          {isSuperAdmin ? (
            <div className="sidebar-v2-group">
              {expanded ? <div className="sidebar-v2-kicker sidebar-v2-group-label">Platform</div> : null}
              <NavLink to="/platform-admin" className={({ isActive }) => `sidebar-v2-link ${isActive ? "sidebar-v2-link-active" : ""}`} onClick={onClose}>
                <AdminPanelSettingsOutlined fontSize="small" />
                <span>Platform Admin</span>
              </NavLink>
              <NavLink to="/platform-admin/theme-studio" className={({ isActive }) => `sidebar-v2-link ${isActive ? "sidebar-v2-link-active" : ""}`} onClick={onClose}>
                <PaletteOutlined fontSize="small" />
                <span>Theme Studio</span>
              </NavLink>
            </div>
          ) : null}
        </nav>

        <div className="sidebar-v2-footer">{expanded ? <span className="sidebar-v2-kicker">Customer Workspace · v2</span> : null}</div>
      </aside>
    </>
  );
}
