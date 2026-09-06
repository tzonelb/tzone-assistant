import { useEffect, useState } from "react";
import { useNavigate } from "react-router-dom";
import {
  AddOutlined,
  ChatOutlined,
  HubOutlined,
  RefreshOutlined,
} from "@mui/icons-material";
import { getDashboardSummaryRequest } from "../../api/client";
import { EmptyState, ErrorState, LoadingState } from "../../components/common";
import "./DashboardPageV2.css";

// Same single real data source as DashboardPage.jsx (v1) — this is a
// visual rebuild only, matching the mockup's "day at a glance" hero +
// stat strip + two-column panels + channel accounts list.
const STAT_CARDS = [
  ["conversations", "Conversations", (c) => `${c.open_conversations || 0} open now`],
  ["customers", "Customers", () => "Customer records"],
  ["knowledge_items", "Knowledge", () => "Active knowledge items"],
  ["channel_accounts", "Channels", () => "Connected channel accounts"],
  ["products", "Products", () => "Product records"],
  ["open_tickets", "Open tickets", (c) => `${c.tickets || 0} total tickets`],
];

function formatNowLabel() {
  const now = new Date();
  const weekday = now.toLocaleDateString(undefined, { weekday: "long" });
  const date = now.toLocaleDateString(undefined, { day: "numeric", month: "long", year: "numeric" });
  const time = now.toLocaleTimeString(undefined, { hour: "2-digit", minute: "2-digit" });
  return `${weekday}, ${date} · ${time}`;
}

export default function DashboardPageV2() {
  const navigate = useNavigate();
  const [summary, setSummary] = useState(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState("");

  async function loadSummary() {
    setLoading(true);
    setError("");
    try {
      setSummary(await getDashboardSummaryRequest());
    } catch (requestError) {
      setError(requestError.message || "Dashboard information could not be loaded.");
    } finally {
      setLoading(false);
    }
  }

  useEffect(() => { loadSummary(); }, []);

  if (loading) {
    return <div className="tzv2-dash-page"><LoadingState title="Loading company dashboard…" description="Retrieving company, subscription and channel information." /></div>;
  }
  if (error) {
    return (
      <div className="tzv2-dash-page">
        <ErrorState title="Dashboard could not load" description={error} action={<button type="button" className="btn btn-primary" onClick={loadSummary}><RefreshOutlined fontSize="small" /> Try again</button>} />
      </div>
    );
  }

  const counts = summary?.counts || {};
  const subscription = summary?.subscription;
  const conversations = summary?.recent_conversations || [];
  const channels = summary?.channels || [];

  return (
    <div className="tzv2-dash-page">
      <div className="tzv2-dash-hero">
        <div>
          <span className="tz-kick">{formatNowLabel()}</span>
          <h1>The day, at a glance</h1>
          <p>Every channel, every open conversation, and the plan you are running against — one sheet, no scrolling for the numbers that matter.</p>
        </div>
        <div className="tzv2-dash-hero-actions">
          <span className={`tag ${summary?.subscription_active ? "tag-outline" : "tag-neutral"}`}>
            {summary?.subscription_active ? "Subscription active" : "Subscription inactive"}
          </span>
          <button type="button" className="btn btn-secondary" onClick={loadSummary}><RefreshOutlined fontSize="small" /> Refresh</button>
          <button type="button" className="btn btn-primary" onClick={() => navigate("/conversations")}>New conversation</button>
        </div>
      </div>

      <div className="tzv2-dash-stats">
        {STAT_CARDS.map(([key, label, describe]) => (
          <div className="tz-stat" key={key}>
            <span className="tz-kick">{label}</span>
            <div className="tz-fig tzv2-dash-fig">{counts[key] ?? 0}</div>
            <div className="tzv2-dash-stat-note">{describe(counts)}</div>
          </div>
        ))}
      </div>

      <div className="tzv2-dash-columns">
        <section className="card">
          <div className="tzv2-dash-panel-head">
            <div><h3>Recent conversations</h3><span className="tz-kick">Live feed</span></div>
            <button type="button" className="btn btn-ghost" onClick={() => navigate("/conversations")}>View all</button>
          </div>
          {conversations.length ? (
            <div className="tzv2-dash-list">
              {conversations.map((conversation) => (
                <div className="tz-row tzv2-dash-row" key={conversation.id}>
                  <div className="tzv2-dash-avatar">{(conversation.channel || "?").charAt(0).toUpperCase()}</div>
                  <div className="tzv2-dash-row-main">
                    <strong>Customer {conversation.external_user_id}</strong>
                    <span>{conversation.department || "Unassigned"} · {conversation.topic || "No topic"}</span>
                  </div>
                  <span className="tz-kick">{conversation.channel}</span>
                  <span className="tag tag-neutral">{conversation.status}</span>
                </div>
              ))}
            </div>
          ) : (
            <EmptyState icon={<ChatOutlined />} title="No database conversations yet" description="The existing Messenger system remains active. Conversation database synchronization will be connected in the next stage." />
          )}
        </section>

        <section className="card">
          <div className="tzv2-dash-panel-head">
            <div><span className="tz-kick">Current plan</span><h3>{subscription?.plan_name || "No plan"}</h3></div>
            <span className="tag tag-accent">{subscription?.status || "—"}</span>
          </div>
          <div className="tzv2-dash-plan-grid">
            <div><span className="tz-kick">Users</span><div className="tz-fig tzv2-dash-plan-fig">{counts.users || 0} / {subscription?.max_users || 0}</div></div>
            <div><span className="tz-kick">Channels</span><div className="tz-fig tzv2-dash-plan-fig">{counts.channel_accounts || 0} / {subscription?.max_channel_accounts || 0}</div></div>
            <div><span className="tz-kick">AI messages</span><div className="tz-fig tzv2-dash-plan-fig">{subscription?.max_ai_messages || 0}</div></div>
            <div><span className="tz-kick">Expires</span><div className="tz-fig tzv2-dash-plan-fig">{subscription?.expires_at ? new Date(subscription.expires_at).toLocaleDateString() : "—"}</div></div>
          </div>
        </section>
      </div>

      <section className="card">
        <div className="tzv2-dash-panel-head">
          <div><span className="tz-kick">Channel accounts</span><h3>Connected</h3></div>
          <button type="button" className="btn btn-secondary" onClick={() => navigate("/company-settings?section=channels")}><AddOutlined fontSize="small" /> Add channel</button>
        </div>
        {channels.length ? (
          <div className="tzv2-dash-channel-grid">
            {channels.map((channel) => (
              <div className="tz-row tzv2-dash-channel-item" key={channel.id}>
                <span className="tzv2-dash-channel-symbol">{(channel.channel || "?").charAt(0).toUpperCase()}</span>
                <div className="tzv2-dash-row-main"><strong>{channel.name || channel.channel}</strong><span>{channel.channel}</span></div>
                <span className="tag tag-accent">{channel.status}</span>
              </div>
            ))}
          </div>
        ) : (
          <EmptyState icon={<HubOutlined />} title="No channel accounts registered" description="Messenger remains connected through the current webhook. Channel account registration will be added without changing the working connection." />
        )}
      </section>
    </div>
  );
}
