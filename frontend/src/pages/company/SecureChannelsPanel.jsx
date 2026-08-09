import { useEffect, useRef, useState } from "react";
import {
  sendVerificationCodeRequest,
  verifyCodeRequest,
  listMyChannelsRequest,
  connectTelegramRequest,
  connectWhatsAppRequest,
  disconnectChannelRequest,
  startWhatsAppQrRequest,
  whatsAppQrStatusRequest,
  connectInstagramDirectRequest,
  connectFacebookDirectRequest,
  startFacebookOAuthRequest,
  getMySubscriptionRequest,
  getSessionChangesRequest,
} from "../../api/client";
import { CHANNEL_CATEGORIES, CHANNEL_LABELS } from "./channelCatalog";
import { resolveChannelIcon } from "./channelIcons";
import "./SecureChannelsPanel.css";

const PURPOSE = "channels_access";

function ChannelsOverview({ channels, usage, onConnect }) {
  // Retired QR sessions (superseded by a re-pair) are already revoked and
  // shouldn't show in the connected directory/list or inflate counts.
  const visibleChannels = channels.filter((c) => !(c.channel === "whatsapp_qr" && c.status !== "active"));
  const connectedByKey = visibleChannels.reduce((map, ch) => {
    (map[ch.channel] ||= []).push(ch);
    return map;
  }, {});

  const used = usage?.used ?? visibleChannels.filter((c) => c.status === "active").length;
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

      {visibleChannels.length ? (
        <div className="channels-connected-list">
          {visibleChannels.map((c) => (
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

  // WhatsApp QR pairing: session key set on start, then polled until the
  // phone scans the code and the bridge reports "connected".
  const [waqrSession, setWaqrSession] = useState(null);
  const [waqrState, setWaqrState] = useState(null);

  const [igUsername, setIgUsername] = useState("");
  const [igPassword, setIgPassword] = useState("");
  const [igCode, setIgCode] = useState("");
  const [fbPage, setFbPage] = useState("");
  const [fbCUser, setFbCUser] = useState("");
  const [fbXs, setFbXs] = useState("");

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
  // Success hint after a direct social login (comments don't auto-import).
  const [connectHint, setConnectHint] = useState("");

  const sectionRefs = useRef({});

  // Both WhatsApp transports connected → replies go out on the Cloud API
  // number, not the QR-linked one. Warn the owner so a customer isn't
  // answered from a different number than they messaged.
  const hasCloudWhatsApp = channels.some((c) => c.channel === "whatsapp" && c.status === "active");
  const hasQrWhatsApp = channels.some((c) => c.channel === "whatsapp_qr" && c.status === "active");

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

  // Poll the pairing session until it connects (or the panel unmounts).
  useEffect(() => {
    if (!waqrSession) return undefined;
    let cancelled = false;
    const timer = setInterval(async () => {
      try {
        const status = await whatsAppQrStatusRequest(waqrSession, elevatedTokenRef.current);
        if (cancelled) return;
        setWaqrState(status);
        if (status.status === "connected") {
          clearInterval(timer);
          setWaqrSession(null);
          await loadChannels();
        } else if (status.status === "disconnected") {
          // Terminal: the QR expired unscanned (or the session ended).
          // Stop polling a session that will never connect; the user can
          // press "Start QR pairing" again for a fresh code.
          clearInterval(timer);
          setWaqrSession(null);
          setError("The QR code expired before it was scanned. Please start again.");
        }
      } catch (e) {
        if (cancelled) return;
        clearInterval(timer);
        setWaqrSession(null);
        setWaqrState(null);
        setError(e.message);
      }
    }, 2500);
    return () => { cancelled = true; clearInterval(timer); };
  }, [waqrSession]);

  function handleWhatsAppQrStart() {
    withVerification(async (token) => {
      let result;
      try {
        result = await startWhatsAppQrRequest(token);
      } catch (e) {
        const msg = String(e?.message || "");
        if (e?.status === 503 || e?.status === 502 || /bridge is not running|temporarily/i.test(msg)) {
          throw new Error("WhatsApp QR pairing is temporarily unavailable. Please try again shortly, or use one of the other WhatsApp options below.");
        }
        throw e;
      }
      setWaqrState({ status: "starting", qr: null });
      setWaqrSession(result.session_key);
    });
  }

  function goToConnectSection(connectKey) {
    if (connectKey === "facebook") {
      handleFacebookConnect();
      return;
    }
    if (connectKey === "whatsapp_qr") {
      const el = sectionRefs.current.whatsapp_qr;
      if (el) el.scrollIntoView({ behavior: "smooth", block: "center" });
      handleWhatsAppQrStart();
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

  return (
    <div className="company-setting-fields">
      {oauthMessage ? (
        <p className={`channels-oauth-message ${oauthMessage.type === "error" ? "is-error" : "is-success"}`}>
          {oauthMessage.text}
        </p>
      ) : null}
      {error ? <p className="channels-error-text">{error}</p> : null}
      {connectHint ? <p className="channels-oauth-message is-success">{connectHint}</p> : null}
      {hasCloudWhatsApp && hasQrWhatsApp ? (
        <p className="channels-oauth-message is-error">
          You have both WhatsApp (Cloud API) and WhatsApp (QR) connected. Replies are sent from your Cloud API
          number, so a customer who messaged your QR-linked number will be answered from a different number.
          Keep only one WhatsApp connection to avoid confusing customers.
        </p>
      ) : null}

      {hasVerifiedSession && !verifyOpen ? (
        <article className="company-setting-field channels-verify-inline">
          <div>
            <strong>Verified session active</strong>
            <span>Connect or disconnect as many channels as you need — no new code required until this session ends.</span>
          </div>
          <button type="button" className="btn btn-secondary" onClick={showSessionChanges} disabled={changesLoading}>
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
                <ul className="channels-changes-list">
                  {sessionChanges.map((change, index) => (
                    <li key={index}>
                      {change.description}
                      <br />
                      <span className="channels-change-time">{new Date(change.created_at).toLocaleString()}</span>
                    </li>
                  ))}
                </ul>
              ) : (
                <p className="channels-changes-empty">No changes were made this session.</p>
              )}
            </div>
            <footer className="tz-dialog-actions">
              <button type="button" className="btn btn-secondary" onClick={closeSessionChanges}>Close</button>
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
              <div className="channels-verify-actions">
                <button type="button" className="btn btn-primary" onClick={handleSendCode} disabled={busy}>
                  {busy ? "Sending…" : "Send verification code"}
                </button>
                <button type="button" className="btn btn-secondary" onClick={() => setVerifyOpen(false)} disabled={busy}>
                  Cancel
                </button>
              </div>
            ) : (
              <form onSubmit={handleVerifyCode} className="channels-verify-code-form">
                <span>Code sent to {emailHint}. Enter it below:</span>
                <input
                  type="text" value={code} onChange={(e) => setCode(e.target.value)}
                  placeholder="123456" maxLength={6} required
                  className="input channels-code-input"
                />
                <button type="submit" className="btn btn-primary" disabled={busy}>{busy ? "Verifying…" : "Verify"}</button>
              </form>
            )}
          </div>
        </article>
      ) : null}

      <ChannelsOverview channels={channels} usage={usage} onConnect={handleConnectClick} />

      <article className="company-setting-field channels-facebook-block" ref={(el) => (sectionRefs.current.facebook = el)}>
        <div>
          <strong>Connect with Facebook</strong>
          <span>One click — automatically finds your Page(s) and connects Messenger and Instagram together, no manual token copying.</span>
        </div>
      </article>
      <button
        type="button"
        className="btn channels-facebook-connect"
        disabled={busy}
        onClick={handleFacebookConnect}
      >
        Connect with Facebook
      </button>

      <article className="company-setting-field">
        <div><strong>Connect Telegram</strong><span>Paste your bot token from @BotFather.</span></div>
      </article>
      <form
        className="channels-connect-form"
        ref={(el) => (sectionRefs.current.telegram = el)}
        onSubmit={(e) => {
          e.preventDefault();
          withVerification(async (token) => {
            await connectTelegramRequest(telegramToken, null, token);
            setTelegramToken("");
          });
        }}
      >
        <input className="input" value={telegramToken} onChange={(e) => setTelegramToken(e.target.value)} placeholder="Bot token" required />
        <button type="submit" className="btn btn-primary" disabled={busy}>Connect</button>
      </form>

      <article className="company-setting-field" ref={(el) => (sectionRefs.current.whatsapp_qr = el)}>
        <div>
          <strong>Connect WhatsApp by QR scan</strong>
          <span>
            No Meta developer account needed — open WhatsApp on your phone, go to Settings → Linked Devices →
            Link a Device, and scan the code below.
          </span>
          <div className="channels-verify-actions">
            <button type="button" className="btn btn-primary" onClick={handleWhatsAppQrStart} disabled={busy || Boolean(waqrSession)}>
              {waqrSession ? "Waiting for scan…" : "Start QR pairing"}
            </button>
            {waqrSession ? (
              <button
                type="button"
                className="btn btn-secondary"
                onClick={() => { setWaqrSession(null); setWaqrState(null); }}
              >
                Cancel
              </button>
            ) : null}
          </div>
          {waqrState?.qr ? (
            <img src={waqrState.qr} alt="WhatsApp pairing QR code" width={240} height={240} style={{ marginTop: "var(--space-3)" }} />
          ) : null}
          {waqrState && !waqrState.qr && waqrState.status !== "connected" ? (
            <span>Preparing the QR code…</span>
          ) : null}
          {waqrState?.status === "connected" ? (
            <span>Connected{waqrState.phone ? ` as +${waqrState.phone}` : ""}. This number now receives and sends through T-ZONE.</span>
          ) : null}
        </div>
      </article>

      <article className="company-setting-field">
        <div><strong>Connect WhatsApp</strong><span>Phone Number ID and access token from Meta Business.</span></div>
      </article>
      <form
        className="channels-connect-form"
        ref={(el) => (sectionRefs.current.whatsapp = el)}
        onSubmit={(e) => {
          e.preventDefault();
          withVerification(async (token) => {
            await connectWhatsAppRequest(waPhoneId, waToken, null, token);
            setWaPhoneId(""); setWaToken("");
          });
        }}
      >
        <input className="input" value={waPhoneId} onChange={(e) => setWaPhoneId(e.target.value)} placeholder="Phone Number ID" required />
        <input className="input" value={waToken} onChange={(e) => setWaToken(e.target.value)} placeholder="Access token" required />
        <button type="submit" className="btn btn-primary" disabled={busy}>Connect</button>
      </form>

      <article className="company-setting-field">
        <div>
          <strong>Connect Instagram by direct login</strong>
          <span>
            Your Instagram username and password — used once to log in, never stored. Brings your posts and
            comments into the Comments page and lets the team reply. If your account has two-factor
            authentication, add the current 6-digit code.
          </span>
        </div>
      </article>
      <form
        className="channels-connect-form"
        ref={(el) => (sectionRefs.current.instagram_direct = el)}
        onSubmit={(e) => {
          e.preventDefault();
          withVerification(async (token) => {
            await connectInstagramDirectRequest(igUsername, igPassword, igCode || null, token);
            setIgUsername(""); setIgPassword(""); setIgCode("");
            setConnectHint("Instagram connected. Go to Comments → “Sync now” to pull in your posts and comments.");
          });
        }}
      >
        <input className="input" value={igUsername} onChange={(e) => setIgUsername(e.target.value)} placeholder="Instagram username" required />
        <input className="input" type="password" value={igPassword} onChange={(e) => setIgPassword(e.target.value)} placeholder="Password" required />
        <input className="input" value={igCode} onChange={(e) => setIgCode(e.target.value)} placeholder="2FA code (required if 2FA is on)" maxLength={8} />
        <button type="submit" className="btn btn-primary" disabled={busy}>Connect</button>
      </form>

      <article className="company-setting-field">
        <div>
          <strong>Connect Facebook by cookie download</strong>
          <span>
            Download your Page's posts and comments without a Meta developer account (read-only). While logged
            in to Facebook in your browser, copy the "c_user" and "xs" cookies (DevTools → Application →
            Cookies) and paste them with your Page name.
          </span>
        </div>
      </article>
      <form
        className="channels-connect-form"
        ref={(el) => (sectionRefs.current.facebook_direct = el)}
        onSubmit={(e) => {
          e.preventDefault();
          withVerification(async (token) => {
            await connectFacebookDirectRequest(fbPage, fbCUser, fbXs, token);
            setFbPage(""); setFbCUser(""); setFbXs("");
            setConnectHint("Facebook connected (read-only). Go to Comments → “Sync now” to download your posts and comments.");
          });
        }}
      >
        <input className="input" value={fbPage} onChange={(e) => setFbPage(e.target.value)} placeholder="Page name (from its URL)" required />
        <input className="input" value={fbCUser} onChange={(e) => setFbCUser(e.target.value)} placeholder="c_user cookie" required />
        <input className="input" value={fbXs} onChange={(e) => setFbXs(e.target.value)} placeholder="xs cookie" required />
        <button type="submit" className="btn btn-primary" disabled={busy}>Connect</button>
      </form>

      <div className="users-table-wrap">
        <table className="users-table">
          <thead><tr><th>Channel</th><th>Name</th><th>Status</th><th></th></tr></thead>
          <tbody>
            {channels
              // Retired QR sessions (superseded by a re-pair) are already
              // revoked on the phone and have no action — don't clutter.
              .filter((c) => !(c.channel === "whatsapp_qr" && c.status !== "active"))
              .map((c) => (
              <tr key={c.id}>
                <td>{c.channel}</td><td>{c.name}</td><td>{c.status}</td>
                <td>
                  {c.status === "active" ? (
                    <button
                      type="button"
                      className="btn btn-secondary"
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
