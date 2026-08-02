import { useCallback, useEffect, useState } from "react";
import { useNavigate } from "react-router-dom";
import {
  AddOutlined,
  CampaignOutlined,
  CloseOutlined,
  DraftsOutlined,
  GroupsOutlined,
} from "@mui/icons-material";

import {
  createBroadcastRequest,
  customerOptionsRequest,
  deleteBroadcastRequest,
  listBroadcastsRequest,
  listCustomerSegmentsRequest,
  sendBroadcastRequest,
  uploadMediaRequest,
} from "../../api/client";
import { AppButton, AppCard, AppTable, ConfirmDialog, ErrorState, PageHeader, StatusBadge } from "../../components/common";
import "./BroadcastPage.css";
import "../analytics/AnalyticsPage.css";

const MESSAGE_LIMIT = 4000;

const CHANNEL_OPTIONS = [
  { value: "telegram", label: "Telegram" },
  { value: "messenger", label: "Messenger" },
  { value: "instagram", label: "Instagram" },
  { value: "whatsapp", label: "WhatsApp" },
];

const CHANNEL_LABELS = CHANNEL_OPTIONS.reduce((accumulator, option) => {
  accumulator[option.value] = option.label;
  return accumulator;
}, {});

function humanize(value) {
  return String(value || "").replaceAll("_", " ").replace(/\b\w/g, (letter) => letter.toUpperCase());
}

function channelLabel(value) {
  return CHANNEL_LABELS[value] || humanize(value);
}

function formatDate(value) {
  if (!value) return "—";
  const normalized = /(?:Z|[+-]\d{2}:?\d{2})$/i.test(value) ? value : `${value}Z`;
  const date = new Date(normalized);
  return Number.isNaN(date.getTime()) ? "—" : date.toLocaleDateString();
}

function statusTone(status) {
  if (status === "draft") return "warning";
  if (status === "sending") return "info";
  if (status === "sent") return "success";
  return "neutral";
}

