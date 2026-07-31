import { useEffect, useMemo, useState } from "react";
import { AddOutlined, CloseOutlined, DeleteOutlineOutlined, SendOutlined } from "@mui/icons-material";
import { useSearchParams } from "react-router-dom";
import {
  createScheduledPostRequest,
  deleteScheduledPostRequest,
  listScheduledPostsRequest,
  publishScheduledPostNowRequest,
  scheduledPostOptionsRequest,
  uploadMediaRequest,
} from "../../api/client";
import { AppButton, AppCard, ConfirmDialog, ErrorState, LoadingState, StatusBadge } from "../../components/common";
import { channelIcon } from "./channelIcon";
import "./PublishPage.css";

const TABS = [
  { key: "scheduled", label: "Queue" },
  { key: "draft", label: "Drafts" },
  { key: "sent", label: "Sent" },
  { key: "failed", label: "Failed" },
];

const STATUS_TONE = { draft: "neutral", scheduled: "info", sent: "success", failed: "danger" };

function formatDateTime(value) {
  if (!value) return "—";
  const normalized = /(?:Z|[+-]\d{2}:?\d{2})$/i.test(value) ? value : `${value}Z`;
  const date = new Date(normalized);
  return Number.isNaN(date.getTime()) ? "—" : date.toLocaleString();
}

const POST_TYPE_LABELS = { feed: "Post", reels: "Reel", story: "Story" };

