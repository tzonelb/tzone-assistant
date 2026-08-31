import { useCallback, useEffect, useMemo, useState } from "react";
import { useNavigate } from "react-router-dom";
import { AddOutlined, CloseOutlined, DeleteOutlineOutlined } from "@mui/icons-material";

import {
  createBroadcastRequest,
  customerOptionsRequest,
  deleteBroadcastRequest,
  listBroadcastsRequest,
  listCustomerSegmentsRequest,
  previewBroadcastRecipientCountRequest,
  sendBroadcastRequest,
  uploadMediaRequest,
} from "../../api/client";
import { EmptyState, ErrorState, LoadingState } from "../../components/common";
import { useAuth } from "../../contexts/AuthContext";
import "./BroadcastPageV2.css";

// Same data + actions as BroadcastPage.jsx (v1) — this is a visual rebuild
// only, matching the approved mockup's kicker + stat strip + bordered table.
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

function isThisMonth(value) {
  if (!value) return false;
  const normalized = /(?:Z|[+-]\d{2}:?\d{2})$/i.test(value) ? value : `${value}Z`;
  const date = new Date(normalized);
  if (Number.isNaN(date.getTime())) return false;
  const now = new Date();
  return date.getFullYear() === now.getFullYear() && date.getMonth() === now.getMonth();
}

// Mirrors BroadcastDetailPage's targetingSummary — real audience fields
// (segment_id / lifecycle_stage / tag / raw_numbers_json) are already on
// every row returned by listBroadcastsRequest, so no extra fetch is needed.
function audienceSummary(row, segments) {
  if (row.raw_numbers_json) {
    let numberCount = row.recipient_count ?? 0;
    try {
      const parsed = JSON.parse(row.raw_numbers_json);
      if (Array.isArray(parsed)) numberCount = parsed.length;
    } catch {
      // Fall back to recipient_count if the stored JSON is somehow malformed.
    }
    return `${numberCount} phone number${numberCount === 1 ? "" : "s"}`;
  }
  if (row.segment_id) {
    const segment = segments.find((item) => item.id === row.segment_id);
    return segment ? segment.name : "Segment";
  }
  if (row.lifecycle_stage) return humanize(row.lifecycle_stage);
  if (row.tag) return `Tag: ${row.tag}`;
  return "All contacts";
}

// Numbers can be pasted one per line or comma-separated (or a mix of both).
function parseNumbersInput(rawText) {
  return String(rawText || "")
    .split(/[\n,]/)
    .map((entry) => entry.trim())
    .filter(Boolean);
}

