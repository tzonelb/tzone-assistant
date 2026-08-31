import { DoneAllOutlined, ExpandLessOutlined, ExpandMoreOutlined, NotificationsNoneOutlined, RefreshOutlined } from "@mui/icons-material";
import { useEffect, useMemo, useState } from "react";
import { useNavigate } from "react-router-dom";
import { useNotifications } from "../../contexts/NotificationContext";
import { SUPPORTED_CHANNELS as CHANNELS } from "../../utils/channels";
import { formatPlatformDateTime, platformTimestamp } from "../../utils/dateTime";

function isUnread(item) { return !item?.is_read && !item?.read_at; }
function humanize(value) { return String(value || "").replaceAll("_", " ").replace(/\b\w/g, (letter) => letter.toUpperCase()); }

export default function NotificationsPage() {
  const navigate = useNavigate();
  const [statusFilter, setStatusFilter] = useState("all");
  const [typeFilter, setTypeFilter] = useState("");
  const [channelFilter, setChannelFilter] = useState("");
  const [dateFilter, setDateFilter] = useState("");
  const [expandedId, setExpandedId] = useState(null);
  const { items, summary, loading, error, refresh, markRead, markUnread, markAllRead } = useNotifications();

  useEffect(() => { refresh({ filters: { status: statusFilter, type: typeFilter, channel: channelFilter, date: dateFilter } }); }, [statusFilter, typeFilter, channelFilter, dateFilter, refresh]);

  const counts = useMemo(() => {
    const channel = Object.fromEntries(CHANNELS.map((name) => [name, 0]));
    const type = {};
    items.forEach((item) => {
      const amount = Number(item.grouped_count || 1);
      if (item.channel) channel[item.channel] = (channel[item.channel] || 0) + amount;
      if (item.notification_type) type[item.notification_type] = (type[item.notification_type] || 0) + amount;
    });
    return { channel, type };
  }, [items]);

  async function openConversation(item) {
    if (isUnread(item)) await markRead(item);
    if (item?.channel && item?.external_user_id) navigate(`/conversations/${encodeURIComponent(item.channel)}/${encodeURIComponent(item.external_user_id)}`);
  }

  return (
    <section className="notifications-page notifications-page-compact">
      <div className="notification-topline">
        <div className="notification-filter-tabs">
          {[["all", "All", summary.total], ["unread", "Unread", summary.unread], ["read", "Read", summary.read]].map(([value, label, count]) => (
            <button type="button" key={value} className={statusFilter === value ? "is-active" : ""} onClick={() => setStatusFilter(value)}>{label}<b>{count}</b></button>
          ))}
        </div>
        <div className="notifications-page-actions">
          <button type="button" onClick={() => refresh()}><RefreshOutlined fontSize="small" /> Refresh</button>
          <button type="button" onClick={markAllRead} disabled={summary.unread === 0}><DoneAllOutlined fontSize="small" /> Mark all read</button>
        </div>
      </div>

      <div className="notification-filterbar">
        <select value={typeFilter} onChange={(event) => setTypeFilter(event.target.value)}>
          <option value="">All categories ({summary.total})</option>
          {Object.entries(counts.type).map(([value, count]) => <option value={value} key={value}>{humanize(value)} ({count})</option>)}
        </select>
        <select value={channelFilter} onChange={(event) => setChannelFilter(event.target.value)}>
          <option value="">All channels ({summary.total})</option>
          {CHANNELS.map((value) => <option value={value} key={value}>{humanize(value)} ({counts.channel[value] || 0})</option>)}
        </select>
        <input type="date" value={dateFilter} onChange={(event) => setDateFilter(event.target.value)} />
      </div>

      <div className="notification-list-card notification-scroll-area">
        {loading ? <div className="notification-page-state">Loading notifications…</div> : error ? <div className="notification-page-state is-error">{error}</div> : items.length === 0 ? <div className="notification-page-empty"><NotificationsNoneOutlined /><strong>No notifications</strong><span>No activity matches the selected filters.</span></div> : items.map((item) => {
          const unread = isUnread(item);
          const count = Number(item.grouped_count || 1);
          const sender = item?.data?.customer_name || item?.data?.sender_name || item.title || "Customer";
          const groupedItems = [...(item?.data?.group_items || [])].sort((a, b) => platformTimestamp(a.created_at) - platformTimestamp(b.created_at));
          const expanded = expandedId === item.id;
          return <article className={`notification-row ${unread ? "is-unread" : ""}`} key={item.id}>
            <div className="notification-row-marker" />
            <div className="notification-row-content">
              <button type="button" className="notification-row-main" onClick={() => openConversation(item)}>
                <div className="notification-row-title"><strong>{sender} · {humanize(item.channel || "Platform")}</strong><time>{formatPlatformDateTime(item.created_at)}</time></div>
                <p>{item.body || item?.data?.message_preview || "New platform event"}</p>
              </button>
              <div className="notification-row-meta"><span>{humanize(item.notification_type)}</span><span>{humanize(item.channel || "platform")}</span>{count > 1 ? <button type="button" onClick={() => setExpandedId(expanded ? null : item.id)}>{count} messages {expanded ? <ExpandLessOutlined /> : <ExpandMoreOutlined />}</button> : null}</div>
              {expanded ? <div className="notification-group-sequence">{groupedItems.map((entry, index) => <div key={entry.id || index}><time>{formatPlatformDateTime(entry.created_at)}</time><span>{entry.body || entry.title || "Message"}</span></div>)}</div> : null}
            </div>
            <div className="notification-row-actions">{unread ? <button type="button" onClick={() => markRead(item)}>Mark read</button> : <button type="button" onClick={() => markUnread(item)}>Mark unread</button>}</div>
          </article>;
        })}
      </div>
    </section>
  );
}
