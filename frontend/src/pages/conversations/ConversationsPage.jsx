import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import {
  AllInboxOutlined,
  ArchiveOutlined,
  BoltOutlined,
  ChatBubbleOutlineOutlined,
  CheckCircleOutlineOutlined,
  ChevronLeftOutlined,
  ChevronRightOutlined,
  ExpandMoreOutlined,
  FilterListOutlined,
  ForumOutlined,
  Instagram,
  LanguageOutlined,
  MarkChatUnreadOutlined,
  PersonOutlineOutlined,
  SearchOutlined,
  StarOutlineOutlined,
  Telegram,
  WhatsApp,
} from "@mui/icons-material";
import { useNavigate, useParams } from "react-router-dom";
import {
  getConversationsRequest,
  updateConversationControlRequest,
} from "../../api/client";
import { AppButton, AppCard, ErrorState, StatusBadge } from "../../components/common";
import { useConversationLive } from "../../contexts/ConversationLiveContext";
import ConversationDetailPage from "./ConversationDetailPage";
import { SUPPORTED_CHANNELS } from "../../utils/channels";
import "./ConversationInbox.css";

// Shown until the first response arrives; the server's list wins after that.
const FOLDERS = [
  { value: "inbox", label: "Inbox", icon: AllInboxOutlined },
  { value: "assigned_to_me", label: "Assigned to me", icon: PersonOutlineOutlined },
  { value: "unread", label: "Unread", icon: MarkChatUnreadOutlined },
  { value: "done", label: "Done", icon: CheckCircleOutlineOutlined },
  { value: "starred", label: "Starred", icon: StarOutlineOutlined },
  { value: "archived", label: "Archived", icon: ArchiveOutlined },
];

const CHANNEL_ICONS = {
  all: ChatBubbleOutlineOutlined,
  messenger: BoltOutlined,
  whatsapp: WhatsApp,
  instagram: Instagram,
  telegram: Telegram,
  website: LanguageOutlined,
};

function humanize(value) {
  return String(value ?? "").replaceAll("_", " ").replace(/\b\w/g, (letter) => letter.toUpperCase());
}

function parseServerDate(value) {
  if (!value) return null;
  const raw = String(value).trim();
  const normalized = /(?:Z|[+-]\d{2}:?\d{2})$/i.test(raw) ? raw : `${raw}Z`;
  const date = new Date(normalized);
  return Number.isNaN(date.getTime()) ? null : date;
}

function formatDate(value) {
  const date = parseServerDate(value);
  return date ? date.toLocaleString() : "—";
}

function truncate(value, maximum = 72) {
  const text = String(value || "").trim();
  return text.length <= maximum ? text : `${text.slice(0, maximum)}…`;
}

