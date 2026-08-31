import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import {
  AddOutlined,
  AlternateEmailOutlined,
  CloseOutlined,
  ForumOutlined,
  GroupAddOutlined,
  LockOutlined,
  LogoutOutlined,
  PersonAddAltOutlined,
  SendOutlined,
  TagOutlined,
} from "@mui/icons-material";
import { AppButton, AppCard, ErrorState } from "../../components/common";
import {
  addTeamChannelMemberRequest,
  createTeamChannelRequest,
  editTeamMessageRequest,
  getTeamChannelMembersRequest,
  getTeamChatOverviewRequest,
  getTeamMessagesRequest,
  joinTeamChannelRequest,
  leaveTeamChannelRequest,
  markTeamChannelReadRequest,
  postTeamMessageRequest,
  subscribeTeamChatEvents,
} from "../../api/teamChat";
import "./TeamChatPage.css";

const PAGE_SIZE = 50;

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
  return date.toLocaleString([], {
    month: "short",
    day: "numeric",
    hour: "2-digit",
    minute: "2-digit",
  });
}

function initials(name) {
  const text = String(name || "?").trim();
  const parts = text.split(/\s+/).slice(0, 2);
  return parts.map((part) => part.charAt(0).toUpperCase()).join("") || "?";
}

/** Split a body so `@mention` runs can be highlighted without dangerous HTML. */
function renderBody(body) {
  const text = String(body || "");
  const pieces = text.split(/(@[\w.\-']+(?:[ \t][\w.\-']+){0,2})/g);

  return pieces.map((piece, index) =>
    piece.startsWith("@") ? (
      <mark className="team-chat-mention" key={`${piece}-${index}`}>
        {piece}
      </mark>
    ) : (
      <span key={`text-${index}`}>{piece}</span>
    ),
  );
}

