import { useCallback, useEffect, useState } from "react";
import { useNavigate, useParams } from "react-router-dom";
import {
  ArrowBackOutlined,
  CloseOutlined,
  DoneAllOutlined,
  ErrorOutlined,
  GroupOutlined,
  ScheduleOutlined,
  SendOutlined,
  VisibilityOutlined,
} from "@mui/icons-material";

import { getBroadcastReportRequest, listCustomerSegmentsRequest, sendBroadcastRequest } from "../../api/client";
import { AppButton, AppCard, AppTable, ErrorState, LoadingState, StatusBadge } from "../../components/common";
import "./BroadcastPage.css";
import "./BroadcastDetailPage.css";
import "../analytics/AnalyticsPage.css";
import "../customers/CustomersPage.css";
import "../customers/CustomerDetailPage.css";

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

function statusTone(status) {
  if (status === "draft") return "warning";
  if (status === "sending") return "info";
  if (status === "sent") return "success";
  return "neutral";
}

function sendStatusTone(status) {
  if (status === "sent") return "success";
  if (status === "failed") return "danger";
  if (status === "pending") return "warning";
  return "neutral";
}

function deliveryStatusTone(status) {
  if (status === "delivered") return "success";
  if (status === "read") return "info";
  if (status === "failed") return "danger";
  return "neutral";
}

function ReportStatCard({ title, value, description, icon: Icon }) {
  return (
    <AppCard padding="medium" hoverable className="analytics-stat-card">
      <div className="analytics-stat-icon">
        <Icon />
      </div>
      <div>
        <span>{title}</span>
        <strong>{value ?? 0}</strong>
        <small>{description}</small>
      </div>
    </AppCard>
  );
}

export default function BroadcastDetailPage() {
  const { broadcastId } = useParams();
  const navigate = useNavigate();

  const [report, setReport] = useState(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState("");

  const [segments, setSegments] = useState([]);

  const [sendOpen, setSendOpen] = useState(false);
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

  if (loading) return <LoadingState title="Loading broadcast report..." />;
  if (error && !report) {
    return <ErrorState title="Could not load this broadcast" description={error} action={<AppButton variant="primary" onClick={load}>Retry</AppButton>} />;
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

  const columns = [
    {
      key: "customer_name",
      label: "Contact",
      render: (value, row) => value || `Contact #${row.customer_id}`,
    },
    {
      key: "send_status",
      label: "Send status",
      render: (value) => <StatusBadge status={value} tone={sendStatusTone(value)} label={humanize(value)} />,
    },
    {
      key: "delivery_status",
      label: "Delivery status",
      render: (value) => (value ? <StatusBadge status={value} tone={deliveryStatusTone(value)} label={humanize(value)} /> : "—"),
    },
    {
      key: "error",
      label: "Error",
      render: (value) => value || "—",
    },
  ];

  return (
    <section className="customers-page client-file-page broadcast-detail-page">
      <button type="button" className="client-file-back" onClick={() => navigate("/broadcast")}>
        <ArrowBackOutlined fontSize="small" /> Back to Broadcast
      </button>

      <div className="client-file-header broadcast-detail-header">
        <div>
          <h2>{broadcast.name}</h2>
          <div className="broadcast-detail-meta">
            <StatusBadge status={broadcast.status} tone={statusTone(broadcast.status)} label={humanize(broadcast.status)} />
            <span>{channelLabel(broadcast.channel)}</span>
            <span>{targetingSummary}</span>
            {broadcast.sent_at ? <span>Sent {formatDateTime(broadcast.sent_at)}</span> : null}
          </div>
        </div>
        {isDraft ? (
          <div className="client-file-header-controls">
            <AppButton variant="primary" icon={<SendOutlined fontSize="small" />} onClick={() => { setSendError(""); setSendOpen(true); }}>
              Send now
            </AppButton>
          </div>
        ) : null}
      </div>

      {error ? <p className="customer-segment-error">{error}</p> : null}

      <AppCard padding="medium" className="broadcast-detail-message-card">
        <h3 className="client-file-section-title">Message</h3>
        <p className="broadcast-detail-message-text">{broadcast.message_text}</p>
      </AppCard>

      {isDraft ? (
        <AppCard padding="medium" className="broadcast-detail-notsent-card">
          <strong>Not sent yet</strong>
          <p>
            This broadcast will reach {totals.recipients ?? broadcast.recipient_count ?? 0} contact
            {(totals.recipients ?? broadcast.recipient_count) === 1 ? "" : "s"} on {channelLabel(broadcast.channel)} once sent.
          </p>
          <AppButton variant="primary" icon={<SendOutlined fontSize="small" />} onClick={() => { setSendError(""); setSendOpen(true); }}>
            Send now
          </AppButton>
        </AppCard>
      ) : null}

      <section className="analytics-stats-grid broadcast-detail-stats-grid">
        <ReportStatCard title="Recipients" value={totals.recipients} description="Total targeted contacts" icon={GroupOutlined} />
        <ReportStatCard title="Sent" value={totals.sent} description="Delivery attempted" icon={SendOutlined} />
        <ReportStatCard title="Failed" value={totals.failed} description="Could not be sent" icon={ErrorOutlined} />
        <ReportStatCard title="Delivered" value={totals.delivered} description="Confirmed delivered" icon={DoneAllOutlined} />
        <ReportStatCard title="Read" value={totals.read} description="Opened by recipient" icon={VisibilityOutlined} />
        <ReportStatCard title="Pending" value={totals.pending} description="Not yet dispatched" icon={ScheduleOutlined} />
      </section>

      {!trackingSupported ? (
        <p className="broadcast-detail-tracking-note">
          Delivery/read tracking isn't available on this channel.
        </p>
      ) : null}

      <section className="broadcast-history-section">
        <h3 className="broadcast-section-title">Recipients</h3>
        <AppTable
          columns={columns}
          rows={recipients}
          loading={false}
          emptyTitle={isDraft ? "Not sent yet" : "No recipients"}
          emptyDescription={isDraft
            ? "Recipients and delivery status will appear here once you send this broadcast."
            : "No recipient records were found for this broadcast."}
          page={1}
          pageSize={Math.max(recipients.length, 1)}
          totalRows={recipients.length}
          onPageChange={() => {}}
        />
      </section>

      {sendOpen ? (
        <div
          className="tz-dialog-backdrop"
          role="presentation"
          onMouseDown={(event) => { if (event.target === event.currentTarget && !sending) setSendOpen(false); }}
        >
          <section className="tz-dialog" role="dialog" aria-modal="true" aria-labelledby="broadcast-detail-send-dialog-title">
            <header className="tz-dialog-header">
              <h3 id="broadcast-detail-send-dialog-title">Send broadcast</h3>
              <button type="button" className="tz-dialog-close" onClick={() => !sending && setSendOpen(false)}>
                <CloseOutlined fontSize="small" />
              </button>
            </header>
            <div className="tz-dialog-body">
              <p>
                Send “{broadcast.name}” to {broadcast.recipient_count} contact{broadcast.recipient_count === 1 ? "" : "s"} on{" "}
                {channelLabel(broadcast.channel)}? This cannot be undone.
              </p>
              {sendError ? <p className="broadcast-error-text">{sendError}</p> : null}
            </div>
            <footer className="tz-dialog-actions">
              <AppButton variant="secondary" disabled={sending} onClick={() => setSendOpen(false)}>Cancel</AppButton>
              <AppButton variant="primary" loading={sending} onClick={confirmSend}>Send now</AppButton>
            </footer>
          </section>
        </div>
      ) : null}
    </section>
  );
}
