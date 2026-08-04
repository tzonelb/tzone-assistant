import { useEffect, useMemo, useRef, useState } from "react";
import {
  CampaignOutlined,
  RefreshOutlined,
  ReplayOutlined,
  SendOutlined,
} from "@mui/icons-material";

import {
  createBroadcastRequest,
  getBroadcastRequest,
  getBroadcastsRequest,
  getConversationOptionsRequest,
  sendBroadcastRequest,
} from "../../api/client";
import {
  AppButton,
  AppCard,
  AppTable,
  ConfirmDialog,
  ErrorState,
  PageHeader,
  StatusBadge,
} from "../../components/common";
import { formatPlatformDateTime } from "../../utils/dateTime";

const MESSAGE_LIMIT = 2000;

const CHANNEL_OPTIONS = [
  { value: "messenger", label: "Messenger", available: true },
  { value: "instagram", label: "Instagram", available: true },
  { value: "whatsapp", label: "WhatsApp", available: true },
  { value: "telegram", label: "Telegram (not available yet)", available: false },
];

const CHANNEL_LABELS = {
  messenger: "Messenger",
  instagram: "Instagram",
  whatsapp: "WhatsApp",
  telegram: "Telegram",
};

const ACTIVE_STATUSES = new Set(["draft", "paused", "sending"]);
const POLL_INTERVAL_MS = 3000;

function channelLabel(channel) {
  return CHANNEL_LABELS[channel] || channel;
}

function recipientSummary(broadcast) {
  const actual = broadcast?.actual_recipient_count;
  const estimated = broadcast?.estimated_recipient_count;

  if (
    actual !== null &&
    actual !== undefined &&
    estimated !== null &&
    estimated !== undefined &&
    actual !== estimated
  ) {
    return `${actual} (est. ${estimated})`;
  }

  return String(actual ?? estimated ?? 0);
}

