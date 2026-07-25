import { useEffect, useState } from "react";
import {
  listMyChannelsRequest,
  connectTelegramRequest,
  connectWhatsAppRequest,
  connectInstagramRequest,
  disconnectChannelRequest,
} from "../../api/client";

export default function ChannelsPage() {
  const [channels, setChannels] = useState([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState("");
  const [connecting, setConnecting] = useState(false);

  const [telegramToken, setTelegramToken] = useState("");
  const [waPhoneId, setWaPhoneId] = useState("");
  const [waToken, setWaToken] = useState("");
  const [igPageId, setIgPageId] = useState("");
  const [igToken, setIgToken] = useState("");

  async function load() {
    setLoading(true);
    try {
      const result = await listMyChannelsRequest();
      setChannels(result.channels || []);
      setError("");
    } catch (e) {
      setError(e.message || "Unable to load channels.");
    } finally {
      setLoading(false);
    }
  }

  useEffect(() => { load(); }, []);

  async function handleConnectTelegram(e) {
    e.preventDefault();
    setConnecting(true);
    setError("");
    try {
      await connectTelegramRequest(telegramToken);
      setTelegramToken("");
      await load();
    } catch (x) {
      setError(x.message);
    } finally {
      setConnecting(false);
    }
  }

  async function handleConnectWhatsApp(e) {
    e.preventDefault();
    setConnecting(true);
    setError("");
    try {
      await connectWhatsAppRequest(waPhoneId, waToken);
      setWaPhoneId("");
      setWaToken("");
      await load();
    } catch (x) {
      setError(x.message);
    } finally {
      setConnecting(false);
    }
  }

  async function handleConnectInstagram(e) {
    e.preventDefault();
    setConnecting(true);
    setError("");
    try {
      await connectInstagramRequest(igPageId, igToken);
      setIgPageId("");
      setIgToken("");
      await load();
    } catch (x) {
      setError(x.message);
    } finally {
      setConnecting(false);
    }
  }

  async function handleDisconnect(accountId) {
    setError("");
    try {
      await disconnectChannelRequest(accountId);
      await load();
    } catch (x) {
      setError(x.message);
    }
  }

  if (loading) return <div className="admin-access-loading">Loading channels…</div>;

  const inputStyle = { flex: 1, padding: "10px 12px", borderRadius: 8, border: "1px solid #d5dae5" };
  const formStyle = { display: "flex", gap: 12, padding: "0 0 20px", flexWrap: "wrap" };

  return (
    <section className="admin-access-page">
      {error ? <div className="admin-access-error">{error}</div> : null}

      <div className="access-toolbar">
        <div>
          <strong>Connected channels</strong>
          <span>{channels.filter((c) => c.status === "active").length} active</span>
        </div>
      </div>

      <div className="admin-access-card users-card">
        <div className="users-card-header">
          <div>
            <h2>Connect Telegram</h2>
            <p>Paste your bot token from @BotFather — connects instantly, no waiting.</p>
          </div>
        </div>
        <form onSubmit={handleConnectTelegram} style={formStyle}>
          <input
            type="text" placeholder="123456789:AAExampleTokenFromBotFather"
            value={telegramToken} onChange={(e) => setTelegramToken(e.target.value)}
            required style={inputStyle}
          />
          <button type="submit" className="primary-action" disabled={connecting}>
            {connecting ? "Connecting…" : "Connect"}
          </button>
        </form>
      </div>

      <div className="admin-access-card users-card">
        <div className="users-card-header">
          <div>
            <h2>Connect WhatsApp</h2>
            <p>From your Meta Business dashboard: Phone Number ID and a permanent access token.</p>
          </div>
        </div>
        <form onSubmit={handleConnectWhatsApp} style={formStyle}>
          <input
            type="text" placeholder="Phone Number ID"
            value={waPhoneId} onChange={(e) => setWaPhoneId(e.target.value)}
            required style={inputStyle}
          />
          <input
            type="text" placeholder="Access token"
            value={waToken} onChange={(e) => setWaToken(e.target.value)}
            required style={inputStyle}
          />
          <button type="submit" className="primary-action" disabled={connecting}>
            {connecting ? "Connecting…" : "Connect"}
          </button>
        </form>
      </div>

      <div className="admin-access-card users-card">
        <div className="users-card-header">
          <div>
            <h2>Connect Instagram</h2>
            <p>Your Facebook Page ID (must have an Instagram professional account linked to it) and an access token.</p>
          </div>
        </div>
        <form onSubmit={handleConnectInstagram} style={formStyle}>
          <input
            type="text" placeholder="Facebook Page ID"
            value={igPageId} onChange={(e) => setIgPageId(e.target.value)}
            required style={inputStyle}
          />
          <input
            type="text" placeholder="Access token"
            value={igToken} onChange={(e) => setIgToken(e.target.value)}
            required style={inputStyle}
          />
          <button type="submit" className="primary-action" disabled={connecting}>
            {connecting ? "Connecting…" : "Connect"}
          </button>
        </form>
      </div>

      <div className="admin-access-card users-card">
        <div className="users-card-header">
          <div>
            <h2>Your channels</h2>
            <p>Every channel connected to your company.</p>
          </div>
        </div>
        <div className="users-table-wrap">
          <table className="users-table">
            <thead>
              <tr><th>Channel</th><th>Name</th><th>Status</th><th></th></tr>
            </thead>
            <tbody>
              {channels.map((channel) => (
                <tr key={channel.id}>
                  <td><strong>{channel.channel}</strong></td>
                  <td>{channel.name}</td>
                  <td>{channel.status}</td>
                  <td>
                    {channel.status === "active" ? (
                      <button type="button" className="secondary-action" onClick={() => handleDisconnect(channel.id)}>
                        Disconnect
                      </button>
                    ) : null}
                  </td>
                </tr>
              ))}
              {!channels.length ? (
                <tr><td colSpan={4}>No channels connected yet.</td></tr>
              ) : null}
            </tbody>
          </table>
        </div>
      </div>
    </section>
  );
}
