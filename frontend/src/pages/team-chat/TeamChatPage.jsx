import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { DeleteOutlineOutlined, SendOutlined } from "@mui/icons-material";
import {
  deleteTeamMessageRequest,
  getCurrentUserRequest,
  listTeamMessagesRequest,
  sendTeamMessageRequest,
  teamChatOptionsRequest,
} from "../../api/client";
import { AppCard, ErrorState, LoadingState, PageHeader } from "../../components/common";
import "./TeamChatPage.css";

const POLL_INTERVAL_MS = 5000;

function formatTime(value) {
  const date = new Date(value);
  if (Number.isNaN(date.getTime())) return "";
  const today = new Date();
  const isToday = date.toDateString() === today.toDateString();
  const time = date.toLocaleTimeString([], { hour: "2-digit", minute: "2-digit" });
  return isToday ? time : `${date.toLocaleDateString()} ${time}`;
}

function renderMessageText(text, employees) {
  const names = employees.map((employee) => employee.full_name).filter(Boolean).sort((a, b) => b.length - a.length);
  if (names.length === 0) return text;
  const pattern = new RegExp(`(@(?:${names.map((name) => name.replace(/[.*+?^${}()|[\]\\]/g, "\\$&")).join("|")}))`, "g");
  return text.split(pattern).map((part, index) => (
    pattern.test(part) ? <strong key={index} className="team-chat-mention">{part}</strong> : <span key={index}>{part}</span>
  ));
}

export default function TeamChatPage() {
  const [currentUserId, setCurrentUserId] = useState(null);
  const [employees, setEmployees] = useState([]);
  const [messages, setMessages] = useState([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState("");
  const [draft, setDraft] = useState("");
  const [mentionedUserIds, setMentionedUserIds] = useState([]);
  const [sending, setSending] = useState(false);
  const [mentionQuery, setMentionQuery] = useState(null);
  const listRef = useRef(null);
  const textareaRef = useRef(null);

  const load = useCallback(async () => {
    try {
      const result = await listTeamMessagesRequest({ limit: 100 });
      setMessages(Array.isArray(result?.items) ? result.items : []);
      setError("");
    } catch (requestError) {
      setError(requestError.message || "Team chat could not be loaded.");
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => {
    getCurrentUserRequest().then((result) => setCurrentUserId(result?.user?.id ?? null)).catch(() => {});
    teamChatOptionsRequest()
      .then((result) => setEmployees(Array.isArray(result?.employees) ? result.employees : []))
      .catch(() => {});
  }, []);

  useEffect(() => {
    load();
    const interval = window.setInterval(load, POLL_INTERVAL_MS);
    return () => window.clearInterval(interval);
  }, [load]);

  useEffect(() => {
    if (listRef.current) listRef.current.scrollTop = listRef.current.scrollHeight;
  }, [messages]);

  const mentionSuggestions = useMemo(() => {
    if (mentionQuery === null) return [];
    const query = mentionQuery.toLowerCase();
    return employees.filter((employee) => (employee.display_name || "").toLowerCase().includes(query)).slice(0, 6);
  }, [mentionQuery, employees]);

  function handleDraftChange(event) {
    const value = event.target.value;
    setDraft(value);
    const cursor = event.target.selectionStart;
    const upToCursor = value.slice(0, cursor);
    const match = upToCursor.match(/@([^\s@]*)$/);
    setMentionQuery(match ? match[1] : null);
  }

  function insertMention(employee) {
    const cursor = textareaRef.current ? textareaRef.current.selectionStart : draft.length;
    const upToCursor = draft.slice(0, cursor);
    const replaced = upToCursor.replace(/@([^\s@]*)$/, `@${employee.display_name} `);
    const nextDraft = `${replaced}${draft.slice(cursor)}`;
    setDraft(nextDraft);
    setMentionQuery(null);
    setMentionedUserIds((current) => (current.includes(employee.id) ? current : [...current, employee.id]));
    window.requestAnimationFrame(() => textareaRef.current?.focus());
  }

  async function handleSend(event) {
    event.preventDefault();
    const text = draft.trim();
    if (!text || sending) return;
    setSending(true);
    try {
      await sendTeamMessageRequest({ text, mentioned_user_ids: mentionedUserIds });
      setDraft("");
      setMentionedUserIds([]);
      setMentionQuery(null);
      await load();
    } catch (requestError) {
      setError(requestError.message || "Message could not be sent.");
    } finally {
      setSending(false);
    }
  }

  function handleKeyDown(event) {
    if (event.key === "Enter" && !event.shiftKey && mentionQuery === null) {
      event.preventDefault();
      handleSend(event);
    }
  }

  async function handleDelete(messageId) {
    try {
      await deleteTeamMessageRequest(messageId);
      setMessages((current) => current.filter((message) => message.id !== messageId));
    } catch (requestError) {
      setError(requestError.message || "Message could not be deleted.");
    }
  }

  return (
    <section className="team-chat-page">
      <PageHeader />

      {error ? <ErrorState title="Team chat error" description={error} /> : null}

      <AppCard padding="none" className="team-chat-card">
        {loading ? (
          <LoadingState label="Loading team chat…" />
        ) : (
          <div className="team-chat-messages" ref={listRef}>
            {messages.length === 0 ? (
              <div className="team-chat-empty">No messages yet — say hello to your team.</div>
            ) : (
              messages.map((message) => {
                const isOwn = message.sender_user_id === currentUserId;
                return (
                  <div key={message.id} className={`team-chat-message ${isOwn ? "is-own" : ""}`}>
                    <div className="team-chat-message-header">
                      <span className="team-chat-sender">{message.sender_name}</span>
                      <span className="team-chat-time">{formatTime(message.created_at)}</span>
                    </div>
                    <div className="team-chat-bubble">
                      <p>{renderMessageText(message.text, employees)}</p>
                      {isOwn ? (
                        <button
                          type="button"
                          className="team-chat-delete"
                          title="Delete message"
                          onClick={() => handleDelete(message.id)}
                        >
                          <DeleteOutlineOutlined fontSize="inherit" />
                        </button>
                      ) : null}
                    </div>
                  </div>
                );
              })
            )}
          </div>
        )}

        <form className="team-chat-composer" onSubmit={handleSend}>
          {mentionSuggestions.length > 0 ? (
            <div className="team-chat-mention-menu">
              {mentionSuggestions.map((employee) => (
                <button type="button" key={employee.id} onClick={() => insertMention(employee)}>
                  {employee.display_name}
                </button>
              ))}
            </div>
          ) : null}
          <textarea
            ref={textareaRef}
            value={draft}
            placeholder="Message your team… use @ to mention someone"
            onChange={handleDraftChange}
            onKeyDown={handleKeyDown}
            rows={2}
          />
          <button type="submit" className="team-chat-send" disabled={sending || !draft.trim()}>
            <SendOutlined fontSize="small" />
          </button>
        </form>
      </AppCard>
    </section>
  );
}
