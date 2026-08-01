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

const navigationItems = [
  ["/dashboard", "Dashboard", DashboardOutlined],
  ["/notifications", "Notification Center", NotificationsOutlined],
  ["/conversations", "Conversations", ChatOutlined],
  ["/community", "Community", HubOutlined],
  ["/customers", "Customers", GroupOutlined],
  ["/broadcast", "Broadcast", CampaignOutlined],
  ["/calls", "Calls", CallOutlined],
  ["/catalogue", "Master Catalogue", Inventory2Outlined],
  ["/ai-teaching", "AI Teaching", AutoAwesomeOutlined],
  ["/tasks", "Tasks", TaskAltOutlined],
  ["/saved-replies", "Saved Replies", QuickreplyOutlined],
  ["/appointments", "Appointments", CalendarMonthOutlined],
  ["/analytics", "Analytics", QueryStatsOutlined],
  ["/team-chat", "Team Chat", EventNoteOutlined],
  ["/settings", "Settings", SettingsOutlined],
  ["/company-settings", "Company Settings", TuneOutlined],
  ["/roles", "Roles & Permissions", AdminPanelSettingsOutlined],
];

export default function Sidebar({ open, collapsed, companyName, onClose, onToggleCollapsed }) {
  const [hovered, setHovered] = useState(false);
  const [isSuperAdmin, setIsSuperAdmin] = useState(false);
  const expanded = !collapsed || hovered;

  useEffect(() => {
    let cancelled = false;
    getCurrentUserRequest()
      .then((response) => {
        if (!cancelled) setIsSuperAdmin(Boolean(response?.user?.is_super_admin));
      })
      .catch(() => {
        // Not fatal — the API already enforces this server-side either way.
      });
    return () => { cancelled = true; };
  }, []);

  const items = isSuperAdmin
    ? [...navigationItems, ["/platform-admin", "Platform Admin", AdminPanelSettingsOutlined]]
    : navigationItems;

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
