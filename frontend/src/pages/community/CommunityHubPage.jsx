import { ArrowBackOutlined, ChatBubbleOutlined, InsightsOutlined, SendOutlined } from "@mui/icons-material";
import { NavLink, Navigate, Route, Routes, useNavigate } from "react-router-dom";
import PublishPage from "./PublishPage";
import InboxPage from "./InboxPage";
import "./CommunityHubPage.css";

const NAV_ITEMS = [
  { to: "/community/publish", label: "Publish", icon: SendOutlined },
  { to: "/community/inbox", label: "Community", icon: ChatBubbleOutlined },
  { to: "/community/insights", label: "Insights", icon: InsightsOutlined },
];

function InsightsPlaceholder() {
  return (
    <div className="community-coming-soon">
      <InsightsOutlined fontSize="large" />
      <h3>Insights — coming soon</h3>
      <p>Engagement stats per post and per channel will land here next.</p>
    </div>
  );
}

export default function CommunityHubPage() {
  const navigate = useNavigate();

  return (
    <section className="company-settings-shell community-hub-shell">
      <aside className="company-settings-nav">
        <button className="company-settings-back" type="button" onClick={() => navigate("/dashboard")}>
          <ArrowBackOutlined /> Back to platform
        </button>
        <div className="company-settings-nav-heading">
          <span>SOCIAL & COMMUNITY</span>
          <h1>Community</h1>
        </div>
        <nav className="company-settings-nav-scroll">
          {NAV_ITEMS.map((item) => (
            <NavLink key={item.to} to={item.to} className={({ isActive }) => (isActive ? "is-active" : "")}>
              <item.icon fontSize="small" /> {item.label}
            </NavLink>
          ))}
        </nav>
      </aside>

      <main className="company-settings-content">
        <div className="company-settings-content-scroll">
          <Routes>
            <Route path="/" element={<Navigate to="/community/publish" replace />} />
            <Route path="/publish" element={<PublishPage />} />
            <Route path="/inbox" element={<InboxPage />} />
            <Route path="/insights" element={<InsightsPlaceholder />} />
            <Route path="*" element={<Navigate to="/community/publish" replace />} />
          </Routes>
        </div>
      </main>
    </section>
  );
}