export default function BroadcastPage() {
  const [departments, setDepartments] = useState([]);

  const [channel, setChannel] = useState("messenger");
  const [targetDepartment, setTargetDepartment] = useState("");
  const [messageText, setMessageText] = useState("");
  const [composeError, setComposeError] = useState("");
  const [reviewing, setReviewing] = useState(false);

  const [draftBroadcast, setDraftBroadcast] = useState(null);
  const [confirmOpen, setConfirmOpen] = useState(false);

  const [activeBroadcast, setActiveBroadcast] = useState(null);
  const [sendInFlightId, setSendInFlightId] = useState(null);
  const [sendNotice, setSendNotice] = useState("");
  const [sendError, setSendError] = useState("");

  const [broadcasts, setBroadcasts] = useState([]);
  const [historyLoading, setHistoryLoading] = useState(true);
  const [historyError, setHistoryError] = useState("");
  const [historyPage, setHistoryPage] = useState(1);

  const sendInFlightRef = useRef(null);
  const pollRef = useRef(null);

  function stopPolling() {
    if (pollRef.current) {
      clearInterval(pollRef.current);
      pollRef.current = null;
    }
  }

  function applyBroadcastUpdate(updated) {
    if (!updated) return;

    setActiveBroadcast((current) =>
      current && current.id === updated.id ? { ...current, ...updated } : current,
    );
    setDraftBroadcast((current) =>
      current && current.id === updated.id ? { ...current, ...updated } : current,
    );
    setBroadcasts((current) =>
      current.map((item) => (item.id === updated.id ? { ...item, ...updated } : item)),
    );
  }

  function startPolling(broadcastId) {
    stopPolling();

    pollRef.current = setInterval(async () => {
      try {
        const updated = await getBroadcastRequest(broadcastId);
        applyBroadcastUpdate(updated);

        if (!ACTIVE_STATUSES.has(updated.status) || updated.status === "paused" || updated.status === "draft") {
          stopPolling();
        }
      } catch {
        // Transient poll failure — keep trying on the next tick.
      }
    }, POLL_INTERVAL_MS);
  }

  async function loadDepartments() {
    try {
      const result = await getConversationOptionsRequest();
      setDepartments(result?.departments || []);
    } catch {
      // Department list is a convenience filter; broadcasting still works without it.
    }
  }

  async function loadHistory({ silent = false } = {}) {
    if (!silent) setHistoryLoading(true);
    setHistoryError("");

    try {
      const result = await getBroadcastsRequest();
      const list = Array.isArray(result) ? result : result?.broadcasts || [];
      setBroadcasts(list);
      if (!silent) setHistoryPage(1);

      const inProgress = list.find((item) => item.status === "sending");
      if (inProgress && !pollRef.current) {
        setActiveBroadcast((current) => current || inProgress);
        startPolling(inProgress.id);
      }
    } catch (error) {
      setHistoryError(error.message || "Broadcast history could not be loaded.");
    } finally {
      if (!silent) setHistoryLoading(false);
    }
  }

  useEffect(() => {
    loadDepartments();
    loadHistory();
    return stopPolling;
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  const charCount = messageText.length;
  const overLimit = charCount > MESSAGE_LIMIT;
  const trimmedMessage = messageText.trim();

  async function handleReview() {
    setComposeError("");

    if (!trimmedMessage) {
      setComposeError("Message text is required.");
      return;
    }

    if (trimmedMessage.length > MESSAGE_LIMIT) {
      setComposeError(`Message must be ${MESSAGE_LIMIT} characters or fewer.`);
      return;
    }

    setReviewing(true);

    try {
      const created = await createBroadcastRequest({
        channel,
        message_text: trimmedMessage,
        target_department: targetDepartment || null,
      });

      setDraftBroadcast(created);
      setBroadcasts((current) => [created, ...current]);
      setSendError("");
      setConfirmOpen(true);
    } catch (error) {
      setComposeError(error.message || "Broadcast could not be created.");
    } finally {
      setReviewing(false);
    }
  }

  function handleCancelDraft() {
    setConfirmOpen(false);
    setDraftBroadcast(null);
  }

  async function handleSend(broadcastId) {
    if (!broadcastId || sendInFlightRef.current) {
      return;
    }

    sendInFlightRef.current = broadcastId;
    setSendInFlightId(broadcastId);
    setSendError("");
    setSendNotice("");

    try {
      const updated = await sendBroadcastRequest(broadcastId);
      applyBroadcastUpdate(updated);
      setActiveBroadcast(updated);
      setConfirmOpen(false);

      if (updated.status === "sending") {
        startPolling(updated.id);
      } else {
        stopPolling();
      }

      loadHistory({ silent: true });
    } catch (error) {
      if (error.status === 409) {
        setSendNotice(error.message || "This broadcast is already sending — showing live progress.");

        try {
          const current = await getBroadcastRequest(broadcastId);
          applyBroadcastUpdate(current);
          setActiveBroadcast(current);
          setConfirmOpen(false);

          if (current.status === "sending") {
            startPolling(current.id);
          }
        } catch {
          // Keep the notice even if the follow-up refresh failed.
        }
      } else {
        setSendError(error.message || "Broadcast could not be sent.");
      }
    } finally {
      sendInFlightRef.current = null;
      setSendInFlightId(null);
    }
  }

  function trackBroadcast(broadcast) {
    setSendError("");
    setSendNotice("");
    setActiveBroadcast(broadcast);

    if (broadcast.status === "sending") {
      startPolling(broadcast.id);
    }
  }

  const estimateMismatch = useMemo(() => {
    if (!activeBroadcast) return false;
    const { estimated_recipient_count: estimated, actual_recipient_count: actual } = activeBroadcast;
    return (
      estimated !== null &&
      estimated !== undefined &&
      actual !== null &&
      actual !== undefined &&
      estimated !== actual
    );
  }, [activeBroadcast]);

  const progressPercent = useMemo(() => {
    if (!activeBroadcast) return 0;
    const total = Number(activeBroadcast.actual_recipient_count) || 0;
    if (total <= 0) return 0;
    const done = Number(activeBroadcast.sent_count || 0) + Number(activeBroadcast.failed_count || 0);
    return Math.max(0, Math.min(100, Math.round((done / total) * 100)));
  }, [activeBroadcast]);

  const historyColumns = [
    { key: "channel", label: "Channel", render: (value) => channelLabel(value) },
    {
      key: "target_department",
      label: "Department",
      render: (value) => value || "All departments",
    },
    {
      key: "message_text",
      label: "Message",
      render: (value) => <span className="broadcast-table-message">{value}</span>,
    },
    {
      key: "status",
      label: "Status",
      render: (value) => <StatusBadge status={value} />,
    },
    {
      key: "recipients",
      label: "Recipients",
      valueGetter: (row) => recipientSummary(row),
    },
    { key: "sent_count", label: "Sent" },
    { key: "failed_count", label: "Failed" },
    {
      key: "created_at",
      label: "Created",
      render: (value) => (value ? formatPlatformDateTime(value) : "—"),
    },
    {
      key: "actions",
      label: "",
      render: (_value, row) => {
        const isInFlight = sendInFlightId === row.id;

        if (row.status === "draft" || row.status === "paused") {
          return (
            <AppButton
              size="small"
              variant="secondary"
              icon={row.status === "paused" ? <ReplayOutlined fontSize="small" /> : <SendOutlined fontSize="small" />}
              loading={isInFlight}
              disabled={isInFlight}
              onClick={() => handleSend(row.id)}
            >
              {row.status === "paused" ? "Resume" : "Send"}
            </AppButton>
          );
        }

        if (row.status === "sending") {
          return (
            <AppButton size="small" variant="secondary" onClick={() => trackBroadcast(row)}>
              View progress
            </AppButton>
          );
        }

        return null;
      },
    },
  ];

  return (
    <section className="broadcast-page">
      <PageHeader
        eyebrow="BULK MESSAGING"
        title="Broadcast"
        description="Compose a message and send it to every matching customer conversation on Messenger, Instagram or WhatsApp."
        actions={
          <AppButton
            variant="secondary"
            icon={<RefreshOutlined fontSize="small" />}
            onClick={() => loadHistory()}
          >
            Refresh history
          </AppButton>
        }
      />

      <AppCard padding="medium" className="broadcast-compose-card">
        <h3 className="broadcast-section-title">
          <CampaignOutlined fontSize="small" /> Compose broadcast
        </h3>

        <div className="broadcast-form-grid">
          <label className="broadcast-field">
            <span>Channel</span>
            <select
              className="tz-select"
              value={channel}
              onChange={(event) => setChannel(event.target.value)}
            >
              {CHANNEL_OPTIONS.map((option) => (
                <option key={option.value} value={option.value} disabled={!option.available}>
                  {option.label}
                </option>
              ))}
            </select>
          </label>

          <label className="broadcast-field">
            <span>Target department (optional)</span>
            <select
              className="tz-select"
              value={targetDepartment}
              onChange={(event) => setTargetDepartment(event.target.value)}
            >
              <option value="">All departments</option>
              {departments.map((department) => (
                <option key={department} value={department}>
                  {department}
                </option>
              ))}
            </select>
          </label>
        </div>

        {channel === "telegram" ? (
          <p className="broadcast-inline-note">
            Telegram broadcast sending is not available yet — pick Messenger, Instagram or WhatsApp instead.
          </p>
        ) : null}

        <label className="broadcast-field broadcast-field-message">
          <span>Message</span>
          <textarea
            className="broadcast-textarea"
            rows={6}
            maxLength={MESSAGE_LIMIT}
            value={messageText}
            placeholder="Write the message every matching customer will receive..."
            onChange={(event) => setMessageText(event.target.value)}
          />
          <div className={`broadcast-char-count${overLimit ? " is-over" : ""}`}>
            {charCount}/{MESSAGE_LIMIT} characters
          </div>
        </label>

        {composeError ? <p className="broadcast-inline-error">{composeError}</p> : null}

        <div className="broadcast-form-actions">
          <AppButton
            variant="primary"
            icon={<CampaignOutlined fontSize="small" />}
            loading={reviewing}
            disabled={!trimmedMessage || overLimit || channel === "telegram"}
            onClick={handleReview}
          >
            Review broadcast
          </AppButton>
        </div>
      </AppCard>

      {activeBroadcast ? (
        <AppCard padding="medium" className="broadcast-active-card">
          <div className="broadcast-active-head">
            <div>
              <span className="broadcast-active-kicker">Broadcast #{activeBroadcast.id}</span>
              <h3>
                {channelLabel(activeBroadcast.channel)} · {activeBroadcast.target_department || "All departments"}
              </h3>
            </div>
            <StatusBadge status={activeBroadcast.status} />
          </div>

          {estimateMismatch ? (
            <p className="broadcast-inline-note">
              Estimated {activeBroadcast.estimated_recipient_count} recipients at review, {activeBroadcast.actual_recipient_count} actually being sent.
            </p>
          ) : null}

          <div className="broadcast-progress-stats">
            <div>
              <span>Recipients</span>
              <strong>{activeBroadcast.actual_recipient_count ?? activeBroadcast.estimated_recipient_count ?? 0}</strong>
            </div>
            <div>
              <span>Sent</span>
              <strong>{activeBroadcast.sent_count ?? 0}</strong>
            </div>
            <div>
              <span>Failed</span>
              <strong>{activeBroadcast.failed_count ?? 0}</strong>
            </div>
          </div>

          <div className="broadcast-progress-bar">
            <div style={{ width: `${progressPercent}%` }} />
          </div>

          {sendNotice ? <p className="broadcast-inline-note">{sendNotice}</p> : null}
          {sendError ? <p className="broadcast-inline-error">{sendError}</p> : null}

          {activeBroadcast.status === "paused" ? (
            <AppButton
              variant="primary"
              icon={<ReplayOutlined fontSize="small" />}
              loading={sendInFlightId === activeBroadcast.id}
              disabled={sendInFlightId === activeBroadcast.id}
              onClick={() => handleSend(activeBroadcast.id)}
            >
              Resume
            </AppButton>
          ) : null}
        </AppCard>
      ) : null}

      <section className="broadcast-history-section">
        <h3 className="broadcast-section-title">Broadcast history</h3>

        {historyError ? (
          <AppCard padding="medium">
            <ErrorState
              title="Broadcast history could not load"
              description={historyError}
              action={
                <AppButton variant="primary" icon={<RefreshOutlined fontSize="small" />} onClick={() => loadHistory()}>
                  Try again
                </AppButton>
              }
            />
          </AppCard>
        ) : (
          <AppTable
            columns={historyColumns}
            rows={broadcasts}
            loading={historyLoading}
            emptyTitle="No broadcasts yet"
            emptyDescription="Broadcasts you send will appear here with live delivery counts."
            rowKey="id"
            page={historyPage}
            onPageChange={setHistoryPage}
          />
        )}
      </section>

      <ConfirmDialog
        open={confirmOpen}
        title="Review broadcast"
        confirmLabel="Send now"
        cancelLabel="Cancel"
        confirmVariant="primary"
        loading={Boolean(draftBroadcast) && sendInFlightId === draftBroadcast.id}
        onConfirm={() => draftBroadcast && handleSend(draftBroadcast.id)}
        onCancel={handleCancelDraft}
        message={
          draftBroadcast ? (
            <div className="broadcast-confirm-body">
              <p>
                You are about to send a <strong>{channelLabel(draftBroadcast.channel)}</strong> broadcast
                {draftBroadcast.target_department ? (
                  <>
                    {" "}to the <strong>{draftBroadcast.target_department}</strong> department
                  </>
                ) : (
                  " to all departments"
                )}
                .
              </p>
              <p className="broadcast-confirm-estimate">
                <strong>~{draftBroadcast.estimated_recipient_count ?? 0} recipients</strong> as of right now — the exact number may shift slightly by the time you send.
              </p>
              <blockquote className="broadcast-confirm-message">{draftBroadcast.message_text}</blockquote>
              {sendError ? <p className="broadcast-inline-error">{sendError}</p> : null}
            </div>
          ) : null
        }
      />
    </section>
  );
}