export default function BroadcastPageV2() {
  const navigate = useNavigate();
  const { user, companies } = useAuth();
  // Mirrors backend: create/send/delete all require channels.manage
  // (backend/api/routes/broadcasts.py) - channels.view only gets you the
  // list/report. Without this, a view-only user sees fully clickable
  // Create/Send/Delete controls that always 403.
  const canManageChannels = useMemo(() => {
    if (user?.is_super_admin) return true;
    const activeCompany = companies.find((company) => company.id === user?.active_company_id) || companies[0];
    return activeCompany?.role_code === "owner" || (activeCompany?.permission_codes || []).includes("channels.manage");
  }, [user, companies]);

  const [broadcasts, setBroadcasts] = useState([]);
  const [listLoading, setListLoading] = useState(true);
  const [listError, setListError] = useState("");
  const [statusFilter, setStatusFilter] = useState("");

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
  const [sendCountLoading, setSendCountLoading] = useState(false);
  const [sending, setSending] = useState(false);
  const [sendError, setSendError] = useState("");

  const [deleteTarget, setDeleteTarget] = useState(null);
  const [deleting, setDeleting] = useState(false);
  const [deleteError, setDeleteError] = useState("");

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

  async function openSendConfirm(broadcast) {
    setSendError("");
    setSendTarget(broadcast);
    // The stored recipient_count is a creation-time snapshot; segment/tag
    // membership may have shifted since. Recount live so the confirm
    // dialog matches what the send will actually resolve.
    setSendCountLoading(true);
    try {
      const result = await previewBroadcastRecipientCountRequest(broadcast.id);
      setSendTarget((current) => (
        current && current.id === broadcast.id ? { ...current, recipient_count: result.recipient_count } : current
      ));
    } catch {
      // Keep the stale snapshot if the live recount fails.
    } finally {
      setSendCountLoading(false);
    }
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
    setDeleteError("");
    setDeleteTarget(broadcast);
  }

  async function confirmDelete() {
    if (!deleteTarget) return;
    setDeleting(true);
    setDeleteError("");
    try {
      await deleteBroadcastRequest(deleteTarget.id);
      setDeleteTarget(null);
      await loadBroadcasts();
    } catch (requestError) {
      setDeleteError(requestError.message || "Could not delete the broadcast.");
    } finally {
      setDeleting(false);
    }
  }

  const draftCount = broadcasts.filter((broadcast) => broadcast.status === "draft").length;
  const totalSent = broadcasts.reduce((sum, broadcast) => sum + (Number(broadcast.sent_count) || 0), 0);
  const totalFailed = broadcasts.reduce((sum, broadcast) => sum + (Number(broadcast.failed_count) || 0), 0);
  const messagesSentThisMonth = broadcasts
    .filter((broadcast) => isThisMonth(broadcast.sent_at))
    .reduce((sum, broadcast) => sum + (Number(broadcast.sent_count) || 0), 0);

  const visibleBroadcasts = statusFilter
    ? broadcasts.filter((broadcast) => broadcast.status === statusFilter)
    : broadcasts;

  if (listLoading && !broadcasts.length) {
    return (
      <div className="tz-screen tzv2-broadcast-page">
        <LoadingState title="Loading broadcasts…" description="Retrieving campaigns and delivery totals." />
      </div>
    );
  }

  return (
    <div className="tz-screen tzv2-broadcast-page">
      <div className="tzv2-broadcast-head">
        <div>
          <span className="tz-kick tzv2-broadcast-kick">
            {broadcasts.length} campaign{broadcasts.length === 1 ? "" : "s"} · {messagesSentThisMonth} message{messagesSentThisMonth === 1 ? "" : "s"} sent this month
          </span>
        </div>
        <div className="tzv2-broadcast-head-actions">
          <button
            type="button"
            className={`btn btn-secondary${statusFilter === "draft" ? " tzv2-broadcast-filter-on" : ""}`}
            onClick={() => setStatusFilter((current) => (current === "draft" ? "" : "draft"))}
          >
            Drafts
          </button>
          {canManageChannels ? (
            <button type="button" className="btn btn-primary" onClick={openCompose}>
              <AddOutlined fontSize="small" /> New broadcast
            </button>
          ) : null}
        </div>
      </div>

      <div className="tzv2-broadcast-stats">
        <div className="tz-stat">
          <span className="tz-kick tzv2-broadcast-stat-kick">Campaigns</span>
          <div className="tz-fig tzv2-broadcast-fig">{broadcasts.length}</div>
        </div>
        <div className="tz-stat">
          <span className="tz-kick tzv2-broadcast-stat-kick">Drafts</span>
          <div className="tz-fig tzv2-broadcast-fig">{draftCount}</div>
        </div>
        <div className="tz-stat">
          <span className="tz-kick tzv2-broadcast-stat-kick">Sent</span>
          <div className="tz-fig tzv2-broadcast-fig">{totalSent}</div>
        </div>
        <div className="tz-stat">
          <span className="tz-kick tzv2-broadcast-stat-kick">Failed</span>
          <div className="tz-fig tzv2-broadcast-fig">{totalFailed}</div>
        </div>
      </div>

      {listError ? (
        <ErrorState title="Could not load broadcasts" description={listError} action={<button type="button" className="btn btn-primary" onClick={loadBroadcasts}>Retry</button>} />
      ) : visibleBroadcasts.length ? (
        <div className="tz-tablewrap tzv2-broadcast-tablewrap">
          <table className="table">
            <thead>
              <tr>
                <th>Campaign</th>
                <th>Audience</th>
                <th>Channel</th>
                <th style={{ textAlign: "right" }}>Sent</th>
                <th>Status</th>
                <th>Date</th>
                <th></th>
              </tr>
            </thead>
            <tbody>
              {visibleBroadcasts.map((broadcast) => (
                <tr key={broadcast.id}>
                  <td>
                    <button type="button" className="btn btn-ghost tzv2-broadcast-name-link" onClick={() => navigate(`/broadcast/${broadcast.id}`)}>
                      {broadcast.name}
                    </button>
                  </td>
                  <td className="tzv2-broadcast-audience">{audienceSummary(broadcast, segments)}</td>
                  <td className="tz-kick tzv2-broadcast-channel">{channelLabel(broadcast.channel)}</td>
                  <td className="tz-num" style={{ textAlign: "right" }}>{broadcast.sent_count}</td>
                  <td><span className="tag tag-neutral">{humanize(broadcast.status)}</span></td>
                  <td className="tz-num tzv2-broadcast-date">{formatDate(broadcast.created_at)}</td>
                  <td>
                    <div className="tzv2-broadcast-row-actions">
                      <button type="button" className="btn btn-ghost" onClick={() => navigate(`/broadcast/${broadcast.id}`)}>Open</button>
                      {canManageChannels && broadcast.status === "draft" ? (
                        <>
                          <button type="button" className="btn btn-secondary" onClick={() => openSendConfirm(broadcast)}>Send</button>
                          <button
                            type="button"
                            className="btn btn-ghost btn-icon"
                            aria-label={`Delete draft ${broadcast.name}`}
                            onClick={() => openDeleteConfirm(broadcast)}
                          >
                            <DeleteOutlineOutlined fontSize="small" />
                          </button>
                        </>
                      ) : null}
                      {canManageChannels && broadcast.status === "sending" ? (
                        // A broadcast stuck here means an earlier send was interrupted
                        // partway through (e.g. a request/proxy timeout on a large
                        // recipient list) - resuming picks up with whoever hasn't
                        // been sent to yet instead of leaving it as a dead end.
                        <button type="button" className="btn btn-secondary" onClick={() => openSendConfirm(broadcast)}>Resume send</button>
                      ) : null}
                    </div>
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      ) : (
        <EmptyState
          title={statusFilter ? "No drafts" : "No broadcasts yet"}
          description={statusFilter ? "There are no draft broadcasts right now." : "Create your first broadcast to reach your contacts."}
        />
      )}

      {composeOpen ? (
        <div
          className="dialog-backdrop"
          role="presentation"
          onMouseDown={(event) => { if (event.target === event.currentTarget) closeCompose(); }}
        >
          <form className="dialog tzv2-broadcast-compose-dialog" role="dialog" aria-modal="true" aria-labelledby="tzv2-broadcast-compose-title" onSubmit={handleCreate}>
            <div className="tzv2-broadcast-dialog-head">
              <span className="dialog-title" id="tzv2-broadcast-compose-title">New broadcast</span>
              <button type="button" className="btn btn-ghost btn-icon" aria-label="Close dialog" onClick={closeCompose}>
                <CloseOutlined fontSize="small" />
              </button>
            </div>
            <div className="dialog-body tzv2-broadcast-dialog-body">
              <div className="field">
                <label>Name</label>
                <input
                  className="input"
                  value={name}
                  placeholder="e.g. July sale announcement"
                  maxLength={200}
                  autoFocus
                  onChange={(event) => setName(event.target.value)}
                />
              </div>

              <div className="field">
                <label>Message</label>
                <textarea
                  className="input tzv2-broadcast-message-input"
                  value={messageText}
                  placeholder="Write the message that will be sent to every recipient..."
                  maxLength={MESSAGE_LIMIT}
                  rows={5}
                  onChange={(event) => setMessageText(event.target.value)}
                />
                <span className={`tzv2-broadcast-char-counter${messageText.length >= MESSAGE_LIMIT ? " tzv2-broadcast-char-limit" : ""}`}>
                  {messageText.length} / {MESSAGE_LIMIT}
                </span>
              </div>

              <div className="field">
                <label>Attach media (optional)</label>
                {mediaUrl ? (
                  <div className="tzv2-broadcast-media-attached">
                    <span>{mediaFileName || mediaUrl} <em>({mediaType})</em></span>
                    <button type="button" className="btn btn-ghost" onClick={removeMedia}>Remove</button>
                  </div>
                ) : (
                  <>
                    <input
                      type="file"
                      accept="image/*,video/*,audio/*"
                      disabled={mediaUploading}
                      onChange={handleMediaFileChange}
                    />
                    {mediaUploading ? <span className="tzv2-broadcast-field-note">Uploading…</span> : null}
                  </>
                )}
                {mediaError ? <span className="tzv2-broadcast-field-note tzv2-broadcast-error-text">{mediaError}</span> : null}
              </div>

              <div className="field">
                <label>Channel</label>
                <select
                  className="input"
                  value={targetMode === "numbers" ? "whatsapp" : channel}
                  disabled={targetMode === "numbers"}
                  onChange={(event) => setChannel(event.target.value)}
                >
                  {CHANNEL_OPTIONS.map((option) => (
                    <option value={option.value} key={option.value}>{option.label}</option>
                  ))}
                </select>
                {targetMode === "numbers" ? (
                  <span className="tzv2-broadcast-field-note">Number-list targeting is WhatsApp-only.</span>
                ) : null}
              </div>

              <div className="tzv2-broadcast-target-section">
                <label>Target audience</label>
                <div className="seg" role="radiogroup" aria-label="Target audience mode">
                  <label className="seg-opt">
                    <input
                      type="radio"
                      name="tzv2-broadcast-target-mode"
                      checked={targetMode === "segment"}
                      onChange={() => selectTargetMode("segment")}
                    />
                    Segment
                  </label>
                  <label className="seg-opt">
                    <input
                      type="radio"
                      name="tzv2-broadcast-target-mode"
                      checked={targetMode === "filter"}
                      onChange={() => selectTargetMode("filter")}
                    />
                    Filter
                  </label>
                  <label className="seg-opt">
                    <input
                      type="radio"
                      name="tzv2-broadcast-target-mode"
                      checked={targetMode === "numbers"}
                      onChange={() => selectTargetMode("numbers")}
                    />
                    Phone numbers
                  </label>
                </div>

                {targetMode === "numbers" ? (
                  <div className="field">
                    <label>Phone numbers</label>
                    <textarea
                      className="input tzv2-broadcast-message-input"
                      value={numbersInput}
                      placeholder={"One per line, or comma-separated, e.g.\n+1 555 0100\n+1 555 0101, +1 555 0102"}
                      rows={5}
                      onChange={(event) => setNumbersInput(event.target.value)}
                    />
                    <span className="tzv2-broadcast-numbers-count">
                      {parseNumbersInput(numbersInput).length} number{parseNumbersInput(numbersInput).length === 1 ? "" : "s"}
                    </span>
                  </div>
                ) : targetMode === "segment" ? (
                  <div className="field">
                    <label>Segment</label>
                    <select className="input" value={segmentId} onChange={(event) => setSegmentId(event.target.value)}>
                      <option value="">Choose a segment…</option>
                      {segments.map((segment) => (
                        <option value={segment.id} key={segment.id}>{segment.name}</option>
                      ))}
                    </select>
                  </div>
                ) : (
                  <div className="tzv2-broadcast-filter-fields">
                    <div className="field">
                      <label>Lifecycle stage</label>
                      <select className="input" value={lifecycleStage} onChange={(event) => setLifecycleStage(event.target.value)}>
                        <option value="">Any stage</option>
                        {lifecycleStages.map((stage) => (
                          <option value={stage} key={stage}>{humanize(stage)}</option>
                        ))}
                      </select>
                    </div>
                    <div className="field">
                      <label>Tag</label>
                      <input
                        className="input"
                        value={tagInput}
                        placeholder="e.g. vip"
                        maxLength={80}
                        onChange={(event) => setTagInput(event.target.value)}
                      />
                    </div>
                  </div>
                )}
              </div>

              {composeError ? <p className="tzv2-broadcast-error-text">{composeError}</p> : null}
            </div>
            <div className="dialog-actions">
              <button type="button" className="btn btn-secondary" disabled={creating} onClick={closeCompose}>Cancel</button>
              <button type="submit" className="btn btn-primary" disabled={creating}>{creating ? "Creating…" : "Create draft"}</button>
            </div>
          </form>
        </div>
      ) : null}

      {sendTarget ? (
        <div
          className="dialog-backdrop"
          role="presentation"
          onMouseDown={(event) => { if (event.target === event.currentTarget && !sending) setSendTarget(null); }}
        >
          <section className="dialog" role="dialog" aria-modal="true" aria-labelledby="tzv2-broadcast-send-title">
            <span className="dialog-title" id="tzv2-broadcast-send-title">Send broadcast</span>
            <div className="dialog-body">
              <p>
                Send "{sendTarget.name}" to {sendCountLoading ? "…" : sendTarget.recipient_count} contact{sendTarget.recipient_count === 1 ? "" : "s"} on{" "}
                {channelLabel(sendTarget.channel)}? This cannot be undone.
              </p>
              {sendError ? <p className="tzv2-broadcast-error-text">{sendError}</p> : null}
            </div>
            <div className="dialog-actions">
              <button type="button" className="btn btn-secondary" disabled={sending} onClick={() => setSendTarget(null)}>Cancel</button>
              <button type="button" className="btn btn-primary" disabled={sending} onClick={confirmSend}>{sending ? "Sending…" : "Send now"}</button>
            </div>
          </section>
        </div>
      ) : null}

      {deleteTarget ? (
        <div
          className="dialog-backdrop"
          role="presentation"
          onMouseDown={(event) => { if (event.target === event.currentTarget && !deleting) setDeleteTarget(null); }}
        >
          <section className="dialog" role="dialog" aria-modal="true" aria-labelledby="tzv2-broadcast-delete-title">
            <span className="dialog-title" id="tzv2-broadcast-delete-title">Delete draft broadcast</span>
            <div className="dialog-body">
              <p>Delete the draft "{deleteTarget.name}"? This cannot be undone.</p>
              {deleteError ? <p className="tzv2-broadcast-error-text">{deleteError}</p> : null}
            </div>
            <div className="dialog-actions">
              <button type="button" className="btn btn-secondary" disabled={deleting} onClick={() => setDeleteTarget(null)}>Cancel</button>
              <button type="button" className="btn btn-primary" disabled={deleting} onClick={confirmDelete}>{deleting ? "Deleting…" : "Delete"}</button>
            </div>
          </section>
        </div>
      ) : null}
    </div>
  );
}
