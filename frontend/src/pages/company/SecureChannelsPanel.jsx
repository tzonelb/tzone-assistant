import { useEffect, useState } from "react";
import {
  sendVerificationCodeRequest,
  verifyCodeRequest,
  getSessionChangesRequest,
  listMyChannelsRequest,
  connectTelegramRequest,
  connectWhatsAppRequest,
  connectInstagramRequest,
  disconnectChannelRequest,
  startFacebookOAuthRequest,
} from "../../api/client";

const PURPOSE = "channels_access";

export default function SecureChannelsPanel() {
  const [stage, setStage] = useState("locked"); // locked -> code_sent -> unlocked -> summary
  const [emailHint, setEmailHint] = useState("");
  const [code, setCode] = useState("");
  const [elevatedToken, setElevatedToken] = useState(null);
  const [error, setError] = useState("");
  const [busy, setBusy] = useState(false);
  const [summary, setSummary] = useState([]);

  const [channels, setChannels] = useState([]);
  const [oauthMessage, setOauthMessage] = useState(null);
  const [telegramToken, setTelegramToken] = useState("");
  const [waPhoneId, setWaPhoneId] = useState("");
  const [waToken, setWaToken] = useState("");
  const [igPageId, setIgPageId] = useState("");
  const [igToken, setIgToken] = useState("");

  async function loadChannels() {
    try {
      const result = await listMyChannelsRequest();
      setChannels(result.channels || []);
    } catch (e) {
      setError(e.message);
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

  async function handleSendCode() {
    setBusy(true);
    setError("");
    try {
      const result = await sendVerificationCodeRequest(PURPOSE);
      setEmailHint(result.email_hint);
      setStage("code_sent");
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
      setElevatedToken(result.elevated_token);
      setCode("");
      setStage("unlocked");
    } catch (e) {
      setError(e.message);
    } finally {
      setBusy(false);
    }
  }

  async function handleFinish() {
    setBusy(true);
    try {
      const result = await getSessionChangesRequest(PURPOSE, elevatedToken);
      setSummary(result.changes || []);
      setStage("summary");
    } catch (e) {
      setError(e.message);
    } finally {
      setBusy(false);
    }
  }

  function closeSummaryAndRelock() {
    setStage("locked");
    setElevatedToken(null);
    setSummary([]);
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

  if (stage === "locked" || stage === "code_sent") {
    return (
      <div className="company-setting-fields">
        {oauthMessage ? (
          <p style={{ color: oauthMessage.type === "success" ? "#1e7e34" : "#c0392b", fontWeight: 600 }}>
            {oauthMessage.text}
          </p>
        ) : null}
        <article className="company-setting-field">
          <div>
            <strong>Verification required</strong>
            <span>
              Connecting or disconnecting a channel handles real account credentials.
              We'll email a one-time code to your account's email before you can make changes.
            </span>
          </div>
        </article>

        {error ? <p style={{ color: "#c0392b" }}>{error}</p> : null}

        {stage === "locked" ? (
          <button type="button" onClick={handleSendCode} disabled={busy}>
            {busy ? "Sending…" : "Send verification code"}
          </button>
        ) : (
          <form onSubmit={handleVerifyCode} style={{ display: "flex", gap: 10, alignItems: "center" }}>
            <span>Code sent to {emailHint}. Enter it below:</span>
            <input
              type="text" value={code} onChange={(e) => setCode(e.target.value)}
              placeholder="123456" maxLength={6} required
              style={{ padding: "8px 10px", borderRadius: 8, border: "1px solid #d5dae5", width: 100 }}
            />
            <button type="submit" disabled={busy}>{busy ? "Verifying…" : "Verify"}</button>
          </form>
        )}

        <div className="users-table-wrap" style={{ marginTop: 16 }}>
          <table className="users-table">
            <thead><tr><th>Channel</th><th>Name</th><th>Status</th></tr></thead>
            <tbody>
              {channels.map((c) => (
                <tr key={c.id}><td>{c.channel}</td><td>{c.name}</td><td>{c.status}</td></tr>
              ))}
              {!channels.length ? <tr><td colSpan={3}>No channels connected yet.</td></tr> : null}
            </tbody>
          </table>
        </div>
      </div>
    );
  }

  if (stage === "summary") {
    return (
      <div className="company-setting-fields">
        <article className="company-setting-field">
          <div>
            <strong>Session summary</strong>
            <span>Here's what changed during this verified session.</span>
          </div>
        </article>
        {summary.length ? (
          <ul>
            {summary.map((item, i) => (
              <li key={i}>{item.description} — {item.created_at}</li>
            ))}
          </ul>
        ) : (
          <p>No changes were made.</p>
        )}
        <button type="button" onClick={closeSummaryAndRelock}>Done</button>
      </div>
    );
  }

  // stage === "unlocked"
  const inputStyle = { flex: 1, padding: "10px 12px", borderRadius: 8, border: "1px solid #d5dae5" };
  const formStyle = { display: "flex", gap: 12, padding: "0 0 20px", flexWrap: "wrap" };

  return (
    <div className="company-setting-fields">
      {error ? <p style={{ color: "#c0392b" }}>{error}</p> : null}

      <article className="company-setting-field">
        <div>
          <strong>Connect with Facebook</strong>
          <span>One click — automatically finds your Page(s) and connects Messenger and Instagram together, no manual token copying.</span>
        </div>
      </article>
      <button
        type="button"
        style={{ marginBottom: 24, padding: "10px 18px", background: "#1877f2", color: "#fff", border: "none", borderRadius: 8, fontWeight: 600, cursor: "pointer" }}
        disabled={busy}
        onClick={async () => {
          setBusy(true);
          setError("");
          try {
            const result = await startFacebookOAuthRequest();
            window.location.href = result.authorize_url;
          } catch (e) {
            setError(e.message);
            setBusy(false);
          }
        }}
      >
        Connect with Facebook
      </button>

      <article className="company-setting-field">
        <div><strong>Connect Telegram</strong><span>Paste your bot token from @BotFather.</span></div>
      </article>
      <form
        style={formStyle}
        onSubmit={(e) => {
          e.preventDefault();
          withErrorHandling(async () => {
            await connectTelegramRequest(telegramToken, null, elevatedToken);
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
        onSubmit={(e) => {
          e.preventDefault();
          withErrorHandling(async () => {
            await connectWhatsAppRequest(waPhoneId, waToken, null, elevatedToken);
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
          withErrorHandling(async () => {
            await connectInstagramRequest(igPageId, igToken, null, elevatedToken);
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
                      onClick={() => withErrorHandling(() => disconnectChannelRequest(c.id, elevatedToken))}
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

      <button type="button" onClick={handleFinish} disabled={busy} style={{ marginTop: 16 }}>
        {busy ? "…" : "Done — show what changed"}
      </button>
    </div>
  );
}
