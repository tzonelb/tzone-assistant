import { useEffect, useRef, useState } from "react";
import {
  sendVerificationCodeRequest,
  verifyCodeRequest,
  listMyChannelsRequest,
  connectTelegramRequest,
  connectWhatsAppRequest,
  connectInstagramRequest,
  disconnectChannelRequest,
  startFacebookOAuthRequest,
  getMySubscriptionRequest,
  getSessionChangesRequest,
} from "../../api/client";
import { CHANNEL_CATEGORIES, CHANNEL_LABELS } from "./channelCatalog";
import { resolveChannelIcon } from "./channelIcons";
import "./SecureChannelsPanel.css";

const PURPOSE = "channels_access";

function ChannelsOverview({ channels, usage, onConnect }) {
  const connectedByKey = channels.reduce((map, ch) => {
    (map[ch.channel] ||= []).push(ch);
    return map;
  }, {});

  const used = usage?.used ?? channels.filter((c) => c.status === "active").length;
  const max = usage?.max ?? null;
  const pct = max ? Math.min(100, Math.round((used / max) * 100)) : 0;

  return (
    <div className="channels-overview">
      <div className="channels-plan-banner">
        <div className="channels-plan-banner-main">
          <strong>Connected channels</strong>
          <span>
            {max != null
              ? `${used} of ${max} channels used on your plan`
              : `${used} channel${used === 1 ? "" : "s"} connected`}
          </span>
        </div>
        {max != null ? (
          <div className="channels-plan-usage">
            <div className="channels-plan-usage-bar">
              <div className={`channels-plan-usage-fill ${used >= max ? "is-full" : ""}`} style={{ width: `${pct}%` }} />
            </div>
            <div className="channels-plan-usage-label">{used >= max ? "Plan limit reached" : `${max - used} remaining`}</div>
          </div>
        ) : null}
      </div>

      {channels.length ? (
        <div className="channels-connected-list">
          {channels.map((c) => (
            <div className="channels-connected-row" key={c.id}>
              <div className="channels-connected-row-main">
                <strong>{c.name}</strong>
                <span>{CHANNEL_LABELS[c.channel] || c.channel}</span>
              </div>
              <span className="channels-connected-status">{c.status}</span>
            </div>
          ))}
        </div>
      ) : null}

      {CHANNEL_CATEGORIES.map((category) => (
        <div className="channels-directory-category" key={category.title}>
          <h4>{category.title}</h4>
          <div className="channels-directory-grid">
            {category.channels.map((channel) => {
              const connected = connectedByKey[channel.key] || [];
              const Icon = resolveChannelIcon(channel.icon);
              const badge = connected.length
                ? { cls: "is-connected", label: connected.length > 1 ? `${connected.length} connected` : "Connected" }
                : channel.availability === "available"
                ? { cls: "is-available", label: "Available" }
                : { cls: "is-soon", label: "Coming soon" };
              return (
                <div className="channels-directory-card" key={channel.key}>
                  <div className="channels-directory-card-icon" style={{ background: `${channel.color}1a`, color: channel.color }}>
                    <Icon fontSize="small" />
                  </div>
                  <div className="channels-directory-card-body">
                    <div className="channels-directory-card-head">
                      <span className="channels-directory-card-name">{channel.name}</span>
                      <span className={`channels-directory-badge ${badge.cls}`}>{badge.label}</span>
                    </div>
                    <span className="channels-directory-card-note">
                      {channel.note || (connected.length ? connected.map((c) => c.name).join(" · ") : " ")}
                    </span>
                  </div>
                  <button
                    type="button"
                    className="channels-directory-card-connect"
                    disabled={channel.availability !== "available"}
                    onClick={() => onConnect?.(channel)}
                  >
                    {channel.availability !== "available" ? "Coming soon" : "Connect"}
                  </button>
                </div>
              );
            })}
          </div>
        </div>
      ))}
    </div>
  );
}