/** The `@word` being typed immediately before the caret, if any. */
function mentionQueryAt(text, caret) {
  const upToCaret = String(text || "").slice(0, caret);
  const match = upToCaret.match(/(?:^|\s)@([\w.\-']*)$/);
  return match ? match[1] : null;
}

export default function TeamChatPage() {
  const [channels, setChannels] = useState([]);
  const [directory, setDirectory] = useState([]);
  const [currentUserId, setCurrentUserId] = useState(null);
  const [activeChannelId, setActiveChannelId] = useState(null);
  const [messages, setMessages] = useState([]);
  const [members, setMembers] = useState([]);
  const [nextBeforeId, setNextBeforeId] = useState(null);
  const [hasMore, setHasMore] = useState(false);

  const [loading, setLoading] = useState(true);
  const [threadLoading, setThreadLoading] = useState(false);
  const [error, setError] = useState("");
  const [notice, setNotice] = useState("");
  const [liveConnected, setLiveConnected] = useState(false);

  const [draft, setDraft] = useState("");
  const [sending, setSending] = useState(false);
  const [mentionQuery, setMentionQuery] = useState(null);
  const [mentionIndex, setMentionIndex] = useState(0);

  const [editingId, setEditingId] = useState(null);
  const [editingText, setEditingText] = useState("");

  const [dialogOpen, setDialogOpen] = useState(false);
  const [newChannel, setNewChannel] = useState({
    name: "",
    topic: "",
    isPrivate: false,
    memberUserIds: [],
  });
  const [creating, setCreating] = useState(false);

  const [inviteUserId, setInviteUserId] = useState("");

  const composerRef = useRef(null);
  const threadEndRef = useRef(null);
  const activeChannelRef = useRef(null);
  activeChannelRef.current = activeChannelId;

  const activeChannel = useMemo(
    () => channels.find((channel) => channel.id === activeChannelId) || null,
    [channels, activeChannelId],
  );

  const unreadTotal = useMemo(
    () => channels.reduce((sum, channel) => sum + Number(channel.unread_count || 0), 0),
    [channels],
  );

  const mentionMatches = useMemo(() => {
    if (mentionQuery === null) return [];
    const needle = mentionQuery.toLowerCase();
    return directory
      .filter((person) => {
        const name = String(person.display_name || person.email || "").toLowerCase();
        return !needle || name.replace(/\s+/g, "").includes(needle.replace(/\s+/g, ""));
      })
      .slice(0, 6);
  }, [directory, mentionQuery]);

  // ------------------------------------------------------------------
  // Loading
  // ------------------------------------------------------------------

  const loadOverview = useCallback(async ({ silent = false } = {}) => {
    if (!silent) setLoading(true);
    try {
      const result = await getTeamChatOverviewRequest();
      const list = Array.isArray(result?.channels) ? result.channels : [];
      setChannels(list);
      setDirectory(Array.isArray(result?.directory) ? result.directory : []);
      setCurrentUserId(result?.current_user_id ?? null);
      setError("");
      setActiveChannelId((current) => {
        if (current && list.some((channel) => channel.id === current)) return current;
        return list.length ? list[0].id : null;
      });
    } catch (requestError) {
      setError(requestError.message || "Team chat could not be loaded.");
    } finally {
      setLoading(false);
    }
  }, []);

  const loadThread = useCallback(async (channelId, { silent = false } = {}) => {
    if (!channelId) {
      setMessages([]);
      setMembers([]);
      return;
    }
    if (!silent) setThreadLoading(true);
    try {
      const [page, memberList] = await Promise.all([
        getTeamMessagesRequest(channelId, { limit: PAGE_SIZE }),
        getTeamChannelMembersRequest(channelId).catch(() => ({ items: [] })),
      ]);
      if (activeChannelRef.current !== channelId) return;
      setMessages(Array.isArray(page?.items) ? page.items : []);
      setHasMore(Boolean(page?.has_more));
      setNextBeforeId(page?.next_before_id ?? null);
      setMembers(Array.isArray(memberList?.items) ? memberList.items : []);
      setError("");
    } catch (requestError) {
      setError(requestError.message || "This channel could not be opened.");
    } finally {
      setThreadLoading(false);
    }
  }, []);

  useEffect(() => {
    loadOverview();
  }, [loadOverview]);

  useEffect(() => {
    setEditingId(null);
    setDraft("");
    setMentionQuery(null);
    loadThread(activeChannelId);
  }, [activeChannelId, loadThread]);

  // Opening a channel clears its badge for this user only.
  useEffect(() => {
    if (!activeChannelId) return undefined;
    let cancelled = false;

    markTeamChannelReadRequest(activeChannelId)
      .then(() => {
        if (cancelled) return;
        setChannels((current) =>
          current.map((channel) =>
            channel.id === activeChannelId ? { ...channel, unread_count: 0 } : channel,
          ),
        );
      })
      .catch(() => {});

    return () => {
      cancelled = true;
    };
  }, [activeChannelId, messages.length]);

  useEffect(() => {
    threadEndRef.current?.scrollIntoView({ block: "end" });
  }, [messages]);

  // Live updates: the server only sends a payload when something the viewer is
  // allowed to see actually changed.
  useEffect(() => {
    const controller = new AbortController();

    subscribeTeamChatEvents({
      channelId: activeChannelId,
      signal: controller.signal,
      onOpen: () => setLiveConnected(true),
      onError: () => setLiveConnected(false),
      onEvent: ({ data }) => {
        if (!data) return;
        if (Array.isArray(data.channels)) setChannels(data.channels);
        if (
          Array.isArray(data.messages) &&
          data.channel_id === activeChannelRef.current
        ) {
          setMessages(data.messages);
        }
      },
    }).finally(() => setLiveConnected(false));

    return () => controller.abort();
  }, [activeChannelId]);

  async function loadOlder() {
    if (!activeChannelId || !nextBeforeId) return;
    try {
      const page = await getTeamMessagesRequest(activeChannelId, {
        limit: PAGE_SIZE,
        beforeId: nextBeforeId,
      });
      const older = Array.isArray(page?.items) ? page.items : [];
      setMessages((current) => [...older, ...current]);
      setHasMore(Boolean(page?.has_more));
      setNextBeforeId(page?.next_before_id ?? null);
    } catch (requestError) {
      setError(requestError.message || "Older messages could not be loaded.");
    }
  }

  // ------------------------------------------------------------------
  // Composer
  // ------------------------------------------------------------------

  function handleDraftChange(event) {
    const { value, selectionStart } = event.target;
    setDraft(value);
    const query = mentionQueryAt(value, selectionStart ?? value.length);
    setMentionQuery(query);
    setMentionIndex(0);
  }

  function insertMention(person) {
    const element = composerRef.current;
    const caret = element?.selectionStart ?? draft.length;
    const before = draft.slice(0, caret).replace(/@[\w.\-']*$/, "");
    const after = draft.slice(caret);
    const name = person.display_name || person.email || `User ${person.id}`;
    const next = `${before}@${name} ${after}`;

    setDraft(next);
    setMentionQuery(null);
    window.requestAnimationFrame(() => {
      element?.focus();
      const position = before.length + name.length + 2;
      element?.setSelectionRange(position, position);
    });
  }

  function handleComposerKeyDown(event) {
    if (mentionQuery !== null && mentionMatches.length) {
      if (event.key === "ArrowDown") {
        event.preventDefault();
        setMentionIndex((index) => (index + 1) % mentionMatches.length);
        return;
      }
      if (event.key === "ArrowUp") {
        event.preventDefault();
        setMentionIndex(
          (index) => (index - 1 + mentionMatches.length) % mentionMatches.length,
        );
        return;
      }
      if (event.key === "Enter" || event.key === "Tab") {
        event.preventDefault();
        insertMention(mentionMatches[mentionIndex] || mentionMatches[0]);
        return;
      }
      if (event.key === "Escape") {
        event.preventDefault();
        setMentionQuery(null);
        return;
      }
    }

    if (event.key === "Enter" && !event.shiftKey) {
      event.preventDefault();
      sendMessage();
    }
  }

  async function sendMessage() {
    const text = draft.trim();
    if (!text || !activeChannelId || sending) return;

    setSending(true);
    try {
      const message = await postTeamMessageRequest(activeChannelId, text);
      setMessages((current) => [...current, message]);
      setDraft("");
      setMentionQuery(null);
      setError("");
      loadOverview({ silent: true });
    } catch (requestError) {
      setError(requestError.message || "The message could not be sent.");
    } finally {
      setSending(false);
    }
  }

  function startEditing(message) {
    setEditingId(message.id);
    setEditingText(message.body);
  }

  function cancelEditing() {
    setEditingId(null);
    setEditingText("");
  }

  async function saveEdit() {
    const text = editingText.trim();
    if (!text || !editingId) return;
    try {
      const updated = await editTeamMessageRequest(editingId, text);
      setMessages((current) =>
        current.map((item) => (item.id === updated.id ? updated : item)),
      );
      cancelEditing();
    } catch (requestError) {
      setError(requestError.message || "The message could not be edited.");
    }
  }

  // ------------------------------------------------------------------
  // Channel actions
  // ------------------------------------------------------------------

  async function createChannel() {
    const name = newChannel.name.trim();
    if (!name || creating) return;

    setCreating(true);
    try {
      const created = await createTeamChannelRequest({
        name,
        topic: newChannel.topic.trim(),
        isPrivate: newChannel.isPrivate,
        memberUserIds: newChannel.memberUserIds,
      });
      setDialogOpen(false);
      setNewChannel({ name: "", topic: "", isPrivate: false, memberUserIds: [] });
      await loadOverview({ silent: true });
      setActiveChannelId(created.id);
      setNotice(`#${created.name} is ready.`);
    } catch (requestError) {
      setError(requestError.message || "The channel could not be created.");
    } finally {
      setCreating(false);
    }
  }

  function toggleNewChannelMember(userId) {
    setNewChannel((current) => {
      const exists = current.memberUserIds.includes(userId);
      return {
        ...current,
        memberUserIds: exists
          ? current.memberUserIds.filter((item) => item !== userId)
          : [...current.memberUserIds, userId],
      };
    });
  }

  async function joinChannel() {
    if (!activeChannelId) return;
    try {
      await joinTeamChannelRequest(activeChannelId);
      await loadOverview({ silent: true });
      await loadThread(activeChannelId, { silent: true });
    } catch (requestError) {
      setError(requestError.message || "You could not join this channel.");
    }
  }

  async function leaveChannel() {
    if (!activeChannelId) return;
    try {
      await leaveTeamChannelRequest(activeChannelId);
      setActiveChannelId(null);
      await loadOverview({ silent: true });
    } catch (requestError) {
      setError(requestError.message || "You could not leave this channel.");
    }
  }

  async function inviteMember() {
    if (!activeChannelId || !inviteUserId) return;
    try {
      await addTeamChannelMemberRequest(activeChannelId, Number(inviteUserId));
      setInviteUserId("");
      await loadThread(activeChannelId, { silent: true });
      await loadOverview({ silent: true });
    } catch (requestError) {
      setError(requestError.message || "That person could not be added.");
    }
  }

  const memberIds = useMemo(
    () => new Set(members.map((member) => Number(member.user_id))),
    [members],
  );

  // ------------------------------------------------------------------
  // Render
  // ------------------------------------------------------------------

  return (
    <div className="team-chat-page">
      <AppCard padding="none" className="team-chat-shell">
        <div className="team-chat-grid">
          <aside className="team-chat-sidebar">
            <header className="team-chat-sidebar-header">
              <div>
                <strong>Team chat</strong>
                <span>
                  {unreadTotal > 0 ? `${unreadTotal} unread` : "Internal discussion"}
                </span>
              </div>
              <button
                type="button"
                className="team-chat-icon-button"
                title="Create a channel"
                onClick={() => setDialogOpen(true)}
              >
                <AddOutlined />
              </button>
            </header>

            <div className="team-chat-channel-list">
              {loading ? <p className="team-chat-empty">Loading channels...</p> : null}

              {!loading && channels.length === 0 ? (
                <p className="team-chat-empty">
                  No channels yet. Create the first one.
                </p>
              ) : null}

              {channels.map((channel) => (
                <button
                  type="button"
                  key={channel.id}
                  className={`team-chat-channel ${
                    channel.id === activeChannelId ? "is-active" : ""
                  } ${Number(channel.unread_count) > 0 ? "is-unread" : ""}`}
                  onClick={() => setActiveChannelId(channel.id)}
                >
                  {channel.is_private ? <LockOutlined /> : <TagOutlined />}
                  <span className="team-chat-channel-name">{channel.name}</span>
                  {Number(channel.unread_count) > 0 ? (
                    <b className="team-chat-badge">{channel.unread_count}</b>
                  ) : null}
                </button>
              ))}
            </div>

            <footer className="team-chat-sidebar-footer">
              <span className={liveConnected ? "team-chat-live is-on" : "team-chat-live"}>
                {liveConnected ? "Live" : "Reconnecting"}
              </span>
            </footer>
          </aside>

          <main className="team-chat-main">
            {/* A failed send must not blank the thread, so only a failed load
                replaces the screen; everything else is a dismissible banner. */}
            {error && channels.length === 0 && !loading ? (
              <ErrorState
                title="Team chat problem"
                description={error}
                action={
                  <AppButton variant="primary" onClick={() => loadOverview()}>
                    Retry
                  </AppButton>
                }
              />
            ) : null}

            {error && channels.length > 0 ? (
              <div className="team-chat-error-banner" role="alert">
                <span>{error}</span>
                <button type="button" onClick={() => setError("")}>
                  <CloseOutlined />
                </button>
              </div>
            ) : null}

            {!activeChannel && channels.length > 0 ? (
              <div className="team-chat-placeholder">
                <ForumOutlined />
                <h2>Select a channel</h2>
                <p>Pick a channel on the left, or create one for your team.</p>
              </div>
            ) : null}

            {!activeChannel && channels.length === 0 && !loading && !error ? (
              <div className="team-chat-placeholder">
                <ForumOutlined />
                <h2>No channels yet</h2>
                <p>Create the first channel for your team.</p>
                <AppButton
                  variant="primary"
                  icon={<AddOutlined />}
                  onClick={() => setDialogOpen(true)}
                >
                  Create a channel
                </AppButton>
              </div>
            ) : null}

            {activeChannel ? (
              <>
                <header className="team-chat-thread-header">
                  <div className="team-chat-thread-title">
                    <h2>
                      {activeChannel.is_private ? <LockOutlined /> : <TagOutlined />}
                      {activeChannel.name}
                    </h2>
                    <p>
                      {activeChannel.topic || "No topic"} ·{" "}
                      {activeChannel.member_count} member
                      {activeChannel.member_count === 1 ? "" : "s"}
                    </p>
                  </div>

                  <div className="team-chat-thread-actions">
                    {activeChannel.is_member ? (
                      <>
                        <label className="team-chat-invite">
                          <PersonAddAltOutlined />
                          <select
                            value={inviteUserId}
                            onChange={(event) => setInviteUserId(event.target.value)}
                          >
                            <option value="">Add someone…</option>
                            {directory
                              .filter((person) => !memberIds.has(Number(person.id)))
                              .map((person) => (
                                <option value={person.id} key={person.id}>
                                  {person.display_name || person.email}
                                </option>
                              ))}
                          </select>
                        </label>
                        <AppButton
                          variant="secondary"
                          size="small"
                          icon={<GroupAddOutlined />}
                          disabled={!inviteUserId}
                          onClick={inviteMember}
                        >
                          Add
                        </AppButton>
                        <AppButton
                          variant="ghost"
                          size="small"
                          icon={<LogoutOutlined />}
                          onClick={leaveChannel}
                        >
                          Leave
                        </AppButton>
                      </>
                    ) : (
                      <AppButton variant="primary" size="small" onClick={joinChannel}>
                        Join channel
                      </AppButton>
                    )}
                  </div>
                </header>

                <div className="team-chat-thread">
                  {threadLoading ? (
                    <p className="team-chat-empty">Loading messages...</p>
                  ) : null}

                  {hasMore ? (
                    <div className="team-chat-older">
                      <AppButton variant="ghost" size="small" onClick={loadOlder}>
                        Load earlier messages
                      </AppButton>
                    </div>
                  ) : null}

                  {!threadLoading && messages.length === 0 ? (
                    <p className="team-chat-empty">
                      Nothing here yet. Start the discussion.
                    </p>
                  ) : null}

                  {messages.map((message) => {
                    const mine = Number(message.author_user_id) === Number(currentUserId);
                    return (
                      <article
                        className={`team-chat-message ${mine ? "is-mine" : ""}`}
                        key={message.id}
                      >
                        <div className="team-chat-avatar">
                          {initials(message.author_name)}
                        </div>
                        <div className="team-chat-bubble">
                          <div className="team-chat-bubble-top">
                            <strong>{message.author_name}</strong>
                            <time>{formatTime(message.created_at)}</time>
                            {message.edited_at ? <em>edited</em> : null}
                            {mine && editingId !== message.id ? (
                              <button
                                type="button"
                                className="team-chat-edit-link"
                                onClick={() => startEditing(message)}
                              >
                                Edit
                              </button>
                            ) : null}
                          </div>

                          {editingId === message.id ? (
                            <div className="team-chat-edit-box">
                              <textarea
                                value={editingText}
                                rows={2}
                                onChange={(event) => setEditingText(event.target.value)}
                              />
                              <div className="team-chat-edit-actions">
                                <AppButton size="small" variant="primary" onClick={saveEdit}>
                                  Save
                                </AppButton>
                                <AppButton
                                  size="small"
                                  variant="ghost"
                                  onClick={cancelEditing}
                                >
                                  Cancel
                                </AppButton>
                              </div>
                            </div>
                          ) : (
                            <p>{renderBody(message.body)}</p>
                          )}
                        </div>
                      </article>
                    );
                  })}

                  <div ref={threadEndRef} />
                </div>

                <footer className="team-chat-composer">
                  {mentionQuery !== null && mentionMatches.length ? (
                    <div className="team-chat-mention-menu">
                      {mentionMatches.map((person, index) => (
                        <button
                          type="button"
                          key={person.id}
                          className={index === mentionIndex ? "is-active" : ""}
                          onMouseEnter={() => setMentionIndex(index)}
                          onClick={() => insertMention(person)}
                        >
                          <AlternateEmailOutlined />
                          <span>{person.display_name || person.email}</span>
                          {person.role_name ? <em>{person.role_name}</em> : null}
                        </button>
                      ))}
                    </div>
                  ) : null}

                  <textarea
                    ref={composerRef}
                    rows={2}
                    value={draft}
                    placeholder={
                      activeChannel.is_member
                        ? "Write a message. Use @ to mention a colleague."
                        : "Post here to join #" + activeChannel.name
                    }
                    onChange={handleDraftChange}
                    onKeyDown={handleComposerKeyDown}
                  />
                  <AppButton
                    variant="primary"
                    icon={<SendOutlined />}
                    disabled={!draft.trim()}
                    loading={sending}
                    onClick={sendMessage}
                  >
                    Send
                  </AppButton>
                </footer>
              </>
            ) : null}
          </main>
        </div>
      </AppCard>

      {notice ? (
        <div className="team-chat-notice" role="status">
          <span>{notice}</span>
          <button type="button" onClick={() => setNotice("")}>
            <CloseOutlined />
          </button>
        </div>
      ) : null}

      {dialogOpen ? (
        <div className="team-chat-dialog-backdrop" role="presentation">
          <section className="team-chat-dialog" role="dialog" aria-label="Create a channel">
            <header>
              <h3>Create a channel</h3>
              <button
                type="button"
                className="team-chat-icon-button"
                onClick={() => setDialogOpen(false)}
              >
                <CloseOutlined />
              </button>
            </header>

            <label className="team-chat-field">
              <span>Name</span>
              <input
                value={newChannel.name}
                placeholder="shift-handover"
                onChange={(event) =>
                  setNewChannel((current) => ({ ...current, name: event.target.value }))
                }
              />
            </label>

            <label className="team-chat-field">
              <span>Topic</span>
              <input
                value={newChannel.topic}
                placeholder="What is this channel for?"
                onChange={(event) =>
                  setNewChannel((current) => ({ ...current, topic: event.target.value }))
                }
              />
            </label>

            <label className="team-chat-checkbox">
              <input
                type="checkbox"
                checked={newChannel.isPrivate}
                onChange={(event) =>
                  setNewChannel((current) => ({
                    ...current,
                    isPrivate: event.target.checked,
                  }))
                }
              />
              <span>
                Private — only the people you add can see this channel or its messages.
              </span>
            </label>

            {newChannel.isPrivate ? (
              <div className="team-chat-member-picker">
                {directory.map((person) => (
                  <label key={person.id}>
                    <input
                      type="checkbox"
                      checked={newChannel.memberUserIds.includes(person.id)}
                      onChange={() => toggleNewChannelMember(person.id)}
                    />
                    <span>{person.display_name || person.email}</span>
                  </label>
                ))}
              </div>
            ) : null}

            <footer>
              <AppButton variant="ghost" onClick={() => setDialogOpen(false)}>
                Cancel
              </AppButton>
              <AppButton
                variant="primary"
                loading={creating}
                disabled={!newChannel.name.trim()}
                onClick={createChannel}
              >
                Create
              </AppButton>
            </footer>
          </section>
        </div>
      ) : null}
    </div>
  );
}
