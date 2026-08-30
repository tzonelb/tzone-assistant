import { useEffect, useState } from "react";
import {
  AddOutlined,
  ArrowBackOutlined,
  ChatBubbleOutlined,
  ExpandLessOutlined,
  ExpandMoreOutlined,
  HomeOutlined,
  InsightsOutlined,
  SearchOutlined,
  SendOutlined,
  SettingsOutlined,
} from "@mui/icons-material";
import { NavLink, Navigate, Route, Routes, useNavigate } from "react-router-dom";
import { scheduledPostOptionsRequest, getCurrentUserRequest } from "../../api/client";
import HomePage from "./HomePage";
import PublishPage from "./PublishPage";
import InboxPage from "./InboxPage";
import { channelIcon } from "./channelIcon";
import "../conversations/ConversationInbox.css";
import "./CommunityHubPage.css";

const GLOBAL_NAV_ITEMS = [
  { to: "/community/home", label: "Home", icon: HomeOutlined },
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

function ChannelGroup({ account }) {
  const [expanded, setExpanded] = useState(true);
  const Icon = channelIcon(account.channel);

  return (
    <div className="community-channel-group">
      <button type="button" className="community-channel-group-head" onClick={() => setExpanded((value) => !value)}>
        <Icon fontSize="small" />
        <span>{account.name}</span>
        {expanded ? <ExpandLessOutlined fontSize="small" /> : <ExpandMoreOutlined fontSize="small" />}
      </button>
      {expanded ? (
        <div className="community-channel-group-links">
          <NavLink to={`/community/publish?channel=${account.id}`} className={({ isActive }) => (isActive ? "is-active" : "")}>
            <SendOutlined fontSize="small" /> Publish
          </NavLink>
          <NavLink to={`/community/inbox?channel=${account.id}`} className={({ isActive }) => (isActive ? "is-active" : "")}>
            <ChatBubbleOutlined fontSize="small" /> Community
          </NavLink>
        </div>
      ) : null}
    </div>
  );
}

export default function CommunityHubPage() {
  const navigate = useNavigate();
  const [channelAccounts, setChannelAccounts] = useState([]);
  const [companyName, setCompanyName] = useState("");

  useEffect(() => {
    scheduledPostOptionsRequest()
      .then((result) => setChannelAccounts(Array.isArray(result?.channel_accounts) ? result.channel_accounts : []))
      .catch(() => {});
    getCurrentUserRequest()
      .then((result) => {
        const activeCompanyId = result?.user?.active_company_id;
        const companies = Array.isArray(result?.companies) ? result.companies : [];
        const active = companies.find((company) => company.id === activeCompanyId) || companies[0];
        setCompanyName(active?.name || "");
      })
      .catch(() => {});
  }, []);

  return (
    <section className="company-settings-shell company-settings-locked-layout community-hub-shell">
      <aside className="company-settings-nav">
        <button className="company-settings-back" type="button" onClick={() => navigate("/dashboard")}>
          <ArrowBackOutlined /> Back to platform
        </button>
        <div className="community-hub-new-row">
          <button type="button" onClick={() => navigate("/community/publish?new=1")}>
            <AddOutlined fontSize="small" /> New
          </button>
        </div>
        <nav className="company-settings-nav-scroll">
          {GLOBAL_NAV_ITEMS.map((item) => (
            <NavLink key={item.to} to={item.to} className={({ isActive }) => (isActive ? "is-active" : "")}>
              <item.icon fontSize="small" /> {item.label}
            </NavLink>
          ))}

          <div className="community-channels-heading">
            <span>CHANNELS</span>
            <div className="community-channels-heading-actions">
              <SearchOutlined fontSize="inherit" />
              <button type="button" title="Manage channels" onClick={() => navigate("/company-settings/channels")}>
                <SettingsOutlined fontSize="inherit" />
              </button>
              <button type="button" title="Connect a channel" onClick={() => navigate("/company-settings/channels")}>
                <AddOutlined fontSize="inherit" />
              </button>
            </div>
          </div>

          {channelAccounts.length === 0 ? (
            <button type="button" className="community-channels-empty" onClick={() => navigate("/company-settings/channels")}>
              + Connect a channel
            </button>
          ) : (
            channelAccounts.map((account) => <ChannelGroup key={account.id} account={account} />)
          )}
        </nav>
        <footer className="community-hub-footer">
          <span>{companyName || "My Organization"}</span>
        </footer>
      </aside>

      <main className="company-settings-content">
        <div className="company-settings-content-scroll">
          <Routes>
            <Route path="/" element={<Navigate to="/community/home" replace />} />
            <Route path="/home" element={<HomePage />} />
            <Route path="/publish" element={<PublishPage />} />
            <Route path="/inbox" element={<InboxPage />} />
            <Route path="/insights" element={<InsightsPlaceholder />} />
            <Route path="*" element={<Navigate to="/community/home" replace />} />
          </Routes>
        </div>
      </main>
    </section>
  );
}
