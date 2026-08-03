import { useEffect, useMemo, useState } from "react";
import {
  chatWithBotRequest,
  listAiTeachingChatRequest,
  listDepartmentsRequest,
  sendAiTeachingChatRequest,
  testAiReplyRequest,
} from "../../api/client";
import { AppCard, LoadingState } from "../../components/common";
import { useAuth } from "../../contexts/AuthContext";
import { CHANNEL_OPTIONS } from "./TagPicker";

function TrainChat() {
  const [messages, setMessages] = useState([]);
  const [draft, setDraft] = useState("");
  const [loading, setLoading] = useState(true);
  const [sending, setSending] = useState(false);
  const [error, setError] = useState("");
  const [forbidden, setForbidden] = useState(false);

  useEffect(() => {
    listAiTeachingChatRequest()
      .then((result) => setMessages(Array.isArray(result?.messages) ? result.messages : []))
      .catch((requestError) => {
        if (requestError.status === 403) setForbidden(true);
        else setError(requestError.message || "Could not load the teaching chat.");
      })
      .finally(() => setLoading(false));
  }, []);

  async function submit(event) {
    event.preventDefault();
    const text = draft.trim();
    if (!text) return;
    setSending(true);
    setError("");
    try {
      const result = await sendAiTeachingChatRequest(text);
      setMessages((current) => [...current, result.manager_message, result.assistant_message]);
      setDraft("");
    } catch (requestError) {
      if (requestError.status === 403) setForbidden(true);
      else setError(requestError.message || "Message could not be sent.");
    } finally {
      setSending(false);
    }
  }

  if (forbidden) {
    return (
      <AppCard padding="medium">
        <h3 className="client-file-section-title">Train — Chat with your AI</h3>
        <p className="ai-teaching-section-hint">You don't have permission to use this yet — ask an owner/admin to grant "Use AI Teaching Chat Module" from Roles &amp; Permissions.</p>
      </AppCard>
    );
  }

  return (
    <AppCard padding="medium">
      <h3 className="client-file-section-title">Train — Chat with your AI</h3>
      <p className="ai-teaching-section-hint">Talk to the AI directly — when you give it an instruction, it confirms and remembers it (saved into Instructions automatically). Manager/admin only.</p>
      {loading ? (
        <LoadingState title="Loading chat..." />
      ) : (
        <>
          <div className="ai-teaching-chat-log">
            {messages.length === 0 ? <p className="ai-teaching-empty-hint">No messages yet — try "Always greet customers in Arabic first."</p> : null}
            {messages.map((message) => (
              <div className={`ai-teaching-chat-bubble ${message.role === "manager" ? "is-manager" : "is-assistant"}`} key={message.id}>
                <span>{message.text}</span>
                {message.instruction_saved ? <em>Saved as a new instruction</em> : null}
              </div>
            ))}
          </div>
          {error ? <p className="customer-segment-error">{error}</p> : null}
          <form className="ai-teaching-chat-form" onSubmit={submit}>
            <input value={draft} disabled={sending} placeholder="Teach the AI something..." onChange={(event) => setDraft(event.target.value)} />
            <button type="submit" className="btn btn-primary" disabled={sending || !draft.trim()}>{sending ? "Sending…" : "Send"}</button>
          </form>
        </>
      )}
    </AppCard>
  );
}

