import {
  AdminPanelSettingsOutlined,
  CalendarMonthOutlined,
  ChatOutlined,
  ChecklistOutlined,
  DashboardOutlined,
  ForumOutlined,
  GroupOutlined,
  HubOutlined,
  InsightsOutlined,
  Inventory2Outlined,
  MenuBookOutlined,
  NotificationsOutlined,
  ScheduleSendOutlined,
  SchoolOutlined,
  SettingsOutlined,
  TuneOutlined,
} from "@mui/icons-material";
import { useState } from "react";
import { NavLink } from "react-router-dom";
import tzoneLogo from "../../assets/tzone-logo.png";
import { useAuth } from "../../contexts/AuthContext";

// [path, label, icon, permission]. A null permission means every signed-in
// employee may open it. Hiding a link the API would refuse keeps the navigation
// honest — an employee should not be shown a door that opens onto a 403.
const navigationSections = [
  {
    title: null,
    items: [
      ["/dashboard", "Dashboard", DashboardOutlined, "dashboard.view"],
      ["/notifications", "Notification Center", NotificationsOutlined, null],
    ],
  },
  {
    title: "Customers",
    items: [
      ["/conversations", "Conversations", ChatOutlined, "conversations.view"],
      ["/comments", "Comments", ForumOutlined, "comments.view"],
      ["/customers", "Customers", GroupOutlined, "customers.view"],
      ["/appointments", "Appointments", CalendarMonthOutlined, "appointments.view"],
    ],
  },
  {
    title: "Operations",
    items: [
      ["/tasks", "Tasks", ChecklistOutlined, "tasks.view"],
      ["/catalogue", "Catalogue", Inventory2Outlined, "catalogue.view"],
      ["/scheduler", "Scheduler", ScheduleSendOutlined, "scheduler.view"],
      ["/team-chat", "Team Chat", HubOutlined, "team_chat.use"],
    ],
  },
  {
    title: "Assistant",
    items: [
      ["/knowledge", "Knowledge Base", MenuBookOutlined, "knowledge.view"],
      ["/ai-teaching", "AI Teaching", SchoolOutlined, "settings.view"],
      ["/analytics", "Analytics", InsightsOutlined, "analytics.view"],
    ],
  },
  {
    title: "Administration",
    items: [
      ["/channels", "Channels", TuneOutlined, "channels.view"],
      ["/roles", "Roles & Permissions", AdminPanelSettingsOutlined, "users.manage"],
      ["/company-settings", "Company Settings", SettingsOutlined, "settings.view"],
      ["/settings", "Preferences", SettingsOutlined, null],
    ],
  },
];

export default function Sidebar({ open, collapsed, companyName, onClose }) {
  const [hovered, setHovered] = useState(false);
  const { can } = useAuth();

  const visibleSections = navigationSections
    .map((section) => ({
      ...section,
      items: section.items.filter(
        ([, , , permission]) => !permission || can(permission),
      ),
    }))
    .filter((section) => section.items.length > 0);

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
          {visibleSections.map((section) => (
            <div className="sidebar-section" key={section.title || "primary"}>
              {section.title ? <span className="sidebar-section-title">{section.title}</span> : null}
              {section.items.map(([path, label, Icon]) => (
                <NavLink key={path} to={path} title={collapsed ? label : undefined} className={({ isActive }) => `sidebar-link ${isActive ? "sidebar-link-active" : ""}`} onClick={onClose}>
                  <Icon fontSize="small" /><span>{label}</span>
                </NavLink>
              ))}
            </div>
          ))}
        </nav>
        <div className="sidebar-footer"><span>Customer Workspace</span><strong>V1</strong></div>
      </aside>
    </>
  );
}
