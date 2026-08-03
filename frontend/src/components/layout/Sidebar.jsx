import {
  AdminPanelSettingsOutlined,
  AutoAwesomeOutlined,
  CalendarMonthOutlined,
  CampaignOutlined,
  ChatOutlined,
  CommentOutlined,
  DashboardOutlined,
  EventNoteOutlined,
  GroupOutlined,
  Inventory2Outlined,
  NotificationsOutlined,
  QueryStatsOutlined,
  ScheduleSendOutlined,
  SettingsOutlined,
  TaskAltOutlined,
  TuneOutlined,
} from "@mui/icons-material";
import { useState } from "react";
import { NavLink } from "react-router-dom";
import tzoneLogo from "../../assets/tzone-logo.png";

const navigationItems = [
  ["/dashboard", "Dashboard", DashboardOutlined],
  ["/notifications", "Notification Center", NotificationsOutlined],
  ["/conversations", "Conversations", ChatOutlined],
  ["/comments", "Comments", CommentOutlined],
  ["/broadcast", "Broadcast", CampaignOutlined],
  ["/customers", "Customers", GroupOutlined],
  ["/catalogue", "Master Catalogue", Inventory2Outlined],
  ["/ai-teaching", "AI Teaching", AutoAwesomeOutlined],
  ["/tasks", "Tasks", TaskAltOutlined],
  ["/scheduler", "Scheduler", ScheduleSendOutlined],
  ["/appointments", "Appointments", CalendarMonthOutlined],
  ["/analytics", "Analytics", QueryStatsOutlined],
  ["/team-chat", "Team Chat", EventNoteOutlined],
  ["/settings", "Settings", SettingsOutlined],
  ["/company-settings", "Company Settings", TuneOutlined],
  ["/roles", "Roles & Permissions", AdminPanelSettingsOutlined],
];

export default function Sidebar({ open, collapsed, companyName, onClose, onToggleCollapsed }) {
  const [hovered, setHovered] = useState(false);
  const expanded = !collapsed || hovered;
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
          {navigationItems.map(([path, label, Icon]) => (
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
