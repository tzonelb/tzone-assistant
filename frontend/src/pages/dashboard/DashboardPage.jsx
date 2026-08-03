import { useEffect, useState } from "react";
import { useNavigate } from "react-router-dom";
import {
  AddOutlined,
  ChatOutlined,
  GroupOutlined,
  HubOutlined,
  Inventory2Outlined,
  MenuBookOutlined,
  RefreshOutlined,
  SupportAgentOutlined,
} from "@mui/icons-material";

import { getDashboardSummaryRequest } from "../../api/client";
import {
  AppCard,
  EmptyState,
  ErrorState,
  LoadingState,
  PageHeader,
  StatusBadge,
} from "../../components/common";


function StatCard({
  title,
  value,
  description,
  icon: Icon,
}) {
  return (
    <AppCard
      padding="medium"
      hoverable
      className="stat-card"
    >
      <div className="stat-icon">
        <Icon />
      </div>

      <div>
        <span>{title}</span>
        <strong>{value ?? 0}</strong>
        <small>{description}</small>
      </div>
    </AppCard>
  );
}


export default function DashboardPage() {
  const navigate = useNavigate();
  const [summary, setSummary] = useState(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState("");

  async function loadSummary() {
    setLoading(true);
    setError("");

    try {
      const result =
        await getDashboardSummaryRequest();

      setSummary(result);
    } catch (requestError) {
      setError(
        requestError.message ||
        "Dashboard information could not be loaded.",
      );
    } finally {
      setLoading(false);
    }
  }

  useEffect(() => {
    loadSummary();
  }, []);

  if (loading) {
    return (
      <AppCard padding="medium">
        <LoadingState
          title="Loading company dashboard..."
          description="Retrieving company, subscription and channel information."
        />
      </AppCard>
    );
  }

  if (error) {
    return (
      <AppCard padding="medium">
        <ErrorState
          title="Dashboard could not load"
          description={error}
          action={
            <button
              type="button"
              className="btn btn-primary"
              onClick={loadSummary}
            >
              <RefreshOutlined fontSize="small" /> Try again
            </button>
          }
        />
      </AppCard>
    );
  }

  const counts = summary?.counts || {};
  const subscription = summary?.subscription;
  const conversations =
    summary?.recent_conversations || [];
  const channels = summary?.channels || [];

  return (
    <div className="dashboard-page">
      <PageHeader
        actions={
          <>
            <button
              type="button"
              className="btn btn-secondary"
              onClick={loadSummary}
            >
              <RefreshOutlined fontSize="small" /> Refresh
            </button>
          </>
        }
      />

      <section className="dashboard-hero">
        <div>
          <span>PLATFORM STATUS</span>

          <h2>
            Everything important in one place
          </h2>

          <p>
            View your company activity, connected services,
            subscription status and recent customer conversations.
          </p>
        </div>

        <StatusBadge
          status={
            summary?.subscription_active
              ? "active"
              : "inactive"
          }
          label={
            summary?.subscription_active
              ? "Subscription active"
              : "Subscription inactive"
          }
        />
      </section>

      <section className="statistics-grid">
        <StatCard
          title="Conversations"
          value={counts.conversations}
          description={`${counts.open_conversations || 0} currently open`}
          icon={ChatOutlined}
        />

        <StatCard
          title="Customers"
          value={counts.customers}
          description="Customer records"
          icon={GroupOutlined}
        />

        <StatCard
          title="Knowledge"
          value={counts.knowledge_items}
          description="Active knowledge items"
          icon={MenuBookOutlined}
        />

        <StatCard
          title="Channels"
          value={counts.channel_accounts}
          description="Connected channel accounts"
          icon={HubOutlined}
        />

        <StatCard
          title="Products"
          value={counts.products}
          description="Product records"
          icon={Inventory2Outlined}
        />

        <StatCard
          title="Open tickets"
          value={counts.open_tickets}
          description={`${counts.tickets || 0} total tickets`}
          icon={SupportAgentOutlined}
        />
      </section>

      <section className="dashboard-columns">
        <AppCard
          padding="medium"
          className="dashboard-panel"
        >
          <div className="panel-title">
            <div>
              <span>RECENT ACTIVITY</span>
              <h3>Recent conversations</h3>
            </div>

            <button
              type="button"
              className="btn btn-ghost"
              onClick={() => navigate("/conversations")}
            >
              View all
            </button>
          </div>

          {conversations.length ? (
            <div className="conversation-list">
              {conversations.map((conversation) => (
                <div
                  className="conversation-item"
                  key={conversation.id}
                >
                  <div className="conversation-avatar">
                    {(conversation.channel || "?")
                      .charAt(0)
                      .toUpperCase()}
                  </div>

                  <div>
                    <strong>
                      Customer{" "}
                      {conversation.external_user_id}
                    </strong>

                    <span>
                      {conversation.department || "Unassigned"}
                      {" · "}
                      {conversation.topic || "No topic"}
                    </span>
                  </div>

                  <div className="conversation-status">
                    <span>{conversation.channel}</span>

                    <StatusBadge
                      status={conversation.status}
                      showDot={false}
                    />
                  </div>
                </div>
              ))}
            </div>
          ) : (
            <EmptyState
              icon={<ChatOutlined />}
              title="No database conversations yet"
              description="The existing Messenger system remains active. Conversation database synchronization will be connected in the next stage."
            />
          )}
        </AppCard>

        <AppCard
          padding="medium"
          className="dashboard-panel"
        >
          <div className="panel-title">
            <div>
              <span>CURRENT PLAN</span>
              <h3>
                {subscription?.plan_name || "No plan"}
              </h3>
            </div>

            <StatusBadge
              status={subscription?.status}
            />
          </div>

          <div className="plan-details">
            <div>
              <span>Users</span>

              <strong>
                {counts.users || 0}
                {" / "}
                {subscription?.max_users || 0}
              </strong>
            </div>

            <div>
              <span>Channels</span>

              <strong>
                {counts.channel_accounts || 0}
                {" / "}
                {subscription?.max_channel_accounts || 0}
              </strong>
            </div>

            <div>
              <span>AI messages</span>

              <strong>
                {subscription?.max_ai_messages || 0}
              </strong>
            </div>

            <div>
              <span>Expires</span>

              <strong>
                {subscription?.expires_at
                  ? new Date(
                      subscription.expires_at,
                    ).toLocaleDateString()
                  : "—"}
              </strong>
            </div>
          </div>
        </AppCard>
      </section>

      <AppCard
        padding="medium"
        className="dashboard-panel"
      >
        <div className="panel-title">
          <div>
            <span>CHANNEL ACCOUNTS</span>
            <h3>Connected channels</h3>
          </div>

          <button
            type="button"
            className="btn btn-secondary"
            onClick={() => navigate("/company-settings?section=channels")}
          >
            <AddOutlined fontSize="small" /> Add channel
          </button>
        </div>

        {channels.length ? (
          <div className="channel-grid">
            {channels.map((channel) => (
              <div
                className="channel-item"
                key={channel.id}
              >
                <div className="channel-symbol">
                  {(channel.channel || "?")
                    .charAt(0)
                    .toUpperCase()}
                </div>

                <div>
                  <strong>
                    {channel.name || channel.channel}
                  </strong>

                  <span>{channel.channel}</span>
                </div>

                <StatusBadge
                  status={channel.status}
                />
              </div>
            ))}
          </div>
        ) : (
          <EmptyState
            icon={<HubOutlined />}
            title="No channel accounts registered"
            description="Messenger remains connected through the current webhook. Channel account registration will be added without changing the working connection."
          />
        )}
      </AppCard>
    </div>
  );
}