function BroadcastStatCard({ title, value, description, icon: Icon }) {
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

const EMPTY_COMPOSE_FORM = {
  name: "",
  messageText: "",
  channel: "telegram",
  targetMode: "segment",
  segmentId: "",
  lifecycleStage: "",
  tagInput: "",
  numbersInput: "",
};

// Numbers can be pasted one per line or comma-separated (or a mix of both).
function parseNumbersInput(rawText) {
  return String(rawText || "")
    .split(/[\n,]/)
    .map((entry) => entry.trim())
    .filter(Boolean);
}

export default function BroadcastPage() {
  const navigate = useNavigate();

  const [broadcasts, setBroadcasts] = useState([]);
  const [listLoading, setListLoading] = useState(true);
  const [listError, setListError] = useState("");

  const [composeOpen, setComposeOpen] = useState(false);
  const [name, setName] = useState(EMPTY_COMPOSE_FORM.name);
  const [messageText, setMessageText] = useState(EMPTY_COMPOSE_FORM.messageText);
  const [channel, setChannel] = useState(EMPTY_COMPOSE_FORM.channel);
  const [targetMode, setTargetMode] = useState(EMPTY_COMPOSE_FORM.targetMode);
  const [segmentId, setSegmentId] = useState(EMPTY_COMPOSE_FORM.segmentId);
  const [lifecycleStage, setLifecycleStage] = useState(EMPTY_COMPOSE_FORM.lifecycleStage);
  const [tagInput, setTagInput] = useState(EMPTY_COMPOSE_FORM.tagInput);
  const [numbersInput, setNumbersInput] = useState(EMPTY_COMPOSE_FORM.numbersInput);
  const [creating, setCreating] = useState(false);
  const [composeError, setComposeError] = useState("");

  const [mediaUrl, setMediaUrl] = useState("");
  const [mediaType, setMediaType] = useState("");
  const [mediaFileName, setMediaFileName] = useState("");
  const [mediaUploading, setMediaUploading] = useState(false);
  const [mediaError, setMediaError] = useState("");

  const [segments, setSegments] = useState([]);
  const [lifecycleStages, setLifecycleStages] = useState([]);

  const [sendTarget, setSendTarget] = useState(null);
  const [sending, setSending] = useState(false);
  const [sendError, setSendError] = useState("");

  const [deleteTarget, setDeleteTarget] = useState(null);
  const [deleting, setDeleting] = useState(false);

  const loadBroadcasts = useCallback(async () => {
    setListLoading(true);
    setListError("");
    try {
      const result = await listBroadcastsRequest();
      setBroadcasts(Array.isArray(result?.items) ? result.items : []);
    } catch (requestError) {
      setListError(requestError.message || "Broadcasts could not be loaded.");
    } finally {
      setListLoading(false);
    }
  }, []);

  useEffect(() => { loadBroadcasts(); }, [loadBroadcasts]);

  useEffect(() => {
    customerOptionsRequest()
      .then((result) => setLifecycleStages(Array.isArray(result?.lifecycle_stages) ? result.lifecycle_stages : []))
      .catch(() => {});
    listCustomerSegmentsRequest()
      .then((result) => setSegments(Array.isArray(result?.items) ? result.items : []))
      .catch(() => {});
  }, []);

  function resetComposeForm() {
    setName(EMPTY_COMPOSE_FORM.name);
    setMessageText(EMPTY_COMPOSE_FORM.messageText);
    setChannel(EMPTY_COMPOSE_FORM.channel);
    setTargetMode(EMPTY_COMPOSE_FORM.targetMode);
    setSegmentId(EMPTY_COMPOSE_FORM.segmentId);
    setLifecycleStage(EMPTY_COMPOSE_FORM.lifecycleStage);
    setTagInput(EMPTY_COMPOSE_FORM.tagInput);
    setNumbersInput(EMPTY_COMPOSE_FORM.numbersInput);
    setComposeError("");
    setMediaUrl("");
    setMediaType("");
    setMediaFileName("");
    setMediaError("");
  }

  async function handleMediaFileChange(event) {
    const file = event.target.files?.[0];
    event.target.value = "";
    if (!file) return;
    setMediaUploading(true);
    setMediaError("");
    try {
      const result = await uploadMediaRequest(file);
      setMediaUrl(result.url);
      setMediaType(result.media_type);
      setMediaFileName(file.name);
    } catch (requestError) {
      setMediaError(requestError.message || "Could not upload this file.");
    } finally {
      setMediaUploading(false);
    }
  }

  function removeMedia() {
    setMediaUrl("");
    setMediaType("");
    setMediaFileName("");
    setMediaError("");
  }

  function openCompose() {
    resetComposeForm();
    setComposeOpen(true);
  }

  function closeCompose() {
    if (creating) return;
    setComposeOpen(false);
  }

  function selectTargetMode(mode) {
    setTargetMode(mode);
    // Number-list targeting only ever sends on WhatsApp — force the
    // channel select so the two choices can never disagree.
    if (mode === "numbers") {
      setChannel("whatsapp");
    }
  }

  async function handleCreate(event) {
    event.preventDefault();
    setComposeError("");

    const trimmedName = name.trim();
    const trimmedMessage = messageText.trim();

    if (!trimmedName) {
      setComposeError("Broadcast name is required.");
      return;
    }
    if (!trimmedMessage) {
      setComposeError("Message text is required.");
      return;
    }
    if (targetMode === "segment" && !segmentId) {
      setComposeError("Choose a segment to target.");
      return;
    }
    if (targetMode === "filter" && !lifecycleStage && !tagInput.trim()) {
      setComposeError("Choose a lifecycle stage or enter a tag to filter by.");
      return;
    }
    const parsedNumbers = parseNumbersInput(numbersInput);
    if (targetMode === "numbers" && parsedNumbers.length === 0) {
      setComposeError("Add at least one phone number.");
      return;
    }

    setCreating(true);
    try {
      const payload = {
        name: trimmedName,
        message_text: trimmedMessage,
        channel: targetMode === "numbers" ? "whatsapp" : channel,
        segment_id: targetMode === "segment" ? segmentId : undefined,
        lifecycle_stage: targetMode === "filter" ? (lifecycleStage || undefined) : undefined,
        tag: targetMode === "filter" ? (tagInput.trim() || undefined) : undefined,
        numbers: targetMode === "numbers" ? parsedNumbers : undefined,
        media_url: mediaUrl || undefined,
        media_type: mediaUrl ? mediaType : undefined,
      };
      const created = await createBroadcastRequest(payload);
      setComposeOpen(false);
      resetComposeForm();
      await loadBroadcasts();
      navigate(`/broadcast/${created.id}`);
    } catch (requestError) {
      setComposeError(requestError.message || "Could not create the broadcast.");
    } finally {
      setCreating(false);
    }
  }

  function openSendConfirm(broadcast) {
    setSendError("");
    setSendTarget(broadcast);
  }

  async function confirmSend() {
    if (!sendTarget) return;
    setSending(true);
    setSendError("");
    try {
      await sendBroadcastRequest(sendTarget.id);
      setSendTarget(null);
      await loadBroadcasts();
    } catch (requestError) {
      setSendError(requestError.message || "Could not send the broadcast.");
    } finally {
      setSending(false);
    }
  }

  function openDeleteConfirm(broadcast) {
    setDeleteTarget(broadcast);
  }

  async function confirmDelete() {
    if (!deleteTarget) return;
    setDeleting(true);
    try {
      await deleteBroadcastRequest(deleteTarget.id);
      setDeleteTarget(null);
      await loadBroadcasts();
    } catch (requestError) {
      setListError(requestError.message || "Could not delete the broadcast.");
    } finally {
      setDeleting(false);
    }
  }

  const draftCount = broadcasts.filter((broadcast) => broadcast.status === "draft").length;
  const totalRecipientsReached = broadcasts
    .filter((broadcast) => broadcast.status === "sent")
    .reduce((sum, broadcast) => sum + (Number(broadcast.sent_count) || 0), 0);

  const columns = [
    {
      key: "name",
      label: "Name",
      render: (value, row) => (
        <button type="button" className="broadcast-name-cell-link" onClick={() => navigate(`/broadcast/${row.id}`)}>
          {value}
        </button>
      ),
    },
    {
      key: "channel",
      label: "Channel",
      render: (value) => channelLabel(value),
    },
    {
      key: "status",
      label: "Status",
      render: (value) => <StatusBadge status={value} tone={statusTone(value)} label={humanize(value)} />,
    },
    {
      key: "recipient_count",
      label: "Recipients",
      align: "right",
    },
    {
      key: "sent_count",
      label: "Sent",
      align: "right",
    },
    {
      key: "failed_count",
      label: "Failed",
      align: "right",
    },
    {
      key: "created_at",
      label: "Created",
      render: (value) => formatDate(value),
    },
    {
      key: "_actions",
      label: "",
      align: "right",
      render: (_value, row) => (
        <div className="broadcast-row-actions">
          <AppButton size="small" variant="secondary" onClick={() => navigate(`/broadcast/${row.id}`)}>View report</AppButton>
          {row.status === "draft" ? (
            <>
              <AppButton size="small" variant="primary" onClick={() => openSendConfirm(row)}>Send</AppButton>
              <AppButton size="small" variant="danger" onClick={() => openDeleteConfirm(row)}>Delete</AppButton>
            </>
          ) : null}
          {row.status === "sending" ? (
            // A broadcast stuck here means an earlier send was interrupted
            // partway through (e.g. a request/proxy timeout on a large
            // recipient list) - resuming picks up with whoever hasn't
            // been sent to yet instead of leaving it as a dead end.
            <AppButton size="small" variant="primary" onClick={() => openSendConfirm(row)}>Resume send</AppButton>
          ) : null}
        </div>
      ),
    },
  ];

  return (
    <section className="broadcast-page">
      <PageHeader
        actions={
          <AppButton variant="primary" icon={<AddOutlined fontSize="small" />} onClick={openCompose}>
            Create Broadcast
          </AppButton>
        }
      />

      <section className="analytics-stats-grid broadcast-stats-grid">
        <BroadcastStatCard
          title="Total broadcasts"
          value={broadcasts.length}
          description="Drafts and sent, all time"
          icon={CampaignOutlined}
        />
        <BroadcastStatCard
          title="Drafts"
          value={draftCount}
          description="Ready to review and send"
          icon={DraftsOutlined}
        />
        <BroadcastStatCard
          title="Recipients reached"
          value={totalRecipientsReached}
          description="Sum of sent broadcasts"
          icon={GroupsOutlined}
        />
      </section>

      {listError ? (
        <ErrorState title="Could not load broadcasts" description={listError} action={<AppButton variant="primary" onClick={loadBroadcasts}>Retry</AppButton>} />
      ) : (
        <AppTable
          columns={columns}
          rows={broadcasts}
          loading={listLoading}
          emptyTitle="No broadcasts yet"
          emptyDescription="Create your first broadcast to reach your contacts."
          page={1}
          pageSize={Math.max(broadcasts.length, 1)}
          totalRows={broadcasts.length}
          onPageChange={() => {}}
        />
      )}

      {composeOpen ? (
        <div
          className="tz-dialog-backdrop"
          role="presentation"
          onMouseDown={(event) => { if (event.target === event.currentTarget) closeCompose(); }}
        >
          <form className="tz-dialog broadcast-compose-dialog" onSubmit={handleCreate}>
            <header className="tz-dialog-header">
              <h3>New broadcast</h3>
              <button type="button" className="tz-dialog-close" onClick={closeCompose}>
                <CloseOutlined fontSize="small" />
              </button>
            </header>
            <div className="tz-dialog-body">
              <div className="broadcast-compose-form">
                <label className="broadcast-field">
                  Name
                  <input
                    value={name}
                    placeholder="e.g. July sale announcement"
                    maxLength={200}
                    autoFocus
                    onChange={(event) => setName(event.target.value)}
                  />
                </label>

                <label className="broadcast-field">
                  Message
                  <textarea
                    className="broadcast-message-input"
                    value={messageText}
                    placeholder="Write the message that will be sent to every recipient..."
                    maxLength={MESSAGE_LIMIT}
                    rows={5}
                    onChange={(event) => setMessageText(event.target.value)}
                  />
                  <span className={`broadcast-char-counter ${messageText.length >= MESSAGE_LIMIT ? "is-limit" : ""}`}>
                    {messageText.length} / {MESSAGE_LIMIT}
                  </span>
                </label>

                <label className="broadcast-field">
                  Attach media (optional)
                  {mediaUrl ? (
                    <div className="broadcast-media-attached">
                      <span>{mediaFileName || mediaUrl} <em>({mediaType})</em></span>
                      <button type="button" onClick={removeMedia}>Remove</button>
                    </div>
                  ) : (
                    <>
                      <input
                        type="file"
                        accept="image/*,video/*,audio/*"
                        disabled={mediaUploading}
                        onChange={handleMediaFileChange}
                      />
                      {mediaUploading ? <span className="broadcast-field-note">Uploading…</span> : null}
                    </>
                  )}
                  {mediaError ? <span className="broadcast-field-note broadcast-media-error">{mediaError}</span> : null}
                </label>

                <label className="broadcast-field">
                  Channel
                  <select
                    className="tz-select"
                    value={targetMode === "numbers" ? "whatsapp" : channel}
                    disabled={targetMode === "numbers"}
                    onChange={(event) => setChannel(event.target.value)}
                  >
                    {CHANNEL_OPTIONS.map((option) => (
                      <option value={option.value} key={option.value}>{option.label}</option>
                    ))}
                  </select>
                  {targetMode === "numbers" ? (
                    <span className="broadcast-field-note">Number-list targeting is WhatsApp-only.</span>
                  ) : null}
                </label>

                <div className="broadcast-target-section">
                  <span className="broadcast-field-label">Target audience</span>
                  <div className="broadcast-target-tabs">
                    <button
                      type="button"
                      className={`broadcast-target-tab ${targetMode === "segment" ? "is-active" : ""}`}
                      onClick={() => selectTargetMode("segment")}
                    >
                      Segment
                    </button>
                    <button
                      type="button"
                      className={`broadcast-target-tab ${targetMode === "filter" ? "is-active" : ""}`}
                      onClick={() => selectTargetMode("filter")}
                    >
                      Filter
                    </button>
                    <button
                      type="button"
                      className={`broadcast-target-tab ${targetMode === "numbers" ? "is-active" : ""}`}
                      onClick={() => selectTargetMode("numbers")}
                    >
                      Phone numbers
                    </button>
                  </div>

                  {targetMode === "numbers" ? (
                    <label className="broadcast-field">
                      Phone numbers
                      <textarea
                        className="broadcast-message-input broadcast-numbers-input"
                        value={numbersInput}
                        placeholder={"One per line, or comma-separated, e.g.\n+1 555 0100\n+1 555 0101, +1 555 0102"}
                        rows={5}
                        onChange={(event) => setNumbersInput(event.target.value)}
                      />
                      <span className="broadcast-numbers-count">
                        {parseNumbersInput(numbersInput).length} number{parseNumbersInput(numbersInput).length === 1 ? "" : "s"}
                      </span>
                    </label>
                  ) : targetMode === "segment" ? (
                    <label className="broadcast-field">
                      Segment
                      <select className="tz-select" value={segmentId} onChange={(event) => setSegmentId(event.target.value)}>
                        <option value="">Choose a segment…</option>
                        {segments.map((segment) => (
                          <option value={segment.id} key={segment.id}>{segment.name}</option>
                        ))}
                      </select>
                    </label>
                  ) : (
                    <div className="broadcast-filter-fields">
                      <label className="broadcast-field">
                        Lifecycle stage
                        <select className="tz-select" value={lifecycleStage} onChange={(event) => setLifecycleStage(event.target.value)}>
                          <option value="">Any stage</option>
                          {lifecycleStages.map((stage) => (
                            <option value={stage} key={stage}>{humanize(stage)}</option>
                          ))}
                        </select>
                      </label>
                      <label className="broadcast-field">
                        Tag
                        <input
                          value={tagInput}
                          placeholder="e.g. vip"
                          maxLength={80}
                          onChange={(event) => setTagInput(event.target.value)}
                        />
                      </label>
                    </div>
                  )}
                </div>

                {composeError ? <p className="broadcast-error-text">{composeError}</p> : null}
              </div>
            </div>
            <footer className="tz-dialog-actions">
              <AppButton type="button" variant="secondary" disabled={creating} onClick={closeCompose}>Cancel</AppButton>
              <AppButton type="submit" variant="primary" loading={creating}>Create draft</AppButton>
            </footer>
          </form>
        </div>
      ) : null}

      {sendTarget ? (
        <div
          className="tz-dialog-backdrop"
          role="presentation"
          onMouseDown={(event) => { if (event.target === event.currentTarget && !sending) setSendTarget(null); }}
        >
          <section className="tz-dialog" role="dialog" aria-modal="true" aria-labelledby="broadcast-send-dialog-title">
            <header className="tz-dialog-header">
              <h3 id="broadcast-send-dialog-title">Send broadcast</h3>
              <button type="button" className="tz-dialog-close" onClick={() => !sending && setSendTarget(null)}>
                <CloseOutlined fontSize="small" />
              </button>
            </header>
            <div className="tz-dialog-body">
              <p>
                Send “{sendTarget.name}” to {sendTarget.recipient_count} contact{sendTarget.recipient_count === 1 ? "" : "s"} on{" "}
                {channelLabel(sendTarget.channel)}? This cannot be undone.
              </p>
              {sendError ? <p className="broadcast-error-text">{sendError}</p> : null}
            </div>
            <footer className="tz-dialog-actions">
              <AppButton variant="secondary" disabled={sending} onClick={() => setSendTarget(null)}>Cancel</AppButton>
              <AppButton variant="primary" loading={sending} onClick={confirmSend}>Send now</AppButton>
            </footer>
          </section>
        </div>
      ) : null}

      <ConfirmDialog
        open={Boolean(deleteTarget)}
        title="Delete draft broadcast"
        message={`Delete the draft "${deleteTarget?.name}"? This cannot be undone.`}
        confirmLabel="Delete"
        confirmVariant="danger"
        loading={deleting}
        onConfirm={confirmDelete}
        onCancel={() => setDeleteTarget(null)}
      />
    </section>
  );
}
