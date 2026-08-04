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
import { useAuth } from "../../contexts/AuthContext";

const navigationItems = [
  ["/dashboard", "Dashboard", DashboardOutlined],
  ["/notifications", "Notification Center", NotificationsOutlined],
  ["/conversations", "Conversations", ChatOutlined],
  ["/comments", "Comments", CommentOutlined],
  ["/broadcast", "Broadcast", CampaignOutlined, "channels.view"],
  ["/customers", "Customers", GroupOutlined, "conversations.view"],
  ["/catalogue", "Master Catalogue", Inventory2Outlined, "catalogue.view"],
  ["/ai-teaching", "AI Teaching", AutoAwesomeOutlined, "knowledge.view"],
  ["/tasks", "Tasks", TaskAltOutlined, "tasks.view"],
  ["/scheduler", "Scheduler", ScheduleSendOutlined, "scheduler.view"],
  ["/appointments", "Appointments", CalendarMonthOutlined, "appointments.view"],
  ["/analytics", "Analytics", QueryStatsOutlined, "dashboard.view"],
  ["/team-chat", "Team Chat", EventNoteOutlined],
  ["/settings", "Settings", SettingsOutlined],
  ["/company-settings", "Company Settings", TuneOutlined, "settings.view"],
  ["/roles", "Roles & Permissions", AdminPanelSettingsOutlined, "users.manage"],
];

export default function Sidebar({ open, collapsed, companyName, onClose, onToggleCollapsed }) {
  const [hovered, setHovered] = useState(false);
  const { hasPermission } = useAuth();
  const expanded = !collapsed || hovered;
  const visibleItems = navigationItems.filter(
    ([, , , requiredPermission]) => !requiredPermission || hasPermission(requiredPermission),
  );
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
          {visibleItems.map(([path, label, Icon]) => (
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
