import {
  LogoutOutlined,
  MenuOpenOutlined,
  MenuOutlined,
  NotificationsNoneOutlined,
  SearchOutlined,
} from "@mui/icons-material";
import { useCallback, useEffect, useRef, useState } from "react";
import { useLocation, useNavigate } from "react-router-dom";
import { useAuth } from "../../contexts/AuthContext";
import { useNotifications } from "../../contexts/NotificationContext";
import { usePlatformTheme } from "../../contexts/ThemeContext";
import NotificationDropdown from "../notifications/NotificationDropdown";
import "./TopbarV2.css";

// Global search is real but modest for this pass: it deep-links into
// Customers' existing search (CustomersPage.jsx reads ?q= on mount) —
// a real jump, not a command-palette. Building actual cross-entity
// search (conversations, customers, tasks...) is separate, larger work.
export default function TopbarV2({ title, sidebarCollapsed, onOpenSidebar, onToggleSidebar }) {
  const { user, logout } = useAuth();
  const { brand } = usePlatformTheme();
  const location = useLocation();
  const navigate = useNavigate();
  const [conversationLive, setConversationLive] = useState(false);
  const [query, setQuery] = useState("");
  const [notificationOpen, setNotificationOpen] = useState(false);
  const { unreadCount } = useNotifications();
  const searchRef = useRef(null);
  const closeNotifications = useCallback(() => setNotificationOpen(false), []);

  useEffect(() => {
    function handleLive(event) {
      setConversationLive(Boolean(event.detail?.connected));
    }
    window.addEventListener("tzone:conversation-live", handleLive);
    return () => window.removeEventListener("tzone:conversation-live", handleLive);
  }, []);

  useEffect(() => {
    function handleKeydown(event) {
      const isSearchShortcut = (event.metaKey || event.ctrlKey) && event.key.toLowerCase() === "k";
      if (isSearchShortcut) {
        event.preventDefault();
        searchRef.current?.focus();
      }
    }
    window.addEventListener("keydown", handleKeydown);
    return () => window.removeEventListener("keydown", handleKeydown);
  }, []);

  function submitSearch(event) {
    event.preventDefault();
    const trimmed = query.trim();
    if (!trimmed) return;
    navigate(`/customers?q=${encodeURIComponent(trimmed)}`);
  }

  const displayName = user?.full_name || user?.email || "Administrator";
  const avatarLetter = displayName.charAt(0).toUpperCase();

  return (
    <header className="topbar-v2">
      <div className="topbar-v2-left">
        <button
          type="button"
          className="icon-button desktop-menu-button"
          aria-label={sidebarCollapsed ? "Expand main menu" : "Hide main menu"}
          title={sidebarCollapsed ? "Expand menu" : "Hide menu"}
          onClick={onToggleSidebar}
        >
          {sidebarCollapsed ? <MenuOutlined /> : <MenuOpenOutlined />}
        </button>
        <button type="button" className="icon-button mobile-menu-button" aria-label="Open menu" onClick={onOpenSidebar}>
          <MenuOutlined />
        </button>
        <div>
          <span className="topbar-v2-eyebrow">{brand?.name || "T-ZONE"} PLATFORM</span>
          <h1 className="topbar-v2-title">{title}</h1>
        </div>
      </div>

      <form className="topbar-v2-search" onSubmit={submitSearch}>
        <SearchOutlined fontSize="small" />
        <input
          ref={searchRef}
          type="search"
          placeholder="Search customers…"
          value={query}
          onChange={(event) => setQuery(event.target.value)}
        />
        <kbd>⌘K</kbd>
      </form>

      <div className="topbar-v2-actions">
        {location.pathname.startsWith("/conversations") ? (
          <span className={`topbar-v2-live ${conversationLive ? "is-live" : ""}`}>{conversationLive ? "Live" : "Reconnecting"}</span>
        ) : null}

        <div className="notification-menu-anchor">
          <button
            type="button"
            className="icon-button notification-bell-button"
            aria-label="Notification Center"
            aria-expanded={notificationOpen}
            onClick={() => setNotificationOpen((current) => !current)}
          >
            <NotificationsNoneOutlined />
            {unreadCount > 0 ? <span className="notification-badge">{unreadCount > 99 ? "99+" : unreadCount}</span> : null}
          </button>
          <NotificationDropdown open={notificationOpen} onClose={closeNotifications} />
        </div>

        <div className="topbar-v2-user">
          <div className="topbar-v2-avatar">{avatarLetter}</div>
          <div className="topbar-v2-user-details">
            <strong>{displayName}</strong>
            <span>{user?.is_super_admin ? "Super Administrator" : "Company User"}</span>
          </div>
        </div>

        <button type="button" className="logout-button" onClick={logout}>
          <LogoutOutlined fontSize="small" />
          <span>Logout</span>
        </button>
      </div>
    </header>
  );
}