function TestYourAI() {
  const [message, setMessage] = useState("");
  const [channel, setChannel] = useState("website");
  const [department, setDepartment] = useState("");
  const [departments, setDepartments] = useState([]);
  const [testing, setTesting] = useState(false);
  const [error, setError] = useState("");
  const [result, setResult] = useState(null);

  useEffect(() => {
    listDepartmentsRequest()
      .then((res) => setDepartments((res?.departments || []).map((d) => d.name || d)))
      .catch(() => {});
  }, []);

  async function submit(event) {
    event.preventDefault();
    if (!message.trim()) return;
    setTesting(true);
    setError("");
    setResult(null);
    try {
      const response = await testAiReplyRequest({ message: message.trim(), channel, department: department || undefined });
      setResult(response);
    } catch (requestError) {
      setError(requestError.message || "Could not get a test reply.");
    } finally {
      setTesting(false);
    }
  }

  return (
    <AppCard padding="medium">
      <h3 className="client-file-section-title">Test your AI</h3>
      <p className="ai-teaching-section-hint">
        Type a message like a real customer would — this runs through the exact same pipeline a real conversation uses
        (your Instructions + Knowledge, scoped to the channel/department you pick below), so what you see here is genuinely what a customer would get. Nothing is saved anywhere.
      </p>
      <form onSubmit={submit} style={{ display: "flex", flexDirection: "column", gap: 10 }}>
        <div style={{ display: "flex", gap: 10, flexWrap: "wrap" }}>
          <select className="tz-select" value={channel} onChange={(event) => setChannel(event.target.value)}>
            {CHANNEL_OPTIONS.map((option) => <option value={option} key={option}>{option}</option>)}
          </select>
          <select className="tz-select" value={department} onChange={(event) => setDepartment(event.target.value)}>
            <option value="">Any department</option>
            {departments.filter((d) => d !== "Unassigned").map((d) => <option value={d} key={d}>{d}</option>)}
          </select>
        </div>
        <textarea
          rows={3}
          value={message}
          disabled={testing}
          onChange={(event) => setMessage(event.target.value)}
          placeholder="e.g. How much does the internet package cost?"
          style={{ padding: "8px 10px", borderRadius: 8, border: "1px solid #d5dae5" }}
        />
        <button type="submit" className="btn btn-primary" disabled={testing || !message.trim()} style={{ alignSelf: "flex-start" }}>
          {testing ? "Sending…" : "Send test message"}
        </button>
      </form>

      {error ? <p className="customer-segment-error">{error}</p> : null}

      {result ? (
        <div className="ai-teaching-test-result">
          <div className="ai-teaching-chat-bubble is-assistant">
            <span>{result.reply}</span>
          </div>
          <p className="ai-teaching-test-meta">
            Detected department: <strong>{result.department_detected || "unknown"}</strong>
            {result.knowledge_used?.length ? <> · Knowledge used: {result.knowledge_used.join(", ")}</> : " · No knowledge entry matched"}
          </p>
        </div>
      ) : null}
    </AppCard>
  );
}

function ChatWithYourBot() {
  const [messages, setMessages] = useState([]);
  const [draft, setDraft] = useState("");
  const [sending, setSending] = useState(false);
  const [error, setError] = useState("");

  async function submit(event) {
    event.preventDefault();
    const text = draft.trim();
    if (!text) return;
    setSending(true);
    setError("");
    const outgoing = { id: `me-${Date.now()}`, role: "manager", text };
    setMessages((current) => [...current, outgoing]);
    setDraft("");
    try {
      const result = await chatWithBotRequest({ message: text, channel: "website" });
      setMessages((current) => [...current, { id: `bot-${Date.now()}`, role: "assistant", text: result.reply }]);
    } catch (requestError) {
      setError(requestError.message || "The bot could not reply — try again.");
    } finally {
      setSending(false);
    }
  }

  return (
    <AppCard padding="medium">
      <h3 className="client-file-section-title">Chat with your bot</h3>
      <p className="ai-teaching-section-hint">
        Talk to your company's AI exactly like a customer would — this is the real bot, not a demo.
        Ask it anything a customer might ask, so you know what they'll see.
      </p>
      <div className="ai-teaching-chat-log">
        {messages.length === 0 ? <p className="ai-teaching-empty-hint">No messages yet — try "How much does the internet package cost?"</p> : null}
        {messages.map((message) => (
          <div className={`ai-teaching-chat-bubble ${message.role === "manager" ? "is-manager" : "is-assistant"}`} key={message.id}>
            <span>{message.text}</span>
          </div>
        ))}
      </div>
      {error ? <p className="customer-segment-error">{error}</p> : null}
      <form className="ai-teaching-chat-form" onSubmit={submit}>
        <input value={draft} disabled={sending} placeholder="Type a message..." onChange={(event) => setDraft(event.target.value)} />
        <button type="submit" className="btn btn-primary" disabled={sending || !draft.trim()}>{sending ? "Sending…" : "Send"}</button>
      </form>
    </AppCard>
  );
}

export default function TrainAndTestPage() {
  const { user, companies } = useAuth();
  const canUseTeachingTools = useMemo(() => {
    if (user?.is_super_admin) return true;
    const activeCompany = companies.find((company) => company.id === user?.active_company_id) || companies[0];
    return activeCompany?.role_code === "owner" || (activeCompany?.permission_codes || []).includes("modules.ai_teaching_chat");
  }, [user, companies]);

  if (!canUseTeachingTools) {
    return <ChatWithYourBot />;
  }

  return (
    <>
      <TrainChat />
      <TestYourAI />
    </>
  );
}
