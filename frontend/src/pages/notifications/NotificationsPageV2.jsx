import { useEffect, useMemo, useRef, useState } from "react";
import { useNavigate } from "react-router-dom";
import {
  DoneAllOutlined,
  DoneOutlined,
  ExpandLessOutlined,
  ExpandMoreOutlined,
  NotificationsNoneOutlined,
  ReplayOutlined,
} from "@mui/icons-material";
import { useNotifications } from "../../contexts/NotificationContext";
import { formatPlatformDateTime, platformTimestamp } from "../../utils/dateTime";
import { EmptyState, ErrorState } from "../../components/common";
import "./NotificationsPageV2.css";

// Same shared NotificationContext as NotificationsPage.jsx (v1) - this is a
// visual rebuild only. The "type" tag row below is driven entirely by the
// real notification_type values the company has actually received (via
// counts.type, exactly as v1's category <select> already computed them) -
// no category is invented that the backend doesn't actually filter by.
const CHANNELS = ["messenger", "whatsapp", "instagram", "telegram", "website"];
function isUnread(item) { return !item?.is_read && !item?.read_at; }
function humanize(value) { return String(value || "").replaceAll("_", " ").replace(/\b\w/g, (letter) => letter.toUpperCase()); }

export default function NotificationsPageV2() {
  const navigate = useNavigate();
  const [statusFilter, setStatusFilter] = useState("all");
  const [typeFilter, setTypeFilter] = useState("");
  const [channelFilter, setChannelFilter] = useState("");
  const [dateFilter, setDateFilter] = useState("");
  const [expandedId, setExpandedId] = useState(null);
  const { items, summary, loading, error, refresh, markRead, markUnread, markAllRead } = useNotifications();

  useEffect(() => { refresh({ filters: { status: statusFilter, type: typeFilter, channel: channelFilter, date: dateFilter } }); }, [statusFilter, typeFilter, channelFilter, dateFilter, refresh]);

  // The type tag list must not depend on `items` alone, since `items` is
  // already server-filtered by the active type/channel/date filters - once
  // a category is picked, `items` would only ever contain that one
  // category, collapsing the other tags. Same fix v1 uses for its dropdown.
  const seenTypesRef = useRef(new Map());
  const counts = useMemo(() => {
    const type = {};
    items.forEach((item) => {
      const amount = Number(item.grouped_count || 1);
      if (item.notification_type) type[item.notification_type] = (type[item.notification_type] || 0) + amount;
    });
    if (!typeFilter) {
      seenTypesRef.current = new Map(Object.entries(type));
    } else {
      Object.keys(type).forEach((key) => seenTypesRef.current.set(key, type[key]));
    }
    return { type: Object.fromEntries(seenTypesRef.current) };
  }, [items, typeFilter]);

  async function openConversation(item) {
    if (isUnread(item)) await markRead(item);
    if (item?.channel && item?.external_user_id) navigate(`/conversations/${encodeURIComponent(item.channel)}/${encodeURIComponent(item.external_user_id)}`);
  }

  const kicker = `${summary.unread} unread`;

  return (
    <div className="tz-screen tzv2-notif-page">
      <div className="tzv2-notif-head">
        <div>
          <span className="tz-kick tzv2-notif-kicker">{kicker}</span>
        </div>
        <div className="tzv2-notif-head-actions">
          <button
            type="button"
            className={statusFilter === "unread" ? "tz-chip tz-chip-on" : "tz-chip"}
            aria-pressed={statusFilter === "unread"}
            onClick={() => setStatusFilter(statusFilter === "unread" ? "all" : "unread")}
          >
            Unread only
          </button>
          <button type="button" className="btn btn-secondary" onClick={markAllRead} disabled={summary.unread === 0}>
            <DoneAllOutlined fontSize="small" /> Mark all read
          </button>
          <button type="button" className="btn btn-secondary" onClick={() => navigate("/settings")}>
            Preferences
          </button>
        </div>
      </div>

      <div className="tzv2-notif-tags">
        <button type="button" className={!typeFilter ? "tag tag-outline" : "tag tag-neutral"} onClick={() => setTypeFilter("")}>All</button>
        {Object.keys(counts.type).map((value) => (
          <button type="button" key={value} className={typeFilter === value ? "tag tag-outline" : "tag tag-neutral"} onClick={() => setTypeFilter(typeFilter === value ? "" : value)}>
            {humanize(value)}
          </button>
        ))}
      </div>

      <div className="tzv2-notif-filterrow">
        <div className="field">
          <label htmlFor="tzv2-notif-channel">Channel</label>
          <select id="tzv2-notif-channel" className="input" value={channelFilter} onChange={(event) => setChannelFilter(event.target.value)}>
            <option value="">All channels</option>
            {CHANNELS.map((value) => <option value={value} key={value}>{humanize(value)}</option>)}
          </select>
        </div>
        <div className="field">
          <label htmlFor="tzv2-notif-date">Date</label>
          <input id="tzv2-notif-date" type="date" className="input" value={dateFilter} onChange={(event) => setDateFilter(event.target.value)} />
        </div>
      </div>

      <div className="tzv2-notif-list">
        {loading ? (
          <p className="tzv2-notif-state">Loading notifications…</p>
        ) : error ? (
          <ErrorState title="Notifications could not load" description={error} action={<button type="button" className="btn btn-primary" onClick={() => refresh()}><ReplayOutlined fontSize="small" /> Try again</button>} />
        ) : items.length === 0 ? (
          <EmptyState icon={<NotificationsNoneOutlined />} title="No notifications" description="No activity matches the selected filters." />
        ) : items.map((item) => {
          const unread = isUnread(item);
          const count = Number(item.grouped_count || 1);
          const sender = item?.data?.customer_name || item?.data?.sender_name || item.title || "Customer";
          const preview = item.body || item?.data?.message_preview || "New platform event";
          const groupedItems = [...(item?.data?.group_items || [])].sort((a, b) => platformTimestamp(a.created_at) - platformTimestamp(b.created_at));
          const expanded = expandedId === item.id;
          return (
            <div className="tz-row tzv2-notif-row" key={item.id}>
              {unread ? <span className="tzv2-notif-dot" /> : <span />}
              <button type="button" className="tzv2-notif-main" onClick={() => openConversation(item)}>
                <strong>{sender} · {humanize(item.channel || "Platform")}</strong>
                <div className="tz-num tzv2-notif-meta">{formatPlatformDateTime(item.created_at)} · {preview}</div>
              </button>
              <span className="tag tag-neutral">{humanize(item.notification_type)}</span>
              <div className="tzv2-notif-actions">
                {count > 1 ? (
                  <button type="button" className="btn btn-ghost tzv2-notif-count-btn" onClick={() => setExpandedId(expanded ? null : item.id)}>
                    {count} {expanded ? <ExpandLessOutlined fontSize="small" /> : <ExpandMoreOutlined fontSize="small" />}
                  </button>
                ) : null}
                <button type="button" className="btn btn-ghost" title={unread ? "Mark as read" : "Mark as unread"} onClick={() => (unread ? markRead(item) : markUnread(item))}>
                  <DoneOutlined fontSize="small" />
                </button>
                <button type="button" className="btn btn-ghost" onClick={() => openConversation(item)}>Open</button>
              </div>
              {expanded ? (
                <div className="tzv2-notif-group">
                  {groupedItems.map((entry, index) => (
                    <div className="tzv2-notif-group-item" key={entry.id || index}>
                      <span className="tz-num">{formatPlatformDateTime(entry.created_at)}</span>
                      <span>{entry.body || entry.title || "Message"}</span>
                    </div>
                  ))}
                </div>
              ) : null}
            </div>
          );
        })}
      </div>

      <p className="tzv2-notif-footnote">AI replies are recorded in the conversation timeline and never raise a bell notification.</p>
    </div>
  );
}
