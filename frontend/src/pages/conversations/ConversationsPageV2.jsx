import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import {
  AddOutlined,
  ArchiveOutlined,
  CheckCircleOutlineOutlined,
  ChevronLeftOutlined,
  ChevronRightOutlined,
  SearchOutlined,
  StarOutlineOutlined,
} from "@mui/icons-material";
import { useNavigate, useParams } from "react-router-dom";
import {
  getConversationsRequest,
  updateConversationControlRequest,
} from "../../api/client";
import { ErrorState } from "../../components/common";
import { useConversationLive } from "../../contexts/ConversationLiveContext";
import { channelIcon } from "../community/channelIcon";
import ConversationDetailPageV2 from "./ConversationDetailPageV2";
import "./ConversationsPageV2.css";

// Same data/handlers as ConversationsPage.jsx (v1) — this is a visual
// rebuild of the thread-list side (CLAUDE_CODE_UI_IMPLEMENTATION.md §3).
// The detail pane now renders ConversationDetailPageV2, which reuses every
// request call and piece of state from ConversationDetailPage.jsx (v1)
// verbatim — attachments, voice notes, takeover, transfer — and only
// rebuilds the JSX/classnames. v1's ConversationDetailPage.jsx is left
// untouched and still backs the standalone `/conversations/:channel/:userId/full`
// route (outside AppLayout, so it never gets the .tzv2-scoped design-system
// classes ConversationDetailPageV2 relies on).
// Matches the @media (max-width: 940px) rule in ConversationsPageV2.css, where
// the list stops being a column beside the thread and becomes an overlay on top
// of it. Change one and change the other.
const NARROW_SCREEN_PX = 940;

// Matches the media query in ConversationsPageV2.css, where the list stops
// being a column beside the thread and becomes an overlay on top of it.
function isNarrowScreen() {
  return typeof window !== "undefined" && window.innerWidth <= NARROW_SCREEN_PX;
}

const CHANNELS = ["all", "messenger", "whatsapp", "instagram", "telegram"];
const FOLDERS = [
  { value: "inbox", label: "Inbox" },
  { value: "assigned_to_me", label: "Assigned to me" },
  { value: "unread", label: "Unread" },
  { value: "starred", label: "Starred" },
  { value: "done", label: "Done" },
  { value: "archived", label: "Archived" },
];

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

function formatTime(value) {
  const date = parseServerDate(value);
  if (!date) return "";
  const sameDay = date.toDateString() === new Date().toDateString();
  return sameDay ? date.toLocaleTimeString([], { hour: "2-digit", minute: "2-digit" }) : date.toLocaleDateString();
}

// Real elapsed-time label, not a fabricated number — computed from the
// conversation's own last-activity timestamp. Only shown while a human
// is expected to act (waiting/pending, or handed to a human with no
// reply yet); AI-handled or resolved/closed conversations don't need it.
function slaLabel(row) {
  const waitingStatuses = ["waiting_customer", "waiting_agent", "pending", "human_handling"];
  if (!waitingStatuses.includes(row.status)) return null;
  const since = parseServerDate(row.updated_at);
  if (!since) return null;
  const minutes = Math.max(0, Math.round((Date.now() - since.getTime()) / 60000));
  const elapsed = minutes < 60 ? `${minutes} min` : `${Math.round(minutes / 60)} h`;
  return row.assigned_user_name ? `Waiting ${elapsed}` : `Unassigned ${elapsed}`;
}

function truncate(value, maximum = 90) {
  const text = String(value || "").trim();
  return text.length <= maximum ? text : `${text.slice(0, maximum)}…`;
}

