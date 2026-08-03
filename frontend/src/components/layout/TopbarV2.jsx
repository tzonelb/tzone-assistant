import {
  CampaignOutlined,
  Inventory2Outlined,
  LogoutOutlined,
  MenuOpenOutlined,
  MenuOutlined,
  NotificationsNoneOutlined,
  PersonOutlined,
  SearchOutlined,
  TaskAltOutlined,
} from "@mui/icons-material";
import { useCallback, useEffect, useRef, useState } from "react";
import { useLocation, useNavigate } from "react-router-dom";
import { listBroadcastsRequest, listCustomersRequest, listProductsRequest, listTasksRequest } from "../../api/client";
import { useAuth } from "../../contexts/AuthContext";
import { useNotifications } from "../../contexts/NotificationContext";
import { usePlatformTheme } from "../../contexts/ThemeContext";
import NotificationDropdown from "../notifications/NotificationDropdown";
import "./TopbarV2.css";

const RESULT_LIMIT = 5;

// Global search — real, but scoped honestly to what the platform can
// actually answer in one pass: Customers and Products/Catalogue have a
// real server-side `search` param, so those two groups are true backend
// search. Tasks and Broadcasts have no search param on the backend
// (confirmed against tasks.py / broadcasts.py) — for those two, this
// fetches the real (small, per-company) list and filters it client-side
// by title/name, so results are still genuine records, just matched in
// the browser instead of the database. Conversations, appointments and
// everything else are NOT covered yet — deliberately left out rather
// than faked. See TopbarSearchDropdown below for the render.
async function runGlobalSearch(term) {
  const q = term.trim();
  if (!q) return { customers: [], products: [], tasks: [], broadcasts: [] };
  const lower = q.toLowerCase();

  const [customersRes, productsRes, tasksRes, broadcastsRes] = await Promise.all([
    listCustomersRequest({ search: q, limit: RESULT_LIMIT }).catch(() => null),
    listProductsRequest({ search: q }).catch(() => null),
    listTasksRequest({}).catch(() => null),
    listBroadcastsRequest().catch(() => null),
  ]);

  const customers = Array.isArray(customersRes?.items) ? customersRes.items.slice(0, RESULT_LIMIT) : [];
  const products = Array.isArray(productsRes?.items) ? productsRes.items.slice(0, RESULT_LIMIT) : [];
  const tasks = (Array.isArray(tasksRes?.items) ? tasksRes.items : [])
    .filter((task) => (task.title || "").toLowerCase().includes(lower))
    .slice(0, RESULT_LIMIT);
  const broadcasts = (Array.isArray(broadcastsRes?.items) ? broadcastsRes.items : [])
    .filter((broadcast) => (broadcast.name || "").toLowerCase().includes(lower))
    .slice(0, RESULT_LIMIT);

  return { customers, products, tasks, broadcasts };
}

function TopbarSearchDropdown({ query, results, loading, onNavigate }) {
  const hasAny = results.customers.length || results.products.length || results.tasks.length || results.broadcasts.length;

  return (
    <div className="topbar-v2-search-dropdown" role="listbox">
      {loading ? (
        <div className="topbar-v2-search-empty">Searching…</div>
      ) : !hasAny ? (
        <div className="topbar-v2-search-empty">
          No matches for "{query}" in Customers, Products, Tasks or Broadcasts.
        </div>
      ) : (
        <>
          {results.customers.length ? (
            <div className="topbar-v2-search-group">
              <span className="topbar-v2-search-group-label">Customers</span>
              {results.customers.map((customer) => (
                <button
                  type="button"
                  key={`customer-${customer.id}`}
                  className="topbar-v2-search-result"
                  onClick={() => onNavigate(`/customers/${customer.id}`)}
                >
                  <PersonOutlined fontSize="small" />
                  <span>{customer.display_name || customer.internal_name || "Unnamed customer"}</span>
                </button>
              ))}
            </div>
          ) : null}
          {results.products.length ? (
            <div className="topbar-v2-search-group">
              <span className="topbar-v2-search-group-label">Products</span>
              {results.products.map((product) => (
                <button
                  type="button"
                  key={`product-${product.id}`}
                  className="topbar-v2-search-result"
                  onClick={() => onNavigate(`/catalogue?q=${encodeURIComponent(product.name)}`)}
                >
                  <Inventory2Outlined fontSize="small" />
                  <span>{product.name}</span>
                </button>
              ))}
            </div>
          ) : null}
          {results.tasks.length ? (
            <div className="topbar-v2-search-group">
              <span className="topbar-v2-search-group-label">Tasks</span>
              {results.tasks.map((task) => (
                <button
                  type="button"
                  key={`task-${task.id}`}
                  className="topbar-v2-search-result"
                  onClick={() => onNavigate("/tasks")}
                >
                  <TaskAltOutlined fontSize="small" />
                  <span>{task.title}</span>
                </button>
              ))}
            </div>
          ) : null}
          {results.broadcasts.length ? (
            <div className="topbar-v2-search-group">
              <span className="topbar-v2-search-group-label">Broadcasts</span>
              {results.broadcasts.map((broadcast) => (
                <button
                  type="button"
                  key={`broadcast-${broadcast.id}`}
                  className="topbar-v2-search-result"
                  onClick={() => onNavigate(`/broadcast/${broadcast.id}`)}
                >
                  <CampaignOutlined fontSize="small" />
                  <span>{broadcast.name}</span>
                </button>
              ))}
            </div>
          ) : null}
        </>
      )}
    </div>
  );
}

