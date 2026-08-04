import { useCallback, useEffect, useRef, useState } from "react";
import {
  AddOutlined,
  DeleteOutlineOutlined,
  EventNoteOutlined,
  LockOutlined,
  RefreshOutlined,
  SendOutlined,
} from "@mui/icons-material";

import {
  createTeamChatRoomRequest,
  deleteTeamChatRoomRequest,
  getTeamChatMessagesRequest,
  getTeamChatRoomsRequest,
  postTeamChatMessageRequest,
} from "../../api/client";
import {
  AppButton,
  AppCard,
  ConfirmDialog,
  EmptyState,
  ErrorState,
  PageHeader,
} from "../../components/common";
import { useAuth } from "../../contexts/AuthContext";
import "./TeamChatPage.css";

const POLL_INTERVAL_MS = 4000;

function formatTime(value) {
  if (!value) return "";
  const date = new Date(value);
  if (Number.isNaN(date.getTime())) return "";
  return date.toLocaleString(undefined, {
    month: "short",
    day: "numeric",
    hour: "2-digit",
    minute: "2-digit",
  });
}

export default function TeamChatPage() {
  const { user, hasPermission } = useAuth();
  const canView = hasPermission("team_chat.view");
  const canPost = hasPermission("team_chat.post");
  const canManage = hasPermission("team_chat.manage");

  const [rooms, setRooms] = useState([]);
  const [roomsError, setRoomsError] = useState("");
  const [activeRoomId, setActiveRoomId] = useState(null);

  const [messages, setMessages] = useState([]);
  const [messagesLoading, setMessagesLoading] = useState(false);
  const [messagesError, setMessagesError] = useState("");
  const [hasOlder, setHasOlder] = useState(false);
  const [loadingOlder, setLoadingOlder] = useState(false);

  const [draft, setDraft] = useState("");
  const [sending, setSending] = useState(false);

  const [roomDialogOpen, setRoomDialogOpen] = useState(false);
  const [roomName, setRoomName] = useState("");
  const [roomDescription, setRoomDescription] = useState("");
  const [roomSaving, setRoomSaving] = useState(false);
  const [roomError, setRoomError] = useState("");

  const [deleteRoomTarget, setDeleteRoomTarget] = useState(null);
  const [deletingRoom, setDeletingRoom] = useState(false);

  const listRef = useRef(null);
  const activeRoomRef = useRef(null);
  activeRoomRef.current = activeRoomId;

  const loadRooms = useCallback(async () => {
    if (!canView) return;
    setRoomsError("");
    try {
      const result = await getTeamChatRoomsRequest();
      const items = Array.isArray(result?.items) ? result.items : [];
      setRooms(items);
      setActiveRoomId((current) => {
        if (current && items.some((room) => room.id === current)) return current;
        return items[0]?.id ?? null;
      });
    } catch (err) {
      setRoomsError(err.message || "Rooms could not be loaded.");
    }
  }, [canView]);

  useEffect(() => {
    loadRooms();
  }, [loadRooms]);

  const scrollToBottom = useCallback(() => {
    const node = listRef.current;
    if (node) node.scrollTop = node.scrollHeight;
  }, []);

  // Full (re)load when the active room changes.
  useEffect(() => {
    if (!activeRoomId || !canView) {
      setMessages([]);
      return;
    }
    let cancelled = false;
    setMessagesLoading(true);
    setMessagesError("");
    setMessages([]);
    getTeamChatMessagesRequest(activeRoomId, { limit: 50 })
      .then((result) => {
        if (cancelled) return;
        const items = Array.isArray(result?.items) ? result.items : [];
        setMessages(items);
        setHasOlder(items.length === 50);
        requestAnimationFrame(scrollToBottom);
      })
      .catch((err) => {
        if (cancelled) return;
        setMessagesError(err.message || "Messages could not be loaded.");
      })
      .finally(() => {
        if (!cancelled) setMessagesLoading(false);
      });
    return () => {
      cancelled = true;
    };
  }, [activeRoomId, canView, scrollToBottom]);

  // Poll for new messages in the active room.
  useEffect(() => {
    if (!activeRoomId || !canView) return undefined;
    const interval = setInterval(async () => {
      const roomAtRequest = activeRoomRef.current;
      if (!roomAtRequest) return;
      try {
        const lastId = messages.length ? messages[messages.length - 1].id : null;
        const result = await getTeamChatMessagesRequest(roomAtRequest, {
          afterId: lastId ?? undefined,
          limit: 50,
        });
        if (activeRoomRef.current !== roomAtRequest) return;
        const items = Array.isArray(result?.items) ? result.items : [];
        if (!items.length) return;
        setMessages((current) => {
          const known = new Set(current.map((m) => m.id));
          const fresh = items.filter((m) => !known.has(m.id));
          if (!fresh.length) return current;
          requestAnimationFrame(scrollToBottom);
          return [...current, ...fresh];
        });
      } catch {
        // Silent: transient polling failures self-heal on the next tick.
      }
    }, POLL_INTERVAL_MS);
    return () => clearInterval(interval);
  }, [activeRoomId, canView, messages, scrollToBottom]);

  async function handleLoadOlder() {
    if (!activeRoomId || !messages.length || loadingOlder) return;
    setLoadingOlder(true);
    try {
      const result = await getTeamChatMessagesRequest(activeRoomId, {
        beforeId: messages[0].id,
        limit: 50,
      });
      const items = Array.isArray(result?.items) ? result.items : [];
      setHasOlder(items.length === 50);
      if (items.length) {
        setMessages((current) => {
          const known = new Set(current.map((m) => m.id));
          return [...items.filter((m) => !known.has(m.id)), ...current];
        });
      }
    } catch (err) {
      setMessagesError(err.message || "Older messages could not be loaded.");
    } finally {
      setLoadingOlder(false);
    }
  }

  async function handleSend() {
    const body = draft.trim();
    if (!body || !activeRoomId || sending) return;
    setSending(true);
    setMessagesError("");
    try {
      const message = await postTeamChatMessageRequest(activeRoomId, { body });
      setDraft("");
      setMessages((current) =>
        current.some((m) => m.id === message.id) ? current : [...current, message],
      );
      requestAnimationFrame(scrollToBottom);
    } catch (err) {
      setMessagesError(
        (typeof err?.data?.detail === "string" ? err.data.detail : null) ||
          err.message ||
          "The message could not be sent.",
      );
    } finally {
      setSending(false);
    }
  }

  async function handleCreateRoom() {
    const name = roomName.trim();
    if (!name) {
      setRoomError("A room name is required.");
      return;
    }
    setRoomSaving(true);
    setRoomError("");
    try {
      const room = await createTeamChatRoomRequest({
        name,
        description: roomDescription.trim() || null,
      });
      setRoomDialogOpen(false);
      setRoomName("");
      setRoomDescription("");
      await loadRooms();
      setActiveRoomId(room.id);
    } catch (err) {
      setRoomError(
        (typeof err?.data?.detail === "string" ? err.data.detail : null) ||
          err.message ||
          "The room could not be created.",
      );
    } finally {
      setRoomSaving(false);
    }
  }

  async function handleDeleteRoom() {
    if (!deleteRoomTarget) return;
    setDeletingRoom(true);
    try {
      await deleteTeamChatRoomRequest(deleteRoomTarget.id);
      setDeleteRoomTarget(null);
      await loadRooms();
    } catch (err) {
      setRoomsError(
        (typeof err?.data?.detail === "string" ? err.data.detail : null) ||
          err.message ||
          "The room could not be deleted.",
      );
      setDeleteRoomTarget(null);
    } finally {
      setDeletingRoom(false);
    }
  }

  if (!canView) {
    return (
      <section className="team-chat-page">
        <PageHeader
          eyebrow="TEAM CHAT"
          title="Team Chat"
          description="Internal messages, follow-ups, shared files and instructions without private WhatsApp groups."
        />
        <AppCard padding="large">
          <EmptyState
            icon={<LockOutlined />}
            title="You don't have access to Team Chat"
            description="Ask a company administrator to grant you the “View Team Chat” permission."
          />
        </AppCard>
      </section>
    );
  }

  const activeRoom = rooms.find((room) => room.id === activeRoomId) || null;

  return (
    <section className="team-chat-page">
      <PageHeader
        eyebrow="TEAM CHAT"
        title="Team Chat"
        description="Internal team messaging — organized into rooms, visible to your whole company team."
        actions={
          <div className="team-chat-header-actions">
            <AppButton
              variant="secondary"
              icon={<RefreshOutlined fontSize="small" />}
              onClick={loadRooms}
            >
              Refresh
            </AppButton>
            {canManage ? (
              <AppButton
                variant="primary"
                icon={<AddOutlined fontSize="small" />}
                onClick={() => {
                  setRoomError("");
                  setRoomDialogOpen(true);
                }}
              >
                New room
              </AppButton>
            ) : null}
          </div>
        }
      />

      <div className="team-chat-layout">
        <AppCard padding="small" className="team-chat-rooms-card">
          <h4 className="team-chat-rooms-heading">Rooms</h4>
          {roomsError ? <p className="team-chat-error">{roomsError}</p> : null}
          <nav className="team-chat-room-list">
            {rooms.map((room) => (
              <div
                key={room.id}
                className={`team-chat-room-row${room.id === activeRoomId ? " is-active" : ""}`}
              >
                <button
                  type="button"
                  className="team-chat-room-button"
                  onClick={() => setActiveRoomId(room.id)}
                >
                  <strong>{room.name}</strong>
                  {room.last_message_at ? (
                    <span>{formatTime(room.last_message_at)}</span>
                  ) : (
                    <span>No messages yet</span>
                  )}
                </button>
                {canManage && !room.is_default ? (
                  <button
                    type="button"
                    className="team-chat-room-delete"
                    aria-label={`Delete room ${room.name}`}
                    onClick={() => setDeleteRoomTarget(room)}
                  >
                    <DeleteOutlineOutlined fontSize="small" />
                  </button>
                ) : null}
              </div>
            ))}
          </nav>
        </AppCard>

        <AppCard padding="small" className="team-chat-messages-card">
          {!activeRoom ? (
            <EmptyState
              icon={<EventNoteOutlined />}
              title="No room selected"
              description="Pick a room on the left to start reading and posting messages."
            />
          ) : (
            <>
              <header className="team-chat-room-header">
                <div>
                  <strong>{activeRoom.name}</strong>
                  {activeRoom.description ? <span>{activeRoom.description}</span> : null}
                </div>
              </header>

              {messagesError ? (
                <ErrorState
                  title="Something went wrong"
                  description={messagesError}
                  action={
                    <AppButton
                      variant="primary"
                      icon={<RefreshOutlined fontSize="small" />}
                      onClick={() => setActiveRoomId((id) => id)}
                    >
                      Try again
                    </AppButton>
                  }
                />
              ) : (
                <div className="team-chat-message-list" ref={listRef}>
                  {hasOlder ? (
                    <div className="team-chat-load-older">
                      <AppButton
                        size="small"
                        variant="secondary"
                        loading={loadingOlder}
                        onClick={handleLoadOlder}
                      >
                        Load older messages
                      </AppButton>
                    </div>
                  ) : null}

                  {messagesLoading ? (
                    <p className="team-chat-loading">Loading messages…</p>
                  ) : messages.length === 0 ? (
                    <p className="team-chat-loading">
                      No messages yet — start the conversation.
                    </p>
                  ) : (
                    messages.map((message) => {
                      const mine = user && message.sender_user_id === user.id;
                      return (
                        <div
                          key={message.id}
                          className={`team-chat-message${mine ? " is-mine" : ""}`}
                        >
                          <div className="team-chat-message-meta">
                            <strong>
                              {mine
                                ? "You"
                                : message.sender_name || message.sender_email || "Team member"}
                            </strong>
                            <span>{formatTime(message.created_at)}</span>
                          </div>
                          <p className="team-chat-message-body">{message.body}</p>
                        </div>
                      );
                    })
                  )}
                </div>
              )}

              {canPost ? (
                <div className="team-chat-composer">
                  <textarea
                    value={draft}
                    disabled={sending}
                    placeholder={`Message #${activeRoom.name}`}
                    onChange={(event) => setDraft(event.target.value)}
                    onKeyDown={(event) => {
                      if (event.key === "Enter" && !event.shiftKey) {
                        event.preventDefault();
                        handleSend();
                      }
                    }}
                  />
                  <AppButton
                    variant="primary"
                    icon={<SendOutlined fontSize="small" />}
                    loading={sending}
                    disabled={!draft.trim()}
                    onClick={handleSend}
                  >
                    Send
                  </AppButton>
                </div>
              ) : (
                <p className="team-chat-readonly-note">
                  <LockOutlined fontSize="small" /> You have read-only access. Ask
                  an administrator for the &quot;Post in Team Chat&quot; permission
                  to send messages.
                </p>
              )}
            </>
          )}
        </AppCard>
      </div>

      {roomDialogOpen ? (
        <div
          className="tz-dialog-backdrop"
          role="presentation"
          onMouseDown={(event) => {
            if (event.target === event.currentTarget && !roomSaving) setRoomDialogOpen(false);
          }}
        >
          <section
            className="tz-dialog team-chat-room-dialog"
            role="dialog"
            aria-modal="true"
            aria-labelledby="team-chat-room-title"
          >
            <header className="tz-dialog-header">
              <h3 id="team-chat-room-title">New room</h3>
              <button
                type="button"
                className="tz-dialog-close"
                aria-label="Close"
                onClick={() => (roomSaving ? null : setRoomDialogOpen(false))}
              >
                ×
              </button>
            </header>
            <div className="tz-dialog-body">
              <div className="team-chat-room-form">
                <label>
                  <span>Name</span>
                  <input
                    type="text"
                    value={roomName}
                    disabled={roomSaving}
                    placeholder="e.g. Support Shift, Sales"
                    onChange={(event) => setRoomName(event.target.value)}
                  />
                </label>
                <label>
                  <span>Description (optional)</span>
                  <input
                    type="text"
                    value={roomDescription}
                    disabled={roomSaving}
                    placeholder="What is this room for?"
                    onChange={(event) => setRoomDescription(event.target.value)}
                  />
                </label>
                {roomError ? <p className="team-chat-error">{roomError}</p> : null}
              </div>
            </div>
            <footer className="tz-dialog-actions">
              <AppButton
                variant="secondary"
                disabled={roomSaving}
                onClick={() => setRoomDialogOpen(false)}
              >
                Cancel
              </AppButton>
              <AppButton variant="primary" loading={roomSaving} onClick={handleCreateRoom}>
                Create room
              </AppButton>
            </footer>
          </section>
        </div>
      ) : null}

      <ConfirmDialog
        open={Boolean(deleteRoomTarget)}
        title="Delete room"
        confirmLabel="Delete"
        cancelLabel="Cancel"
        confirmVariant="danger"
        loading={deletingRoom}
        onConfirm={handleDeleteRoom}
        onCancel={() => (deletingRoom ? null : setDeleteRoomTarget(null))}
        message={
          deleteRoomTarget ? (
            <p>
              Delete <strong>{deleteRoomTarget.name}</strong> and all its messages?
              This cannot be undone.
            </p>
          ) : null
        }
      />
    </section>
  );
}