export default function ConversationsPageV2() {
  const navigate = useNavigate();
  const { channel: routeChannel, userId: routeUserId } = useParams();
  const live = useConversationLive();
  const liveRef = useRef(live);
  liveRef.current = live;

  const [rows, setRows] = useState([]);
  const [channelCounts, setChannelCounts] = useState({});
  const [availableChannels, setAvailableChannels] = useState([]);
  const [activeChannel, setActiveChannel] = useState("all");
  const [activeFolder, setActiveFolder] = useState("inbox");
  const [statusChip, setStatusChip] = useState("all");
  const [searchInput, setSearchInput] = useState("");
  const [search, setSearch] = useState("");
  const [readStatus, setReadStatus] = useState("all");
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState("");
  // On a narrow screen the list and the thread occupy the same space, so which
  // one is showing is a property of *where you are*, not of what you last
  // tapped: a conversation in the URL means the thread, no conversation means
  // the list. Deriving it from the route is what makes the browser's back
  // button, a refresh and a shared link all behave -- an initial `true` left
  // the list overlaying the thread on every one of those paths, swallowing
  // every tap meant for the conversation underneath it. On a wide screen both
  // panes fit, so the list simply stays.
  const [listOpen, setListOpen] = useState(
    () => !(isNarrowScreen() && Boolean(routeChannel && routeUserId))
  );

  const loadConversations = useCallback(async ({ silent = false } = {}) => {
    silent ? null : setLoading(true);
    setError("");
    try {
      const result = await getConversationsRequest({
        search,
        channel: activeChannel,
        status: statusChip === "all" ? "all" : statusChip,
        department: "all",
        assignedUserId: null,
        folder: activeFolder,
        tag: "",
        readStatus,
        page: 1,
        pageSize: 100,
      });
      setRows(Array.isArray(result?.items) ? result.items : []);
      setChannelCounts(result?.channel_counts || {});
      setAvailableChannels(Array.isArray(result?.available_channels) ? result.available_channels : []);
    } catch (requestError) {
      setError(requestError.message || "Conversations could not be loaded.");
    } finally {
      setLoading(false);
    }
  }, [search, activeChannel, statusChip, activeFolder, readStatus]);

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

  // Re-derive on arrival at a conversation and on leaving one, so the back
  // button restores the list the same way tapping a row hid it. Keyed on the
  // route rather than on every render, which leaves the toggle free to
  // override the derived value for as long as you stay on that screen.
  useEffect(() => {
    if (!isNarrowScreen()) {
      setListOpen(true);
      return;
    }

    setListOpen(!(routeChannel && routeUserId));
  }, [routeChannel, routeUserId]);

  // Rotating a phone, or dragging a desktop window across the breakpoint,
  // changes which of the two layouts is on screen. Without this, widening past
  // the breakpoint keeps the list hidden with no column to bring it back into,
  // and narrowing leaves it covering the thread again.
  useEffect(() => {
    if (typeof window === "undefined") {
      return undefined;
    }

    const query = window.matchMedia(`(max-width: ${NARROW_SCREEN_PX}px)`);

    function apply(event) {
      setListOpen(!event.matches || !(routeChannel && routeUserId));
    }

    query.addEventListener("change", apply);
    return () => query.removeEventListener("change", apply);
  }, [routeChannel, routeUserId]);

  const enabledChannels = useMemo(() => new Set(availableChannels), [availableChannels]);
  const totalCount = Object.values(channelCounts).reduce((sum, value) => sum + Number(value || 0), 0);
  const hasSelectedConversation = Boolean(routeChannel && routeUserId);

  function openConversation(row) {
    navigate(`/conversations/${encodeURIComponent(row.channel)}/${encodeURIComponent(row.external_user_id)}`);
  }

  async function quickUpdate(row, updates, event) {
    event?.stopPropagation();
    if (!row.can_manage) return;
    try {
      await updateConversationControlRequest(row.channel, row.external_user_id, updates);
      await loadConversations({ silent: true });
    } catch (requestError) {
      setError(requestError.message || "Conversation action failed.");
    }
  }

  return (
    <div className="tzv2-conv-page">
      <div className="tzv2-conv-filterbar">
        <div className="tzv2-conv-filterbar-row">
          <select className="tzv2-select" value={activeFolder} onChange={(event) => {
            setActiveFolder(event.target.value);
            if (event.target.value === "unread") setReadStatus("unread");
            else if (readStatus === "unread") setReadStatus("all");
            navigate("/conversations");
          }}>
            {FOLDERS.map((folder) => (
              <option value={folder.value} key={folder.value}>{folder.label}</option>
            ))}
          </select>
          <div className="tzv2-channel-filter" role="group" aria-label="Filter by channel">
            <button
              type="button"
              className={`tzv2-channel-chip ${activeChannel === "all" ? "is-active" : ""}`}
              onClick={() => setActiveChannel("all")}
            >
              All channels
              {totalCount ? <span className="tzv2-channel-chip-count">{totalCount}</span> : null}
            </button>
            {/* Only the channels this company has actually connected. A chip
                for a channel nobody linked is a filter that can only ever
                return nothing, and greying it out still spends header room on
                four names most companies do not all use. `available_channels`
                is the API's list of connected accounts, not the list of
                channels the platform supports. */}
            {CHANNELS.filter(
              (name) => name !== "all" && enabledChannels.has(name),
            ).map((name) => {
              const Icon = channelIcon(name);
              const count = Number(channelCounts[name] || 0);
              return (
                <button
                  type="button"
                  key={name}
                  className={`tzv2-channel-chip ${activeChannel === name ? "is-active" : ""}`}
                  style={{ "--channel-color": `var(--color-channel-${name})` }}
                  title={humanize(name)}
                  onClick={() => setActiveChannel(name)}
                >
                  <Icon fontSize="inherit" />
                  {humanize(name)}
                  {count ? <span className="tzv2-channel-chip-count">{count}</span> : null}
                </button>
              );
            })}
          </div>
          <button
            type="button"
            className="btn btn-primary tzv2-new-conv"
            disabled
            title="Starting a new outbound conversation isn't available yet — conversations begin when a customer messages a connected channel."
          >
            <AddOutlined fontSize="small" /> New conversation
          </button>
        </div>
      </div>

      <div className="tzv2-conv-body">
        {listOpen ? (
          <aside className="tzv2-conv-list">
            <div className="tzv2-conv-list-head">
              <label className="tzv2-conv-search">
                <SearchOutlined fontSize="small" />
                <input value={searchInput} placeholder="Search name, number, tag…" onChange={(event) => setSearchInput(event.target.value)} />
              </label>
              <div className="tzv2-status-chips">
                {["all", "waiting_customer", "waiting_agent"].map((value) => (
                  <button
                    type="button"
                    key={value}
                    className={`tag ${statusChip === value ? "tag-outline" : "tag-neutral"}`}
                    onClick={() => setStatusChip(value)}
                  >
                    {value === "all" ? "Open" : value === "waiting_customer" ? "Waiting" : "Needs agent"}
                  </button>
                ))}
              </div>
            </div>
            <div className="tzv2-conv-rows">
              {loading ? <div className="tzv2-conv-empty">Loading…</div> : null}
              {error ? <ErrorState title="Could not load conversations" description={error} action={<button type="button" className="btn btn-primary" onClick={() => loadConversations()}>Retry</button>} /> : null}
              {!loading && !error && rows.length === 0 ? <div className="tzv2-conv-empty">No conversations here.</div> : null}
              {!loading && !error && rows.map((row) => {
                const selected = routeChannel === row.channel && routeUserId === row.external_user_id;
                const unread = Number(row.unread_count || 0) > 0 || row.is_unread;
                const sla = slaLabel(row);
                return (
                  <div
                    role="button"
                    tabIndex={0}
                    key={row.id}
                    className={`tzv2-conv-row ${selected ? "is-selected" : ""}`}
                    onClick={() => openConversation(row)}
                    onKeyDown={(event) => { if (event.key === "Enter") openConversation(row); }}
                  >
                    <div className="tzv2-conv-avatar">{(row.customer_alias || row.customer_name || "?").charAt(0).toUpperCase()}</div>
                    <div className="tzv2-conv-main">
                      <div className="tzv2-conv-topline">
                        <strong>{row.customer_name || "Unknown Customer"}</strong>
                        {unread ? <span className="tzv2-unread-dot" /> : null}
                        <span className="tzv2-conv-time">{formatTime(row.updated_at)}</span>
                      </div>
                      <p className="tzv2-conv-preview">{truncate(row.last_message) || "No message text"}</p>
                      <div className="tzv2-conv-metaline">
                        <span className={`tzv2-channel-dot tzv2-channel-${row.channel}`} />
                        <span className="tzv2-conv-channel">{humanize(row.channel)}</span>
                        <span className="tzv2-conv-sep" />
                        <span className="tzv2-conv-owner">{row.assigned_user_name || "Unassigned"}</span>
                        {sla ? <span className="tzv2-conv-sla">{sla}</span> : null}
                      </div>
                    </div>
                    {row.can_manage ? (
                      <div className="tzv2-conv-row-actions">
                        <button type="button" title={row.is_starred ? "Unstar" : "Star"} onClick={(event) => quickUpdate(row, { is_starred: !row.is_starred }, event)}>
                          <StarOutlineOutlined fontSize="inherit" />
                        </button>
                        <button
                          type="button"
                          title={activeFolder === "done" ? "Move to inbox" : "Mark done"}
                          onClick={(event) => quickUpdate(row, { folder: activeFolder === "done" ? "inbox" : "done" }, event)}
                        >
                          <CheckCircleOutlineOutlined fontSize="inherit" />
                        </button>
                        <button
                          type="button"
                          title={activeFolder === "archived" ? "Unarchive" : "Archive"}
                          onClick={(event) => quickUpdate(row, { folder: activeFolder === "archived" ? "inbox" : "archived" }, event)}
                        >
                          <ArchiveOutlined fontSize="inherit" />
                        </button>
                      </div>
                    ) : null}
                  </div>
                );
              })}
            </div>
          </aside>
        ) : null}

        <button type="button" className="tzv2-list-toggle" title={listOpen ? "Hide list" : "Show list"} onClick={() => setListOpen((value) => !value)}>
          {listOpen ? <ChevronLeftOutlined fontSize="small" /> : <ChevronRightOutlined fontSize="small" />}
        </button>

        <main className="tzv2-conv-detail">
          {hasSelectedConversation ? (
            <ConversationDetailPageV2
              embedded
              channelOverride={routeChannel}
              userIdOverride={routeUserId}
              onExit={() => navigate("/conversations")}
              onConversationChanged={() => loadConversations({ silent: true })}
            />
          ) : (
            <div className="tzv2-conv-empty-state">
              <h3>No conversation open</h3>
              <p>Pick a conversation from the list. While none is open, the assistant keeps handling the queue on its own.</p>
            </div>
          )}
        </main>
      </div>
    </div>
  );
}
