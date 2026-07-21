import {
  LogoutOutlined,
  MenuOpenOutlined,
  MenuOutlined,
  NotificationsNoneOutlined,
} from "@mui/icons-material";

import { useCallback, useEffect, useState } from "react";
import { useLocation } from "react-router-dom";

import { useAuth } from "../../contexts/AuthContext";
import { useNotifications } from "../../contexts/NotificationContext";
import NotificationDropdown from "../notifications/NotificationDropdown";


export default function Topbar({
  title,
  sidebarCollapsed,
  onOpenSidebar,
  onToggleSidebar,
}) {
  const { user, logout } = useAuth();
  const location = useLocation();
  const [conversationLive, setConversationLive] = useState(false);
  const [conversationRefreshing, setConversationRefreshing] = useState(false);
  const [notificationOpen, setNotificationOpen] = useState(false);
  const { unreadCount } = useNotifications();
  const closeNotifications = useCallback(() => setNotificationOpen(false), []);

  useEffect(() => {
    function handleLive(event) {
      setConversationLive(Boolean(event.detail?.connected));
    }
    function handleRefreshing(event) {
      setConversationRefreshing(Boolean(event.detail?.refreshing));
    }
    window.addEventListener("tzone:conversation-live", handleLive);
    window.addEventListener("tzone:conversation-refreshing", handleRefreshing);
    return () => {
      window.removeEventListener("tzone:conversation-live", handleLive);
      window.removeEventListener("tzone:conversation-refreshing", handleRefreshing);
    };
  }, []);

  const onConversationsPage = location.pathname.startsWith("/conversations");

  const displayName =
    user?.full_name ||
    user?.email ||
    "Administrator";

  const avatarLetter = displayName
    .charAt(0)
    .toUpperCase();

  return (
    <header className="topbar">
      <div className="topbar-left">
        <button
          type="button"
          className="icon-button desktop-menu-button"
          aria-label={
            sidebarCollapsed
              ? "Expand main menu"
              : "Hide main menu"
          }
          title={
            sidebarCollapsed
              ? "Expand menu"
              : "Hide menu"
          }
          onClick={onToggleSidebar}
        >
          {sidebarCollapsed ? (
            <MenuOutlined />
          ) : (
            <MenuOpenOutlined />
          )}
        </button>

        <button
          type="button"
          className="icon-button mobile-menu-button"
          aria-label="Open menu"
          onClick={onOpenSidebar}
        >
          <MenuOutlined />
        </button>

        <div>
          <span className="topbar-eyebrow">T-ZONE PLATFORM</span>
          <div className="topbar-title-line">
            <h1>{title}</h1>
            {onConversationsPage ? (
              <div className="topbar-conversation-tools">
                <span className={`topbar-live-status ${conversationLive ? "is-live" : ""}`}>
                  {conversationLive ? "Live" : "Reconnecting"}
                </span>
                <button
                  type="button"
                  className="topbar-refresh-button"
                  disabled={conversationRefreshing}
                  onClick={() => window.dispatchEvent(new CustomEvent("tzone:conversation-refresh"))}
                >
                  {conversationRefreshing ? "Refreshing…" : "Refresh"}
                </button>
              </div>
            ) : null}
          </div>
        </div>
      </div>

      <div className="topbar-actions">
        <div className="notification-menu-anchor">
          <button
            type="button"
            className="icon-button notification-bell-button"
            aria-label="Notification Center"
            title="Notification Center"
            aria-expanded={notificationOpen}
            onClick={() => setNotificationOpen((current) => !current)}
          >
            <NotificationsNoneOutlined />
            {unreadCount > 0 ? (
              <span className="notification-badge">
                {unreadCount > 99 ? "99+" : unreadCount}
              </span>
            ) : null}
          </button>
          <NotificationDropdown
            open={notificationOpen}
            onClose={closeNotifications}
          />
        </div>

        <div className="topbar-user">
          <div className="topbar-avatar">
            {avatarLetter}
          </div>

          <div className="topbar-user-details">
            <strong>{displayName}</strong>
            <span>
              {user?.is_super_admin
                ? "Super Administrator"
                : "Company User"}
            </span>
          </div>
        </div>

        <button
          type="button"
          className="logout-button"
          onClick={logout}
        >
          <LogoutOutlined fontSize="small" />
          <span>Logout</span>
        </button>
      </div>
    </header>
  );
}