export default function SecureChannelsPanel() {
  // Viewing what's connected is always open — no code required. A code is
  // only ever requested at the moment of an actual connect/disconnect
  // action (real credentials changing hands), via `withVerification` below.
  const [channels, setChannels] = useState([]);
  const [usage, setUsage] = useState(null);
  const [error, setError] = useState("");
  const [busy, setBusy] = useState(false);
  const [oauthMessage, setOauthMessage] = useState(null);

  const [telegramToken, setTelegramToken] = useState("");
  const [waPhoneId, setWaPhoneId] = useState("");
  const [waToken, setWaToken] = useState("");
  const [igPageId, setIgPageId] = useState("");
  const [igToken, setIgToken] = useState("");

  // Inline verification prompt state — appears only when an action needs it.
  const [verifyOpen, setVerifyOpen] = useState(false);
  const [verifyStage, setVerifyStage] = useState("send"); // send -> code_sent
  const [emailHint, setEmailHint] = useState("");
  const [code, setCode] = useState("");
  const elevatedTokenRef = useRef(null);
  const pendingActionRef = useRef(null);
  const [hasVerifiedSession, setHasVerifiedSession] = useState(false);
  const [sessionChanges, setSessionChanges] = useState(null);
  const [changesLoading, setChangesLoading] = useState(false);

  const sectionRefs = useRef({});

  async function loadChannels() {
    try {
      const result = await listMyChannelsRequest();
      setChannels(result.channels || []);
    } catch (e) {
      setError(e.message);
    }
    try {
      const sub = await getMySubscriptionRequest();
      setUsage(sub?.channels || null);
    } catch {
      setUsage(null);
    }
  }

  useEffect(() => {
    loadChannels();
    const params = new URLSearchParams(window.location.search);
    const connected = params.get("fb_connected");
    const fbError = params.get("fb_error");
    if (connected) {
      setOauthMessage({ type: "success", text: `Connected ${connected} channel(s) via Facebook.` });
    } else if (fbError) {
      setOauthMessage({ type: "error", text: `Facebook connection failed: ${fbError}` });
    }
    if (connected || fbError) {
      window.history.replaceState({}, "", window.location.pathname);
    }
  }, []);

  function goToConnectSection(connectKey) {
    if (connectKey === "facebook") {
      handleFacebookConnect();
      return;
    }
    const el = sectionRefs.current[connectKey];
    if (el) {
      el.scrollIntoView({ behavior: "smooth", block: "center" });
      const input = el.querySelector("input");
      if (input) input.focus();
    }
  }

  function handleConnectClick(channel) {
    goToConnectSection(channel.connect);
  }

  async function handleFacebookConnect() {
    // Facebook's own OAuth login IS the verification — no extra code needed.
    setBusy(true);
    setError("");
    try {
      const result = await startFacebookOAuthRequest();
      window.location.href = result.authorize_url;
    } catch (e) {
      setError(e.message);
      setBusy(false);
    }
  }

  // Runs `action(elevatedToken)` — if we don't have a valid token for this
  // session yet, opens the inline verification prompt first and re-runs the
  // same action automatically once the code is confirmed.
  async function withVerification(action) {
    if (elevatedTokenRef.current) {
      await withErrorHandling(() => action(elevatedTokenRef.current));
      return;
    }
    pendingActionRef.current = action;
    setVerifyOpen(true);
    setVerifyStage("send");
    setError("");
  }

  async function handleSendCode() {
    setBusy(true);
    setError("");
    try {
      const result = await sendVerificationCodeRequest(PURPOSE);
      setEmailHint(result.email_hint);
      setVerifyStage("code_sent");
    } catch (e) {
      setError(e.message);
    } finally {
      setBusy(false);
    }
  }

  async function handleVerifyCode(e) {
    e.preventDefault();
    setBusy(true);
    setError("");
    try {
      const result = await verifyCodeRequest(PURPOSE, code);
      elevatedTokenRef.current = result.elevated_token;
      setHasVerifiedSession(true);
      setCode("");
      setVerifyOpen(false);
      const action = pendingActionRef.current;
      pendingActionRef.current = null;
      if (action) {
        await withErrorHandling(() => action(result.elevated_token));
      }
    } catch (e) {
      setError(e.message);
    } finally {
      setBusy(false);
    }
  }

  async function withErrorHandling(fn) {
    setBusy(true);
    setError("");
    try {
      await fn();
      await loadChannels();
    } catch (e) {
      setError(e.message);
    } finally {
      setBusy(false);
    }
  }

  // The Security tab tells the admin this exists ("see Channels tab,
  // 'Done — show what changed'") — this is that button. Fetches every
  // connect/disconnect logged during the current verified session, then
  // ends the session so the next action asks for a fresh code.
  async function showSessionChanges() {
    if (!elevatedTokenRef.current) return;
    setChangesLoading(true);
    setError("");
    try {
      const result = await getSessionChangesRequest(PURPOSE, elevatedTokenRef.current);
      setSessionChanges(result.changes || []);
    } catch (e) {
      setError(e.message);
    } finally {
      setChangesLoading(false);
    }
  }

  function closeSessionChanges() {
    setSessionChanges(null);
    elevatedTokenRef.current = null;
    setHasVerifiedSession(false);
  }

  const inputStyle = { flex: 1, padding: "10px 12px", borderRadius: 8, border: "1px solid #d5dae5" };
  const formStyle = { display: "flex", gap: 12, padding: "0 0 20px", flexWrap: "wrap" };

  return (
    <div className="company-setting-fields">
      {oauthMessage ? (
        <p style={{ color: oauthMessage.type === "success" ? "#1e7e34" : "#c0392b", fontWeight: 600 }}>
          {oauthMessage.text}
        </p>
      ) : null}
      {error ? <p style={{ color: "#c0392b" }}>{error}</p> : null}

      {hasVerifiedSession && !verifyOpen ? (
        <article className="company-setting-field channels-verify-inline">
          <div>
            <strong>Verified session active</strong>
            <span>Connect or disconnect as many channels as you need — no new code required until this session ends.</span>
          </div>
          <button type="button" onClick={showSessionChanges} disabled={changesLoading}>
            {changesLoading ? "Loading…" : "Done — show what changed"}
          </button>
        </article>
      ) : null}

      {sessionChanges !== null ? (
        <div className="tz-dialog-backdrop" role="presentation" onMouseDown={(event) => { if (event.target === event.currentTarget) closeSessionChanges(); }}>
          <div className="tz-dialog">
            <header className="tz-dialog-header">
              <h3>What changed this session</h3>
            </header>
            <div className="tz-dialog-body">
              {sessionChanges.length ? (
                <ul style={{ margin: 0, paddingLeft: 18, display: "flex", flexDirection: "column", gap: 8 }}>
                  {sessionChanges.map((change, index) => (
                    <li key={index}>
                      {change.description}
                      <br />
                      <span style={{ fontSize: 12, color: "#6b7280" }}>{new Date(change.created_at).toLocaleString()}</span>
                    </li>
                  ))}
                </ul>
              ) : (
                <p style={{ color: "#6b7280" }}>No changes were made this session.</p>
              )}
            </div>
            <footer className="tz-dialog-actions">
              <button type="button" onClick={closeSessionChanges}>Close</button>
            </footer>
          </div>
        </div>
      ) : null}

      {verifyOpen ? (
        <article className="company-setting-field channels-verify-inline">
          <div>
            <strong>Verify it's you</strong>
            <span>
              This will change a real channel connection. We'll email a one-time code to confirm it's you first.
            </span>
            {verifyStage === "send" ? (
              <div style={{ marginTop: 10 }}>
                <button type="button" onClick={handleSendCode} disabled={busy}>
                  {busy ? "Sending…" : "Send verification code"}
                </button>
                <button type="button" onClick={() => setVerifyOpen(false)} disabled={busy} style={{ marginLeft: 8 }}>
                  Cancel
                </button>
              </div>
            ) : (
              <form onSubmit={handleVerifyCode} style={{ display: "flex", gap: 10, alignItems: "center", marginTop: 10 }}>
                <span>Code sent to {emailHint}. Enter it below:</span>
                <input
                  type="text" value={code} onChange={(e) => setCode(e.target.value)}
                  placeholder="123456" maxLength={6} required
                  style={{ padding: "8px 10px", borderRadius: 8, border: "1px solid #d5dae5", width: 100 }}
                />
                <button type="submit" disabled={busy}>{busy ? "Verifying…" : "Verify"}</button>
              </form>
            )}
          </div>
        </article>
      ) : null}

      <ChannelsOverview channels={channels} usage={usage} onConnect={handleConnectClick} />

      <article className="company-setting-field" style={{ marginTop: 24 }} ref={(el) => (sectionRefs.current.facebook = el)}>
        <div>
          <strong>Connect with Facebook</strong>
          <span>One click — automatically finds your Page(s) and connects Messenger and Instagram together, no manual token copying.</span>
        </div>
      </article>
      <button
        type="button"
        style={{ marginBottom: 24, padding: "10px 18px", background: "#1877f2", color: "#fff", border: "none", borderRadius: 8, fontWeight: 600, cursor: "pointer" }}
        disabled={busy}
        onClick={handleFacebookConnect}
      >
        Connect with Facebook
      </button>

      <article className="company-setting-field">
        <div><strong>Connect Telegram</strong><span>Paste your bot token from @BotFather.</span></div>
      </article>
      <form
        style={formStyle}
        ref={(el) => (sectionRefs.current.telegram = el)}
        onSubmit={(e) => {
          e.preventDefault();
          withVerification(async (token) => {
            await connectTelegramRequest(telegramToken, null, token);
            setTelegramToken("");
          });
        }}
      >
        <input style={inputStyle} value={telegramToken} onChange={(e) => setTelegramToken(e.target.value)} placeholder="Bot token" required />
        <button type="submit" disabled={busy}>Connect</button>
      </form>

      <article className="company-setting-field">
        <div><strong>Connect WhatsApp</strong><span>Phone Number ID and access token from Meta Business.</span></div>
      </article>
      <form
        style={formStyle}
        ref={(el) => (sectionRefs.current.whatsapp = el)}
        onSubmit={(e) => {
          e.preventDefault();
          withVerification(async (token) => {
            await connectWhatsAppRequest(waPhoneId, waToken, null, token);
            setWaPhoneId(""); setWaToken("");
          });
        }}
      >
        <input style={inputStyle} value={waPhoneId} onChange={(e) => setWaPhoneId(e.target.value)} placeholder="Phone Number ID" required />
        <input style={inputStyle} value={waToken} onChange={(e) => setWaToken(e.target.value)} placeholder="Access token" required />
        <button type="submit" disabled={busy}>Connect</button>
      </form>

      <article className="company-setting-field">
        <div><strong>Connect Instagram</strong><span>Facebook Page ID (with a linked Instagram professional account) and access token.</span></div>
      </article>
      <form
        style={formStyle}
        onSubmit={(e) => {
          e.preventDefault();
          withVerification(async (token) => {
            await connectInstagramRequest(igPageId, igToken, null, token);
            setIgPageId(""); setIgToken("");
          });
        }}
      >
        <input style={inputStyle} value={igPageId} onChange={(e) => setIgPageId(e.target.value)} placeholder="Facebook Page ID" required />
        <input style={inputStyle} value={igToken} onChange={(e) => setIgToken(e.target.value)} placeholder="Access token" required />
        <button type="submit" disabled={busy}>Connect</button>
      </form>

      <div className="users-table-wrap">
        <table className="users-table">
          <thead><tr><th>Channel</th><th>Name</th><th>Status</th><th></th></tr></thead>
          <tbody>
            {channels.map((c) => (
              <tr key={c.id}>
                <td>{c.channel}</td><td>{c.name}</td><td>{c.status}</td>
                <td>
                  {c.status === "active" ? (
                    <button
                      type="button"
                      onClick={() => withVerification((token) => disconnectChannelRequest(c.id, token))}
                      disabled={busy}
                    >
                      Disconnect
                    </button>
                  ) : null}
                </td>
              </tr>
            ))}
            {!channels.length ? <tr><td colSpan={4}>No channels connected yet.</td></tr> : null}
          </tbody>
        </table>
      </div>
    </div>
  );
}