export default function ConversationsPage() {
  const navigate = useNavigate();
  const { channel: routeChannel, userId: routeUserId } = useParams();
  const live = useConversationLive();
  const liveRef = useRef(live);
  liveRef.current = live;

  const [rows, setRows] = useState([]);
  const [channelCounts, setChannelCounts] = useState({});
  const [availableChannels, setAvailableChannels] = useState([]);
  const [supportedChannels, setSupportedChannels] = useState(SUPPORTED_CHANNELS);
  const [employees, setEmployees] = useState([]);
  const [departmentOptions, setDepartmentOptions] = useState([]);
  const [activeChannel, setActiveChannel] = useState("all");
  const [activeFolder, setActiveFolder] = useState("inbox");
  const [searchInput, setSearchInput] = useState("");
  const [search, setSearch] = useState("");
  const [status, setStatus] = useState("all");
  const [department, setDepartment] = useState("all");
  const [assignedUserId, setAssignedUserId] = useState("all");
  const [tag, setTag] = useState("");
  const [readStatus, setReadStatus] = useState("all");
  const [filtersOpen, setFiltersOpen] = useState(false);
  const [folderMenuOpen, setFolderMenuOpen] = useState(false);
  const [loading, setLoading] = useState(true);
  const [refreshing, setRefreshing] = useState(false);
  const [error, setError] = useState("");
  const [conversationListOpen, setConversationListOpen] = useState(true);

  const loadConversations = useCallback(async ({ silent = false } = {}) => {
    silent ? setRefreshing(true) : setLoading(true);
    setError("");
    try {
      const result = await getConversationsRequest({
        search,
        channel: activeChannel,
        status,
        department,
        assignedUserId: assignedUserId === "all" ? null : Number(assignedUserId),
        folder: activeFolder,
        tag,
        readStatus,
        page: 1,
        pageSize: 100,
      });
      setRows(Array.isArray(result?.items) ? result.items : []);
      setChannelCounts(result?.channel_counts || {});
      setAvailableChannels(Array.isArray(result?.available_channels) ? result.available_channels : []);
      if (Array.isArray(result?.supported_channels) && result.supported_channels.length) {
        setSupportedChannels(result.supported_channels);
      }
      setEmployees(Array.isArray(result?.employees) ? result.employees : []);
      setDepartmentOptions(
        Array.isArray(result?.department_options) ? result.department_options : [],
      );
    } catch (requestError) {
      setError(requestError.message || "Conversations could not be loaded.");
    } finally {
      setLoading(false);
      setRefreshing(false);
    }
  }, [search, activeChannel, status, department, assignedUserId, activeFolder, tag, readStatus]);

  useEffect(() => { loadConversations(); }, [loadConversations]);

  useEffect(() => {
    const timeout = window.setTimeout(() => setSearch(searchInput.trim()), 300);
    return () => window.clearTimeout(timeout);
  }, [searchInput]);

  useEffect(() => live.subscribe((event) => {
    if (event?.type === "snapshot") loadConversations({ silent: true });
  }), [live, loadConversations]);

  useEffect(() => {
    window.dispatchEvent(new CustomEvent("tzone:conversation-live", { detail: { connected: live.connected } }));
  }, [live.connected]);

  useEffect(() => {
    window.dispatchEvent(new CustomEvent("tzone:conversation-refreshing", { detail: { refreshing } }));
  }, [refreshing]);

  useEffect(() => {
    function refreshFromTopbar() { loadConversations({ silent: true }); }
    window.addEventListener("tzone:conversation-refresh", refreshFromTopbar);
    return () => window.removeEventListener("tzone:conversation-refresh", refreshFromTopbar);
  }, [loadConversations]);

  const enabledChannels = useMemo(() => new Set(availableChannels), [availableChannels]);
  const channelTabs = useMemo(
    () => ["all", ...supportedChannels],
    [supportedChannels],
  );
  const availableTags = useMemo(() => {
    const values = new Set();
    rows.forEach((row) => (row.tags || []).forEach((item) => values.add(item)));
    return [...values].sort();
  }, [rows]);

  const activeFolderItem = FOLDERS.find((item) => item.value === activeFolder) || FOLDERS[0];
  const ActiveFolderIcon = activeFolderItem.icon;
  const hasSelectedConversation = Boolean(routeChannel && routeUserId);

  function openConversation(row) {
    navigate(`/conversations/${encodeURIComponent(row.channel)}/${encodeURIComponent(row.external_user_id)}`);
  }

  async function quickUpdate(row, updates) {
    if (!row.can_manage) return;
    try {
      await updateConversationControlRequest(row.channel, row.external_user_id, updates);
      await loadConversations({ silent: true });
    } catch (requestError) {
      setError(requestError.message || "Conversation action failed.");
    }
  }

  function chooseFolder(value) {
    setActiveFolder(value);
    setFolderMenuOpen(false);
    if (value === "unread") setReadStatus("unread");
    else if (readStatus === "unread") setReadStatus("all");
    navigate("/conversations");
  }

  return (
    <div className={`unified-inbox-page channel-${activeChannel}`}>
      <AppCard padding="none" className="unified-inbox-shell">
        <nav className="global-channel-tabs" aria-label="Channels">
          {channelTabs.map((channelName) => {
            const Icon = CHANNEL_ICONS[channelName] || ForumOutlined;
            const connected = channelName === "all" || enabledChannels.has(channelName);
            const unreadCount = Number(channelCounts[channelName] || 0);
            return (
              <button
                type="button"
                className={`channel-tab channel-tab-${channelName} ${activeChannel === channelName ? "is-active" : ""} ${connected ? "" : "is-preview"}`}
                key={channelName}
                onClick={() => setActiveChannel(channelName)}
                title={connected ? undefined : `${humanize(channelName)} is not connected yet.`}
              >
                <Icon />
                <span>{channelName === "all" ? "All messages" : humanize(channelName)}</span>
                {unreadCount > 0 ? <strong>{unreadCount}</strong> : null}
              </button>
            );
          })}
        </nav>

        <div className={`unified-inbox-grid ${conversationListOpen ? "" : "list-collapsed"}`}>
          <aside className="unified-conversation-list">
            <header className="unified-list-header">
              <div className="folder-dropdown-wrap">
                <button type="button" className="folder-dropdown-trigger" onClick={() => setFolderMenuOpen((value) => !value)}>
                  <ActiveFolderIcon />
                  <span>{activeFolderItem.label}</span>
                  <ExpandMoreOutlined />
                </button>
                {folderMenuOpen ? (
                  <div className="folder-dropdown-menu" onMouseLeave={() => setFolderMenuOpen(false)}>
                    {FOLDERS.map((folderItem) => {
                      const Icon = folderItem.icon;
                      return (
                        <button
                          type="button"
                          className={activeFolder === folderItem.value ? "is-active" : ""}
                          key={folderItem.value}
                          onClick={() => chooseFolder(folderItem.value)}
                        >
                          <Icon /><span>{folderItem.label}</span>
                        </button>
                      );
                    })}
                  </div>
                ) : null}
              </div>
              <button type="button" className="inbox-icon-button" title="Hide conversation list" onClick={() => setConversationListOpen(false)}>
                <ChevronLeftOutlined />
              </button>
            </header>

            <div className="conversation-search-line">
              <label className="unified-list-search">
                <SearchOutlined />
                <input
                  value={searchInput}
                  placeholder="Search customers and all messages..."
                  onFocus={() => setFiltersOpen(true)}
                  onChange={(event) => setSearchInput(event.target.value)}
                />
              </label>
              <button type="button" className={`inbox-icon-button ${filtersOpen ? "is-active" : ""}`} title="Filters" onClick={() => setFiltersOpen((value) => !value)}>
                <FilterListOutlined />
              </button>
            </div>

            {filtersOpen ? (
              <div className="unified-list-filters">
                <div className="filter-grid">
                  <select value={status} onChange={(event) => setStatus(event.target.value)}>
                    <option value="all">All statuses</option><option value="ai_handling">AI handling</option><option value="human_handling">Human handling</option><option value="waiting_customer">Waiting customer</option><option value="waiting_agent">Waiting agent</option><option value="pending">Pending</option><option value="resolved">Resolved</option><option value="closed">Closed</option>
                  </select>
                  <select value={department} onChange={(event) => setDepartment(event.target.value)}>
                    {/* The company's own sections, from the server. This list
                        used to be eight hardcoded English names — another
                        company's departments, offered to every company's team,
                        filtering on values nothing in the database held. */}
                    <option value="all">All departments</option>
                    {departmentOptions.map((item) => (
                      <option value={item.code} key={item.code}>{item.label}</option>
                    ))}
                  </select>
                  <select value={assignedUserId} onChange={(event) => setAssignedUserId(event.target.value)}>
                    <option value="all">All employees</option>{employees.map((employee) => <option value={employee.id} key={employee.id}>{employee.display_name}</option>)}
                  </select>
                  <select value={readStatus} onChange={(event) => setReadStatus(event.target.value)}>
                    <option value="all">All read states</option><option value="unread">Unread only</option><option value="read">Read only</option>
                  </select>
                  <select value={tag} onChange={(event) => setTag(event.target.value)}>
                    <option value="">All tags</option>{availableTags.map((item) => <option value={item} key={item}>{item}</option>)}
                  </select>
                </div>
              </div>
            ) : null}

            <div className="unified-list-items">
              {loading ? <div className="unified-list-empty">Loading...</div> : null}
              {error ? <ErrorState title="Could not load conversations" description={error} action={<AppButton variant="primary" onClick={() => loadConversations()}>Retry</AppButton>} /> : null}
              {!loading && !error && rows.length === 0 ? <div className="unified-list-empty">No conversations found in {activeFolderItem.label}.</div> : null}
              {!loading && !error && rows.map((row) => {
                const selected = routeChannel === row.channel && routeUserId === row.external_user_id;
                const unread = Number(row.unread_count || 0) > 0 || row.is_unread;
                return (
                  <article className={`unified-list-item ${selected ? "is-selected" : ""} ${unread ? "is-unread" : ""}`} key={row.id}>
                    <button type="button" className="conversation-open-button" onClick={() => openConversation(row)}>
                      <div className="unified-list-avatar">{(row.customer_alias || row.customer_name || "?").charAt(0).toUpperCase()}</div>
                      <div className="unified-list-content">
                        <div className="unified-list-topline">
                          <strong>{row.customer_name || "Unknown Customer"}{row.customer_alias ? <em>{row.customer_alias}</em> : null}</strong>
                          <time>{formatDate(row.updated_at)}</time>
                        </div>
                        <p>{truncate(row.last_message) || "No message text"}</p>
                        <div className="unified-list-meta">
                          <span>{humanize(row.channel)}</span><span>{row.department || "Unassigned"}</span>
                          <StatusBadge status={row.handled_by_ai ? "active" : "handoff"} label={row.handled_by_ai ? "AI" : "Human"} />
                          {row.assigned_user_name ? <span>{row.assigned_user_name}</span> : null}
                          {unread ? <b className="unread-count">{row.unread_count || 1}</b> : null}
                        </div>
                      </div>
                    </button>
                    {row.can_manage ? (
                      <div className="conversation-slide-actions">
                        <button type="button" className={row.is_starred ? "action-star is-active" : "action-star"} title={row.is_starred ? "Unstar" : "Star"} onClick={() => quickUpdate(row, { is_starred: !row.is_starred })}><StarOutlineOutlined /></button>
                        <button type="button" className={activeFolder === "done" ? "action-done is-active" : "action-done"} title={activeFolder === "done" ? "Move to Inbox" : "Mark done"} onClick={() => quickUpdate(row, { folder: activeFolder === "done" ? "inbox" : "done" })}><CheckCircleOutlineOutlined /></button>
                        <button type="button" className={activeFolder === "archived" ? "action-archive is-active" : "action-archive"} title={activeFolder === "archived" ? "Unarchive" : "Archive"} onClick={() => quickUpdate(row, { folder: activeFolder === "archived" ? "inbox" : "archived" })}><ArchiveOutlined /></button>
                      </div>
                    ) : null}
                  </article>
                );
              })}
            </div>
          </aside>

          {!conversationListOpen ? <button type="button" className="restore-list-button" title="Show conversation list" onClick={() => setConversationListOpen(true)}><ChevronRightOutlined /></button> : null}

          <main className="unified-chat-area">
            {hasSelectedConversation ? (
              <ConversationDetailPage
                embedded
                channelOverride={routeChannel}
                userIdOverride={routeUserId}
                onExit={() => navigate("/conversations")}
                onConversationChanged={() => loadConversations({ silent: true })}
              />
            ) : (
              <div className="unified-chat-placeholder"><ChatBubbleOutlineOutlined /><h2>Select a conversation</h2><p>Choose a customer conversation from the list.</p></div>
            )}
          </main>
        </div>
      </AppCard>
    </div>
  );
}
