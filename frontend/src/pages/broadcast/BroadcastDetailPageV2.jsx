import { useCallback, useEffect, useState } from "react";
import { useNavigate, useParams } from "react-router-dom";
import { ArrowBackOutlined, SendOutlined } from "@mui/icons-material";

import {
  getBroadcastReportRequest,
  listCustomerSegmentsRequest,
  previewBroadcastRecipientCountRequest,
  sendBroadcastRequest,
} from "../../api/client";
import { EmptyState, ErrorState, LoadingState } from "../../components/common";
import "./BroadcastDetailPageV2.css";

// Same data + actions as BroadcastDetailPage.jsx (v1) — this is a visual
// rebuild only. There's no dedicated mockup for this screen, so it reuses
// the same class vocabulary and rhythm as BroadcastPageV2 (kicker + h1,
// tz-stat tiles, card sections, tag for status, table for recipients).
const CHANNEL_LABELS = {
  telegram: "Telegram",
  messenger: "Messenger",
  instagram: "Instagram",
  whatsapp: "WhatsApp",
};

function humanize(value) {
  return String(value || "").replaceAll("_", " ").replace(/\b\w/g, (letter) => letter.toUpperCase());
}

function channelLabel(value) {
  return CHANNEL_LABELS[value] || humanize(value);
}

function formatDateTime(value) {
  if (!value) return "—";
  const normalized = /(?:Z|[+-]\d{2}:?\d{2})$/i.test(value) ? value : `${value}Z`;
  const date = new Date(normalized);
  return Number.isNaN(date.getTime()) ? "—" : date.toLocaleString();
}

function statusTagClass(status) {
  if (status === "sent") return "tag tag-accent";
  if (status === "sending") return "tag tag-outline";
  return "tag tag-neutral";
}

function sendStatusTagClass(status) {
  if (status === "sent") return "tag tag-accent";
  if (status === "failed") return "tag tag-accent-2";
  return "tag tag-neutral";
}

function deliveryStatusTagClass(status) {
  if (status === "delivered") return "tag tag-accent";
  if (status === "read") return "tag tag-outline";
  if (status === "failed") return "tag tag-accent-2";
  return "tag tag-neutral";
}

