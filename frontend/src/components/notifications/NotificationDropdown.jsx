import {
  CheckCircleOutlineOutlined,
  DeleteSweepOutlined,
  DoneAllOutlined,
  NotificationsNoneOutlined,
  RefreshOutlined,
} from "@mui/icons-material";
import { useEffect, useMemo, useRef } from "react";
import { useNavigate } from "react-router-dom";

import { useNotifications } from "../../contexts/NotificationContext";

const VISIBLE_LIMIT = 5;

function formatDate(value) {
  if (!value) return "";
  const date = new Date(value);
  if (Number.isNaN(date.getTime())) return "";
  return new Intl.DateTimeFormat(undefined, {
    month: "short",
    day: "numeric",
    hour: "2-digit",
    minute: "2-digit",
  }).format(date);
}

function isUnread(item) {
  return !item?.is_read && !item?.read_at;
}

function itemTitle(item) {
  const count = Number(item?.grouped_count || 1);
  const sender = item?.data?.customer_name || item?.data?.sender_name || item?.title || "Customer";
  const channel = item?.channel ? String(item.channel).replaceAll("_", " ") : "Platform";
  return count > 1 ? `${count} · ${sender} · ${channel}` : `${sender} · ${channel}`;
}

export default function NotificationDropdown({ open, onClose }) {
  const panelRef = useRef(null);
  const navigate = useNavigate();
  const {
    items,
    unreadCount,
    loading,
    error,
    refresh,
    markRead,
    markAllRead,
    clearShown,
  } = useNotifications();
  const unreadItems = useMemo(() => items.filter(isUnread), [items]);
  const visibleItems = useMemo(
    () => unreadItems.slice(0, VISIBLE_LIMIT),
    [unreadItems],
  );

  useEffect(() => {
    if (!open) return undefined;
    const handleMouseDown = (event) => {
      if (panelRef.current && !panelRef.current.contains(event.target)) onClose();
    };
    const handleKeyDown = (event) => {
      if (event.key === "Escape") onClose();
    };
    document.addEventListener("mousedown", handleMouseDown);
    document.addEventListener("keydown", handleKeyDown);
    return () => {
      document.removeEventListener("mousedown", handleMouseDown);
      document.removeEventListener("keydown", handleKeyDown);
    };
  }, [open, onClose]);

  if (!open) return null;

  return (
    <div className="notification-dropdown" ref={panelRef} role="dialog" aria-label="Notifications">
      <div className="notification-dropdown-head">
        <div><strong>Notifications</strong><span>{unreadCount} unread</span></div>
        <div className="notification-dropdown-actions">
          <button type="button" onClick={() => refresh()} title="Refresh"><RefreshOutlined fontSize="small" /></button>
          {unreadCount > 0 ? (
            <button type="button" onClick={markAllRead} title="Mark all as read"><DoneAllOutlined fontSize="small" /></button>
          ) : null}
        </div>
      </div>

      <div className="notification-dropdown-body">
        {loading ? (
          <div className="notification-dropdown-state">Loading notifications…</div>
        ) : error ? (
          <div className="notification-dropdown-state is-error">{error}</div>
        ) : visibleItems.length === 0 ? (
          <div className="notification-dropdown-empty">
            <NotificationsNoneOutlined />
            <strong>No notifications yet</strong>
            <span>New customer messages and platform events will appear here.</span>
          </div>
        ) : visibleItems.map((item) => {
          const unread = isUnread(item);
          return (
            <button
              type="button"
              className={`notification-dropdown-item ${unread ? "is-unread" : ""}`}
              key={item.id}
              onClick={async () => {
                if (unread) await markRead(item);
                if (item?.channel && item?.external_user_id) {
                  navigate(`/conversations/${encodeURIComponent(item.channel)}/${encodeURIComponent(item.external_user_id)}`);
                } else {
                  navigate("/notifications");
                }
                onClose();
              }}
            >
              <span className="notification-dropdown-icon">{unread ? <NotificationsNoneOutlined /> : <CheckCircleOutlineOutlined />}</span>
              <span className="notification-dropdown-copy">
                <strong>{itemTitle(item)}</strong>
                <span>{item?.body || item?.data?.message_preview || "New platform event"}</span>
                <time>{formatDate(item?.created_at)}</time>
              </span>
            </button>
          );
        })}
      </div>

      <div className="notification-dropdown-footer-actions">
        {visibleItems.length ? (
          <button type="button" onClick={() => clearShown(visibleItems)}>
            <DeleteSweepOutlined fontSize="small" /> Clear shown
          </button>
        ) : null}
        {unreadItems.length > VISIBLE_LIMIT ? (
          <button type="button" onClick={() => { navigate("/notifications"); onClose(); }}>
            See all ({unreadItems.length})
          </button>
        ) : (
          <button type="button" onClick={() => { navigate("/notifications"); onClose(); }}>
            Notification center
          </button>
        )}
      </div>
    </div>
  );
}
