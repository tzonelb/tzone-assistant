import {
  AdminPanelSettingsOutlined,
  AutoAwesomeOutlined,
  CalendarMonthOutlined,
  CallOutlined,
  CampaignOutlined,
  ChatOutlined,
  DashboardOutlined,
  EventNoteOutlined,
  GroupOutlined,
  HubOutlined,
  Inventory2Outlined,
  NotificationsOutlined,
  QuickreplyOutlined,
  QueryStatsOutlined,
  SettingsOutlined,
  TaskAltOutlined,
  TuneOutlined,
} from "@mui/icons-material";
import { useEffect, useState } from "react";
import { NavLink } from "react-router-dom";
import { getCurrentUserRequest } from "../../api/client";
import tzoneLogo from "../../assets/tzone-logo.png";

// A nav item with `requiredPermissions` is hidden unless the user holds
// at least one of the listed permission codes (or is owner/super admin) -
// otherwise it leads to a page that just 403s on every real action.
const navigationItems = [
  ["/dashboard", "Dashboard", DashboardOutlined],
  ["/notifications", "Notification Center", NotificationsOutlined],
  ["/conversations", "Conversations", ChatOutlined],
  ["/community", "Community", HubOutlined, ["channels.view", "modules.comments"]],
  ["/customers", "Customers", GroupOutlined],
  ["/broadcast", "Broadcast", CampaignOutlined, ["channels.view"]],
  ["/calls", "Calls", CallOutlined],
  ["/catalogue", "Master Catalogue", Inventory2Outlined],
  ["/ai-teaching", "AI Teaching", AutoAwesomeOutlined],
  ["/tasks", "Tasks", TaskAltOutlined],
  ["/saved-replies", "Saved Replies", QuickreplyOutlined],
  ["/appointments", "Appointments", CalendarMonthOutlined, ["modules.appointments"]],
  ["/analytics", "Analytics", QueryStatsOutlined, ["analytics.view"]],
  ["/team-chat", "Team Chat", EventNoteOutlined, ["modules.team_chat"]],
  ["/settings", "Settings", SettingsOutlined],
  // Reply Flows and Roles & Permissions are admin-only and live inside
  // Company Settings, not as standalone nav links.
  ["/company-settings", "Company Settings", TuneOutlined],
];

export default function Sidebar({ open, collapsed, companyName, onClose, onToggleCollapsed }) {
  const [hovered, setHovered] = useState(false);
  const [isSuperAdmin, setIsSuperAdmin] = useState(false);
  const [isOwner, setIsOwner] = useState(false);
  const [permissionCodes, setPermissionCodes] = useState([]);
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
      .catch(() => {
        // Not fatal — the API already enforces this server-side either way.
      });
    return () => { cancelled = true; };
  }, []);

  let items = navigationItems.filter(([, , , requiredPermissions]) => (
    !requiredPermissions
    || isSuperAdmin
    || isOwner
    || requiredPermissions.some((code) => permissionCodes.includes(code))
  ));
  if (isSuperAdmin) {
    items = [...items, ["/platform-admin", "Platform Admin", AdminPanelSettingsOutlined]];
  }

  return (
    <>
      {open ? <button type="button" className="sidebar-overlay" aria-label="Close navigation" onClick={onClose} /> : null}
      <aside
        className={["sidebar", open ? "sidebar-mobile-open" : "", collapsed ? "sidebar-collapsed" : "", collapsed && hovered ? "sidebar-hover-expanded" : ""].filter(Boolean).join(" ")}
        onMouseEnter={() => setHovered(true)}
        onMouseLeave={() => setHovered(false)}
      >
        <div className="sidebar-brand">
          <div className="sidebar-logo-shell"><img src={tzoneLogo} alt="T-ZONE" className="sidebar-logo" /></div>
          <div className="sidebar-brand-copy"><strong>T-ZONE</strong><span>{companyName || "Platform"}</span></div>
        </div>
        <nav className="sidebar-navigation">
          {items.map(([path, label, Icon]) => (
            <NavLink key={path} to={path} title={collapsed ? label : undefined} className={({ isActive }) => `sidebar-link ${isActive ? "sidebar-link-active" : ""}`} onClick={onClose}>
              <Icon fontSize="small" /><span>{label}</span>
            </NavLink>
          ))}
        </nav>
        <div className="sidebar-footer"><span>Customer Workspace</span><strong>V1</strong></div>
      </aside>
    </>
  );
}