export default function BroadcastDetailPageV2() {
  const { broadcastId } = useParams();
  const navigate = useNavigate();

  const [report, setReport] = useState(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState("");

  const [segments, setSegments] = useState([]);

  const [sendOpen, setSendOpen] = useState(false);
  const [, setSendCount] = useState(null);
  const [, setSendCountLoading] = useState(false);
  const [sending, setSending] = useState(false);
  const [sendError, setSendError] = useState("");

  const load = useCallback(async () => {
    setLoading(true);
    setError("");
    try {
      const result = await getBroadcastReportRequest(broadcastId);
      setReport(result);
    } catch (requestError) {
      setError(requestError.message || "Broadcast report could not be loaded.");
    } finally {
      setLoading(false);
    }
  }, [broadcastId]);

  useEffect(() => { load(); }, [load]);

  useEffect(() => {
    listCustomerSegmentsRequest()
      .then((result) => setSegments(Array.isArray(result?.items) ? result.items : []))
      .catch(() => {});
  }, []);

  // eslint-disable-next-line no-unused-vars -- wired when the send dialog lands
  async function openSendConfirm() {
    setSendError("");
    setSendOpen(true);
    // recipient_count is a creation-time snapshot; recount live so this
    // matches what the send will actually resolve.
    setSendCount(null);
    setSendCountLoading(true);
    try {
      const result = await previewBroadcastRecipientCountRequest(broadcastId);
      setSendCount(result.recipient_count);
    } catch {
      // Keep the stale snapshot if the live recount fails.
    } finally {
      setSendCountLoading(false);
    }
  }

  async function confirmSend() {
    setSending(true);
    setSendError("");
    try {
      await sendBroadcastRequest(broadcastId);
      setSendOpen(false);
      await load();
    } catch (requestError) {
      setSendError(requestError.message || "Could not send the broadcast.");
    } finally {
      setSending(false);
    }
  }

  if (loading && !report) {
    return (
      <div className="tz-screen tzv2-broadcast-detail-page">
        <LoadingState title="Loading broadcast report…" description="Retrieving delivery totals and recipients." />
      </div>
    );
  }
  if (error && !report) {
    return (
      <div className="tz-screen tzv2-broadcast-detail-page">
        <ErrorState title="Could not load this broadcast" description={error} action={<button type="button" className="btn btn-primary" onClick={load}>Retry</button>} />
      </div>
    );
  }
  if (!report) return null;

  const broadcast = report.broadcast || {};
  const totals = report.totals || {};
  const recipients = Array.isArray(report.recipients) ? report.recipients : [];
  const trackingSupported = report.channel_tracking_supported !== false;
  const isDraft = broadcast.status === "draft";

  let targetingSummary = "All contacts";
  if (broadcast.raw_numbers_json) {
    let numberCount = broadcast.recipient_count ?? 0;
    try {
      const parsed = JSON.parse(broadcast.raw_numbers_json);
      if (Array.isArray(parsed)) numberCount = parsed.length;
    } catch {
      // Fall back to recipient_count if the stored JSON is somehow malformed.
    }
    targetingSummary = `Targeted ${numberCount} phone number${numberCount === 1 ? "" : "s"} directly`;
  } else if (broadcast.segment_id) {
    const segment = segments.find((item) => item.id === broadcast.segment_id);
    targetingSummary = segment ? `Segment: ${segment.name}` : "Segment target";
  } else if (broadcast.lifecycle_stage) {
    targetingSummary = `Lifecycle stage: ${humanize(broadcast.lifecycle_stage)}`;
  } else if (broadcast.tag) {
    targetingSummary = `Tag: ${broadcast.tag}`;
  }

  return (
    <div className="tz-screen tzv2-broadcast-detail-page">
      <button type="button" className="btn btn-ghost tzv2-broadcast-detail-back" onClick={() => navigate("/broadcast")}>
        <ArrowBackOutlined fontSize="small" /> Back to Broadcast
      </button>

      <div className="tzv2-broadcast-detail-head">
        <div>
          <span className="tz-kick tzv2-broadcast-detail-kick">
            {channelLabel(broadcast.channel)} · {targetingSummary}
          </span>
          <h1>{broadcast.name}</h1>
          <div className="tzv2-broadcast-detail-meta">
            <span className={statusTagClass(broadcast.status)}>{humanize(broadcast.status)}</span>
            {broadcast.sent_at ? <span className="tzv2-broadcast-detail-meta-note">Sent {formatDateTime(broadcast.sent_at)}</span> : null}
          </div>
        </div>
        {isDraft ? (
          <button type="button" className="btn btn-primary" onClick={() => { setSendError(""); setSendOpen(true); }}>
            <SendOutlined fontSize="small" /> Send now
          </button>
        ) : null}
      </div>

      {error ? <p className="tzv2-broadcast-detail-error">{error}</p> : null}

      <section className="card tzv2-broadcast-detail-message-card">
        <span className="card-kicker">Message</span>
        <p className="tzv2-broadcast-detail-message-text">{broadcast.message_text}</p>
        {broadcast.media_url ? (
          <div className="tzv2-broadcast-detail-media">
            {broadcast.media_type === "image" ? (
              <img src={broadcast.media_url} alt="Broadcast attachment" />
            ) : broadcast.media_type === "video" ? (
              <video src={broadcast.media_url} controls />
            ) : (
              <audio src={broadcast.media_url} controls />
            )}
          </div>
        ) : null}
      </section>

      {isDraft ? (
        <section className="card tzv2-broadcast-detail-notsent-card">
          <span className="card-kicker">Not sent yet</span>
          <p>
            This broadcast will reach {totals.recipients ?? broadcast.recipient_count ?? 0} contact
            {(totals.recipients ?? broadcast.recipient_count) === 1 ? "" : "s"} on {channelLabel(broadcast.channel)} once sent.
          </p>
          <button type="button" className="btn btn-primary" onClick={() => { setSendError(""); setSendOpen(true); }}>
            <SendOutlined fontSize="small" /> Send now
          </button>
        </section>
      ) : null}

      <div className="tzv2-broadcast-detail-stats">
        <div className="tz-stat">
          <span className="tz-kick tzv2-broadcast-detail-stat-kick">Recipients</span>
          <div className="tz-fig tzv2-broadcast-detail-fig">{totals.recipients ?? 0}</div>
        </div>
        <div className="tz-stat">
          <span className="tz-kick tzv2-broadcast-detail-stat-kick">Sent</span>
          <div className="tz-fig tzv2-broadcast-detail-fig">{totals.sent ?? 0}</div>
        </div>
        <div className="tz-stat">
          <span className="tz-kick tzv2-broadcast-detail-stat-kick">Failed</span>
          <div className="tz-fig tzv2-broadcast-detail-fig">{totals.failed ?? 0}</div>
        </div>
        <div className="tz-stat">
          <span className="tz-kick tzv2-broadcast-detail-stat-kick">Delivered</span>
          <div className="tz-fig tzv2-broadcast-detail-fig">{totals.delivered ?? 0}</div>
        </div>
        <div className="tz-stat">
          <span className="tz-kick tzv2-broadcast-detail-stat-kick">Read</span>
          <div className="tz-fig tzv2-broadcast-detail-fig">{totals.read ?? 0}</div>
        </div>
        <div className="tz-stat">
          <span className="tz-kick tzv2-broadcast-detail-stat-kick">Pending</span>
          <div className="tz-fig tzv2-broadcast-detail-fig">{totals.pending ?? 0}</div>
        </div>
      </div>

      {!trackingSupported ? (
        <p className="tzv2-broadcast-detail-tracking-note">
          Delivery/read tracking isn't available on this channel.
        </p>
      ) : null}

      <section className="tzv2-broadcast-detail-recipients">
        <h3 className="tzv2-broadcast-detail-section-title">Recipients</h3>
        {recipients.length ? (
          <div className="tz-tablewrap tzv2-broadcast-detail-tablewrap">
            <table className="table">
              <thead>
                <tr>
                  <th>Contact</th>
                  <th>Send status</th>
                  <th>Delivery status</th>
                  <th>Error</th>
                </tr>
              </thead>
              <tbody>
                {recipients.map((recipient, index) => (
                  <tr key={`${recipient.customer_id ?? recipient.external_user_id ?? index}`}>
                    <td>{recipient.customer_name || `Contact #${recipient.customer_id}`}</td>
                    <td><span className={sendStatusTagClass(recipient.send_status)}>{humanize(recipient.send_status)}</span></td>
                    <td>{recipient.delivery_status ? <span className={deliveryStatusTagClass(recipient.delivery_status)}>{humanize(recipient.delivery_status)}</span> : "—"}</td>
                    <td className="tzv2-broadcast-detail-recipient-error">{recipient.error || "—"}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        ) : (
          <EmptyState
            title={isDraft ? "Not sent yet" : "No recipients"}
            description={isDraft
              ? "Recipients and delivery status will appear here once you send this broadcast."
              : "No recipient records were found for this broadcast."}
          />
        )}
      </section>

      {sendOpen ? (
        <div
          className="dialog-backdrop"
          role="presentation"
          onMouseDown={(event) => { if (event.target === event.currentTarget && !sending) setSendOpen(false); }}
        >
          <section className="dialog" role="dialog" aria-modal="true" aria-labelledby="tzv2-broadcast-detail-send-title">
            <span className="dialog-title" id="tzv2-broadcast-detail-send-title">Send broadcast</span>
            <div className="dialog-body">
              <p>
                Send "{broadcast.name}" to {broadcast.recipient_count} contact{broadcast.recipient_count === 1 ? "" : "s"} on{" "}
                {channelLabel(broadcast.channel)}? This cannot be undone.
              </p>
              {sendError ? <p className="tzv2-broadcast-detail-error">{sendError}</p> : null}
            </div>
            <div className="dialog-actions">
              <button type="button" className="btn btn-secondary" disabled={sending} onClick={() => setSendOpen(false)}>Cancel</button>
              <button type="button" className="btn btn-primary" disabled={sending} onClick={confirmSend}>{sending ? "Sending…" : "Send now"}</button>
            </div>
          </section>
        </div>
      ) : null}
    </div>
  );
}