export default function TopbarV2({ title, sidebarCollapsed, onOpenSidebar, onToggleSidebar }) {
  const { user, logout } = useAuth();
  const { brand } = usePlatformTheme();
  const location = useLocation();
  const navigate = useNavigate();
  const [conversationLive, setConversationLive] = useState(false);
  const [query, setQuery] = useState("");
  const [searchResults, setSearchResults] = useState({ customers: [], products: [], tasks: [], broadcasts: [] });
  const [searching, setSearching] = useState(false);
  const [searchOpen, setSearchOpen] = useState(false);
  const [notificationOpen, setNotificationOpen] = useState(false);
  const { unreadCount } = useNotifications();
  const searchRef = useRef(null);
  const searchBoxRef = useRef(null);
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
      if (event.key === "Escape") setSearchOpen(false);
    }
    window.addEventListener("keydown", handleKeydown);
    return () => window.removeEventListener("keydown", handleKeydown);
  }, []);

  useEffect(() => {
    function handleClickOutside(event) {
      if (searchBoxRef.current && !searchBoxRef.current.contains(event.target)) setSearchOpen(false);
    }
    window.addEventListener("mousedown", handleClickOutside);
    return () => window.removeEventListener("mousedown", handleClickOutside);
  }, []);

  useEffect(() => {
    const trimmed = query.trim();
    if (!trimmed) {
      setSearchResults({ customers: [], products: [], tasks: [], broadcasts: [] });
      setSearching(false);
      return;
    }
    setSearching(true);
    let cancelled = false;
    const timeout = window.setTimeout(() => {
      runGlobalSearch(trimmed).then((result) => {
        if (!cancelled) {
          setSearchResults(result);
          setSearching(false);
        }
      });
    }, 300);
    return () => { cancelled = true; window.clearTimeout(timeout); };
  }, [query]);

  function goToResult(path) {
    setSearchOpen(false);
    setQuery("");
    navigate(path);
  }

  function submitSearch(event) {
    event.preventDefault();
    const trimmed = query.trim();
    if (!trimmed) return;
    // Enter with no dropdown selection: jump to the first real match found
    // (Customers, then Products, then Tasks, then Broadcasts). If nothing
    // matched in any of those four areas, fall back to Customers' own
    // search page so the search bar still does *something* useful rather
    // than silently no-op — Customers is the one list page built to show
    // a "no results" state for an arbitrary term.
    const { customers, products, tasks, broadcasts } = searchResults;
    if (customers.length) return goToResult(`/customers/${customers[0].id}`);
    if (products.length) return goToResult(`/catalogue?q=${encodeURIComponent(products[0].name)}`);
    if (tasks.length) return goToResult("/tasks");
    if (broadcasts.length) return goToResult(`/broadcast/${broadcasts[0].id}`);
    goToResult(`/customers?q=${encodeURIComponent(trimmed)}`);
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

      <div className="topbar-v2-search-box" ref={searchBoxRef}>
        <form className="topbar-v2-search" onSubmit={submitSearch}>
          <SearchOutlined fontSize="small" />
          <input
            ref={searchRef}
            type="search"
            placeholder="Search customers, products, tasks, broadcasts…"
            value={query}
            onChange={(event) => setQuery(event.target.value)}
            onFocus={() => setSearchOpen(true)}
          />
          <kbd>⌘K</kbd>
        </form>
        {searchOpen && query.trim() ? (
          <TopbarSearchDropdown query={query.trim()} results={searchResults} loading={searching} onNavigate={goToResult} />
        ) : null}
      </div>

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
