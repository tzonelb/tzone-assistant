import { useEffect, useState } from "react";
import { useNavigate } from "react-router-dom";
import { AddOutlined, AutoAwesomeOutlined, CampaignOutlined, ChatBubbleOutlined, LinkOutlined } from "@mui/icons-material";
import { getCurrentUserRequest, listScheduledPostsRequest, scheduledPostOptionsRequest } from "../../api/client";
import { AppButton, AppCard, LoadingState } from "../../components/common";
import { channelIcon } from "./channelIcon";
import "./HomePage.css";

function formatDateTime(value) {
  if (!value) return "—";
  const normalized = /(?:Z|[+-]\d{2}:?\d{2})$/i.test(value) ? value : `${value}Z`;
  const date = new Date(normalized);
  return Number.isNaN(date.getTime()) ? "—" : date.toLocaleString();
}

export default function HomePage() {
  const navigate = useNavigate();
  const [userName, setUserName] = useState("");
  const [channelAccounts, setChannelAccounts] = useState([]);
  const [upNext, setUpNext] = useState([]);
  const [draftCount, setDraftCount] = useState(0);
  const [sentThisWeekCount, setSentThisWeekCount] = useState(0);
  const [loading, setLoading] = useState(true);

  useEffect(() => {
    getCurrentUserRequest().then((result) => setUserName(result?.user?.full_name || result?.user?.email || "")).catch(() => {});

    Promise.all([
      scheduledPostOptionsRequest(),
      listScheduledPostsRequest({ status: "scheduled" }),
      listScheduledPostsRequest({ status: "draft" }),
      listScheduledPostsRequest({ status: "sent" }),
    ])
      .then(([options, scheduled, drafts, sent]) => {
        setChannelAccounts(Array.isArray(options?.channel_accounts) ? options.channel_accounts : []);
        setUpNext((Array.isArray(scheduled?.items) ? scheduled.items : []).slice(0, 5));
        setDraftCount(Array.isArray(drafts?.items) ? drafts.items.length : 0);

        const oneWeekAgo = Date.now() - 7 * 24 * 60 * 60 * 1000;
        const sentItems = Array.isArray(sent?.items) ? sent.items : [];
        setSentThisWeekCount(sentItems.filter((post) => {
          const publishedAt = post.published_at ? new Date(post.published_at).getTime() : 0;
          return publishedAt >= oneWeekAgo;
        }).length);
      })
      .catch(() => {})
      .finally(() => setLoading(false));
  }, []);

  const greetingHour = new Date().getHours();
  const greeting = greetingHour < 12 ? "Good Morning" : greetingHour < 18 ? "Good Afternoon" : "Good Evening";
  const today = new Date().toLocaleDateString(undefined, { weekday: "short", month: "short", day: "numeric", year: "numeric" });

  if (loading) return <LoadingState label="Loading…" />;

  return (
    <section className="community-home-page">
      <header className="community-home-header">
        <div>
          <h2>{greeting}{userName ? `, ${userName}!` : "!"}</h2>
          <span>{today}</span>
        </div>
      </header>

      <div className="community-home-stats">
        <AppCard padding="medium" className="community-home-stat-card">
          <div className="community-home-stat-badge">{channelAccounts.length}</div>
          <div>
            <strong>Connected Channels</strong>
            <span>{channelAccounts.length ? "Ready to publish" : "Connect one to get started"}</span>
          </div>
        </AppCard>
        <AppCard padding="medium" className="community-home-stat-card">
          <div className="community-home-stat-badge">{sentThisWeekCount}</div>
          <div>
            <strong>Posts sent this week</strong>
            <span>Across all connected channels</span>
          </div>
        </AppCard>
        <AppCard padding="medium" className="community-home-stat-card">
          <div className="community-home-stat-badge">{draftCount}</div>
          <div>
            <strong>Drafts waiting</strong>
            <span>Ready to review and schedule</span>
          </div>
        </AppCard>
      </div>

      <div className="community-home-firststeps-head">First Steps</div>
      <div className="community-home-firststeps">
        <AppCard padding="medium" className="community-home-step-card">
          <LinkOutlined />
          <strong>1. Connect a channel</strong>
          <p>Link a Facebook Page or Instagram account to start publishing.</p>
          <AppButton variant="secondary" size="small" onClick={() => navigate("/company-settings/channels")}>Connect Channel</AppButton>
        </AppCard>
        <AppCard padding="medium" className="community-home-step-card">
          <CampaignOutlined />
          <strong>2. Create a post</strong>
          <p>Schedule your first post in just a few clicks.</p>
          <AppButton variant="secondary" size="small" onClick={() => navigate("/community/publish?new=1")}>Create Post</AppButton>
        </AppCard>
        <AppCard padding="medium" className="community-home-step-card">
          <AutoAwesomeOutlined />
          <strong>3. Teach your AI</strong>
          <p>Add instructions and knowledge so your AI replies accurately.</p>
          <AppButton variant="secondary" size="small" onClick={() => navigate("/ai-teaching")}>Set Up AI Teaching</AppButton>
        </AppCard>
      </div>

      <div className="community-home-columns">
        <AppCard padding="medium">
          <div className="community-home-panel-head">
            <h3>Up Next</h3>
            <span>{upNext.length} post{upNext.length === 1 ? "" : "s"} scheduled</span>
          </div>
          {upNext.length ? (
            <div className="community-home-upnext-list">
              {upNext.map((post) => (
                <div key={post.id} className="community-home-upnext-row">
                  <div className="community-home-upnext-avatars">
                    {post.channel_account_ids.map((accountId) => {
                      const account = channelAccounts.find((item) => item.id === accountId);
                      const Icon = channelIcon(account?.channel);
                      return <Icon key={accountId} fontSize="small" />;
                    })}
                  </div>
                  <span className="community-home-upnext-text">{post.text || "(media only)"}</span>
                  <time>{formatDateTime(post.scheduled_at)}</time>
                </div>
              ))}
            </div>
          ) : (
            <div className="community-home-empty">
              <AddOutlined fontSize="large" />
              <p>No posts scheduled yet.</p>
              <span>You'll see upcoming posts here.</span>
              <AppButton variant="secondary" size="small" onClick={() => navigate("/community/publish?new=1")}>+ Create Post</AppButton>
            </div>
          )}
        </AppCard>

        <AppCard padding="medium">
          <div className="community-home-panel-head">
            <h3>Community</h3>
          </div>
          <div className="community-home-empty">
            <ChatBubbleOutlined fontSize="large" />
            <p>Unified comment inbox — coming soon.</p>
            <span>Needs a Meta feed-comment webhook confirmed on your Meta App first.</span>
          </div>
        </AppCard>
      </div>
    </section>
  );
}
