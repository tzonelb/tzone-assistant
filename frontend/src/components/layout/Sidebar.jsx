import {
  AdminPanelSettingsOutlined,
  CalendarMonthOutlined,
  CampaignOutlined,
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
import { useWorkspaceConfig } from "../../contexts/WorkspaceConfigContext";

// [path, label, icon, permission, module]. A null permission means every
// signed-in employee may open it. The module key is the switch the platform
// administrator controls; both must allow the link, because both are enforced
// by the API. Hiding a link the API would refuse keeps the navigation honest —
// an employee should not be shown a door that opens onto a 403.
const navigationSections = [
  {
    title: null,
    items: [
      ["/dashboard", "Dashboard", DashboardOutlined, "dashboard.view", "dashboard"],
      ["/notifications", "Notification Center", NotificationsOutlined, null, "notifications"],
    ],
  },
  {
    title: "Customers",
    items: [
      ["/conversations", "Conversations", ChatOutlined, "conversations.view", "conversations"],
      ["/comments", "Comments", ForumOutlined, "comments.view", "comments"],
      ["/customers", "Customers", GroupOutlined, "customers.view", "customers"],
      ["/broadcast", "Broadcast", CampaignOutlined, "channels.view", "broadcast"],
      ["/appointments", "Appointments", CalendarMonthOutlined, "appointments.view", "appointments"],
    ],
  },
  {
    title: "Operations",
    items: [
      ["/tasks", "Tasks", ChecklistOutlined, "tasks.view", "tasks"],
      ["/catalogue", "Catalogue", Inventory2Outlined, "catalogue.view", "catalogue"],
      ["/scheduler", "Scheduler", ScheduleSendOutlined, "scheduler.view", "scheduler"],
      ["/team-chat", "Team Chat", HubOutlined, "team_chat.use", "team_chat"],
    ],
  },
  {
    title: "Assistant",
    items: [
      ["/knowledge", "Knowledge Base", MenuBookOutlined, "knowledge.view", "knowledge"],
      ["/ai-teaching", "AI Teaching", SchoolOutlined, "settings.view", "ai_teaching"],
      ["/analytics", "Analytics", InsightsOutlined, "analytics.view", "analytics"],
    ],
  },
  {
    title: "Administration",
    items: [
      ["/channels", "Channels", TuneOutlined, "channels.view", "channels"],
      ["/roles", "Roles & Permissions", AdminPanelSettingsOutlined, "users.manage", "roles"],
      ["/company-settings", "Company Settings", SettingsOutlined, "settings.view", "company_settings"],
      ["/settings", "Preferences", SettingsOutlined, null, "preferences"],
    ],
  },
];

export default function Sidebar({ open, collapsed, companyName, onClose }) {
  const [hovered, setHovered] = useState(false);
  const { can } = useAuth();
  const { branding, moduleEnabled } = useWorkspaceConfig();

  const visibleSections = navigationSections
    .map((section) => ({
      ...section,
      items: section.items.filter(
        ([, , , permission, module]) =>
          (!permission || can(permission)) && moduleEnabled(module),
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
          <div className="sidebar-logo-shell"><img src={branding?.logo_url || tzoneLogo} alt={branding?.brand_name || "T-ZONE"} className="sidebar-logo" /></div>
          <div className="sidebar-brand-copy"><strong>{branding?.brand_name || "T-ZONE"}</strong><span>{branding?.tagline || companyName || "Platform"}</span></div>
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