function CreatePostDialog({ open, channelAccounts, saving, error, onCancel, onSave }) {
  const [text, setText] = useState("");
  const [selectedAccountIds, setSelectedAccountIds] = useState([]);
  const [contentOverrides, setContentOverrides] = useState({});
  const [channelPostTypes, setChannelPostTypes] = useState({});
  const [expandedAccountId, setExpandedAccountId] = useState(null);
  const [channelPickerOpen, setChannelPickerOpen] = useState(false);
  const [mediaUrl, setMediaUrl] = useState("");
  const [mediaType, setMediaType] = useState("");
  const [mediaFileName, setMediaFileName] = useState("");
  const [mediaUploading, setMediaUploading] = useState(false);
  const [mediaError, setMediaError] = useState("");
  const [when, setWhen] = useState("now");
  const [scheduledAt, setScheduledAt] = useState("");

  useEffect(() => {
    if (!open) {
      setText(""); setSelectedAccountIds([]); setContentOverrides({}); setChannelPostTypes({});
      setExpandedAccountId(null); setChannelPickerOpen(false); setMediaUrl(""); setMediaType("");
      setMediaFileName(""); setMediaError(""); setWhen("now"); setScheduledAt("");
    }
  }, [open]);

  if (!open) return null;

  function toggleAccount(accountId) {
    setSelectedAccountIds((current) => {
      const next = current.includes(accountId) ? current.filter((id) => id !== accountId) : [...current, accountId];
      if (!current.includes(accountId)) setExpandedAccountId(accountId);
      return next;
    });
  }

  function setOverrideText(accountId, value) {
    setContentOverrides((current) => ({ ...current, [accountId]: value }));
  }

  function setPostType(accountId, postType) {
    setChannelPostTypes((current) => ({ ...current, [accountId]: postType }));
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

  function buildPayload({ asDraft }) {
    const overrides = {};
    const postTypes = {};
    selectedAccountIds.forEach((accountId) => {
      const overrideText = (contentOverrides[accountId] || "").trim();
      if (overrideText) overrides[accountId] = overrideText;
      const postType = channelPostTypes[accountId];
      if (postType && postType !== "feed") postTypes[accountId] = postType;
    });
    return {
      text: text.trim() || null,
      channel_account_ids: selectedAccountIds,
      media_urls: mediaUrl ? [mediaUrl] : [],
      media_type: mediaUrl ? mediaType : null,
      content_overrides: overrides,
      channel_post_types: postTypes,
      status: asDraft ? "draft" : "scheduled",
      scheduled_at: asDraft
        ? null
        : (when === "schedule" ? new Date(scheduledAt).toISOString() : new Date().toISOString()),
    };
  }

  function submit(event) {
    event.preventDefault();
    if (!canSave) return;
    onSave(buildPayload({ asDraft: false }));
  }

  function submitDraft() {
    if (!text.trim() && !mediaUrl) return;
    if (selectedAccountIds.length === 0) return;
    onSave(buildPayload({ asDraft: true }));
  }

  const canSave = (text.trim() || mediaUrl) && selectedAccountIds.length > 0 && (when === "now" || scheduledAt);

  return (
    <div className="tz-dialog-backdrop" role="presentation" onMouseDown={(event) => { if (event.target === event.currentTarget && !saving) onCancel(); }}>
      <form className="tz-dialog publish-create-dialog" onSubmit={submit}>
        <header className="tz-dialog-header">
          <h3>Create Post</h3>
          <button type="button" className="tz-dialog-close" onClick={onCancel} disabled={saving}><CloseOutlined fontSize="small" /></button>
        </header>
        <div className="tz-dialog-body">
          <div className="publish-channel-strip">
            {channelAccounts.length === 0 ? (
              <p className="publish-no-channels">No Facebook Page or Instagram account connected yet — connect one from Company Settings → Channels first.</p>
            ) : (
              <>
                {channelAccounts.filter((account) => selectedAccountIds.includes(account.id)).map((account) => {
                  const Icon = channelIcon(account.channel);
                  return (
                    <button
                      type="button"
                      key={account.id}
                      className="publish-channel-avatar is-selected"
                      onClick={() => setExpandedAccountId((current) => (current === account.id ? null : account.id))}
                      title={account.name}
                    >
                      <Icon fontSize="small" />
                      <span>{account.name}</span>
                    </button>
                  );
                })}
                <div className="publish-channel-picker-wrap">
                  <button type="button" className="publish-channel-add-btn" onClick={() => setChannelPickerOpen((current) => !current)}>
                    <AddOutlined fontSize="small" />
                  </button>
                  {channelPickerOpen ? (
                    <div className="publish-channel-picker">
                      {channelAccounts.map((account) => {
                        const Icon = channelIcon(account.channel);
                        const selected = selectedAccountIds.includes(account.id);
                        return (
                          <label key={account.id} className="publish-channel-picker-row">
                            <input type="checkbox" checked={selected} onChange={() => toggleAccount(account.id)} />
                            <Icon fontSize="small" />
                            <span>{account.name}</span>
                          </label>
                        );
                      })}
                    </div>
                  ) : null}
                </div>
              </>
            )}
          </div>

          <label className="ai-teaching-field">
            Post text (shared across every selected channel by default)
            <textarea rows={4} value={text} disabled={saving} onChange={(event) => setText(event.target.value)} placeholder="Start writing..." />
          </label>

          {selectedAccountIds.length > 0 ? (
            <div className="publish-channel-panels">
              {channelAccounts.filter((account) => selectedAccountIds.includes(account.id)).map((account) => {
                const Icon = channelIcon(account.channel);
                const isExpanded = expandedAccountId === account.id;
                const postType = channelPostTypes[account.id] || "feed";
                return (
                  <div key={account.id} className={`publish-channel-panel ${isExpanded ? "is-expanded" : ""}`}>
                    <button type="button" className="publish-channel-panel-head" onClick={() => setExpandedAccountId(isExpanded ? null : account.id)}>
                      <Icon fontSize="small" />
                      <span>{account.name}</span>
                      <em>{POST_TYPE_LABELS[postType]}</em>
                    </button>
                    {isExpanded ? (
                      <div className="publish-channel-panel-body">
                        <div className="publish-post-type-row">
                          {Object.entries(POST_TYPE_LABELS).map(([value, label]) => (
                            <label key={value} className="publish-when-option">
                              <input
                                type="radio"
                                name={`post-type-${account.id}`}
                                checked={postType === value}
                                disabled={saving}
                                onChange={() => setPostType(account.id, value)}
                              />
                              {label}
                            </label>
                          ))}
                        </div>
                        <textarea
                          rows={3}
                          value={contentOverrides[account.id] || ""}
                          disabled={saving}
                          placeholder={text || "Uses the shared text above unless you customize it here..."}
                          onChange={(event) => setOverrideText(account.id, event.target.value)}
                        />
                      </div>
                    ) : null}
                  </div>
                );
              })}
            </div>
          ) : null}

          <label className="ai-teaching-field">
            Media (optional)
            {mediaUrl ? (
              <div className="broadcast-media-attached">
                <span>{mediaFileName || mediaUrl} <em>({mediaType})</em></span>
                <button type="button" onClick={() => { setMediaUrl(""); setMediaType(""); setMediaFileName(""); }}>Remove</button>
              </div>
            ) : (
              <>
                <input type="file" accept="image/*,video/*" disabled={mediaUploading} onChange={handleMediaFileChange} />
                {mediaUploading ? <span className="broadcast-field-note">Uploading…</span> : null}
              </>
            )}
            {mediaError ? <span className="broadcast-field-note broadcast-media-error">{mediaError}</span> : null}
          </label>

          <div className="publish-when-row">
            <label className="publish-when-option">
              <input type="radio" name="when" checked={when === "now"} onChange={() => setWhen("now")} disabled={saving} />
              Now
            </label>
            <label className="publish-when-option">
              <input type="radio" name="when" checked={when === "schedule"} onChange={() => setWhen("schedule")} disabled={saving} />
              Set date &amp; time
            </label>
            {when === "schedule" ? (
              <input type="datetime-local" value={scheduledAt} disabled={saving} onChange={(event) => setScheduledAt(event.target.value)} />
            ) : null}
          </div>

          {error ? <p className="customer-segment-error">{error}</p> : null}
        </div>
        <footer className="tz-dialog-actions">
          <AppButton type="button" variant="secondary" disabled={saving} onClick={onCancel}>Cancel</AppButton>
          <AppButton
            type="button"
            variant="secondary"
            disabled={saving || (!text.trim() && !mediaUrl) || selectedAccountIds.length === 0}
            onClick={submitDraft}
          >
            Save as Draft
          </AppButton>
          <AppButton type="submit" variant="primary" loading={saving} disabled={!canSave}>
            {when === "now" ? "Post now" : "Schedule"}
          </AppButton>
        </footer>
      </form>
    </div>
  );
}

export default function PublishPage() {
  const [searchParams, setSearchParams] = useSearchParams();
  const channelFilter = searchParams.get("channel");

  const [activeTab, setActiveTab] = useState("scheduled");
  const [posts, setPosts] = useState([]);
  const [channelAccounts, setChannelAccounts] = useState([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState("");

  const [dialogOpen, setDialogOpen] = useState(false);
  const [saving, setSaving] = useState(false);
  const [saveError, setSaveError] = useState("");
  const [toDelete, setToDelete] = useState(null);
  const [deleting, setDeleting] = useState(false);
  const [publishingId, setPublishingId] = useState(null);

  async function load() {
    setLoading(true);
    setError("");
    try {
      const result = await listScheduledPostsRequest({ status: activeTab });
      const items = Array.isArray(result?.items) ? result.items : [];
      setPosts(channelFilter ? items.filter((post) => post.channel_account_ids.includes(Number(channelFilter))) : items);
    } catch (requestError) {
      setError(requestError.message || "Posts could not be loaded.");
    } finally {
      setLoading(false);
    }
  }

  useEffect(() => { load(); }, [activeTab, channelFilter]);

  useEffect(() => {
    scheduledPostOptionsRequest()
      .then((result) => setChannelAccounts(Array.isArray(result?.channel_accounts) ? result.channel_accounts : []))
      .catch(() => {});
  }, []);

  useEffect(() => {
    if (searchParams.get("new") === "1") {
      setDialogOpen(true);
      const next = new URLSearchParams(searchParams);
      next.delete("new");
      setSearchParams(next, { replace: true });
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);


  async function savePost(values) {
    setSaving(true);
    setSaveError("");
    try {
      await createScheduledPostRequest(values);
      setDialogOpen(false);
      await load();
    } catch (requestError) {
      setSaveError(requestError.message || "Could not save this post.");
    } finally {
      setSaving(false);
    }
  }

  async function publishNow(post) {
    setPublishingId(post.id);
    try {
      await publishScheduledPostNowRequest(post.id);
      await load();
    } catch (requestError) {
      setError(requestError.message || "Could not publish this post.");
    } finally {
      setPublishingId(null);
    }
  }

  async function confirmDelete() {
    if (!toDelete) return;
    setDeleting(true);
    try {
      await deleteScheduledPostRequest(toDelete.id);
      setToDelete(null);
      await load();
    } catch (requestError) {
      setError(requestError.message || "Could not delete this post.");
    } finally {
      setDeleting(false);
    }
  }

  const filteredAccountName = channelFilter
    ? channelAccounts.find((account) => String(account.id) === channelFilter)?.name
    : null;

  return (
    <section className="publish-page">
      {filteredAccountName ? (
        <div className="publish-channel-filter-chip">
          Showing posts for <strong>{filteredAccountName}</strong>
          <button type="button" onClick={() => setSearchParams({})}>Clear</button>
        </div>
      ) : null}
      <div className="publish-header-row">
        <div className="publish-tabs">
          {TABS.map((tab) => (
            <button
              type="button"
              key={tab.key}
              className={`publish-tab ${activeTab === tab.key ? "is-active" : ""}`}
              onClick={() => setActiveTab(tab.key)}
            >
              {tab.label}
            </button>
          ))}
        </div>
        <AppButton variant="primary" icon={<AddOutlined fontSize="small" />} onClick={() => setDialogOpen(true)}>
          New Post
        </AppButton>
      </div>

      {error ? <ErrorState title="Could not load posts" description={error} action={<AppButton variant="primary" onClick={load}>Retry</AppButton>} /> : null}

      {loading ? (
        <LoadingState label="Loading posts…" />
      ) : posts.length === 0 ? (
        <AppCard padding="large" className="publish-empty">
          <p>No posts here yet.</p>
        </AppCard>
      ) : (
        <div className="publish-post-list">
          {posts.map((post) => (
            <AppCard key={post.id} padding="medium" className="publish-post-card">
              <div className="publish-post-avatars">
                {post.channel_account_ids.map((accountId) => {
                  const account = channelAccounts.find((item) => item.id === accountId);
                  const Icon = channelIcon(account?.channel);
                  return <Icon key={accountId} fontSize="small" title={account?.name} />;
                })}
              </div>
              <div className="publish-post-body">
                {post.text ? <p>{post.text}</p> : null}
                {post.media_urls?.length ? (
                  post.media_type === "video" ? (
                    <video src={post.media_urls[0]} controls className="publish-post-media" />
                  ) : (
                    <img src={post.media_urls[0]} alt="" className="publish-post-media" />
                  )
                ) : null}
              </div>
              <div className="publish-post-meta">
                <StatusBadge status={post.status} tone={STATUS_TONE[post.status]} label={post.status} />
                <span>{post.status === "scheduled" ? formatDateTime(post.scheduled_at) : formatDateTime(post.published_at || post.created_at)}</span>
              </div>
              <div className="publish-post-actions">
                {post.status !== "sent" ? (
                  <AppButton
                    variant="secondary"
                    size="small"
                    icon={<SendOutlined fontSize="small" />}
                    loading={publishingId === post.id}
                    onClick={() => publishNow(post)}
                  >
                    Post now
                  </AppButton>
                ) : null}
                <button type="button" className="publish-post-delete" onClick={() => setToDelete(post)}>
                  <DeleteOutlineOutlined fontSize="small" />
                </button>
              </div>
              {post.status === "failed" && post.results ? (
                <div className="publish-post-errors">
                  {Object.values(post.results).filter((result) => !result.ok).map((result, index) => (
                    <p key={index}>{result.error}</p>
                  ))}
                </div>
              ) : null}
            </AppCard>
          ))}
        </div>
      )}

      <CreatePostDialog
        open={dialogOpen}
        channelAccounts={channelAccounts}
        saving={saving}
        error={saveError}
        onCancel={() => setDialogOpen(false)}
        onSave={savePost}
      />
      <ConfirmDialog
        open={Boolean(toDelete)}
        title="Delete post"
        message="Delete this post? This can't be undone."
        confirmLabel="Delete"
        confirmVariant="danger"
        loading={deleting}
        onConfirm={confirmDelete}
        onCancel={() => setToDelete(null)}
      />
    </section>
  );
}
