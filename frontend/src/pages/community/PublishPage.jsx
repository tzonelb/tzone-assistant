import { useEffect, useState } from "react";
import {
  AddOutlined,
  ArticleOutlined,
  AutoAwesomeOutlined,
  CloseOutlined,
  DeleteOutlineOutlined,
  EventOutlined,
  ImageOutlined,
  InfoOutlined,
  InsertEmoticonOutlined,
  LocalOfferOutlined,
  OpenInFullOutlined,
  SendOutlined,
  TagOutlined,
  VisibilityOutlined,
} from "@mui/icons-material";
import { useSearchParams } from "react-router-dom";
import {
  createScheduledPostRequest,
  deleteScheduledPostRequest,
  listScheduledPostsRequest,
  publishScheduledPostNowRequest,
  scheduledPostOptionsRequest,
  uploadMediaRequest,
} from "../../api/client";
import { AppCard, ConfirmDialog, ErrorState, LoadingState, StatusBadge } from "../../components/common";
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
  const [customizeMode, setCustomizeMode] = useState(false);
  const [expandedAccountId, setExpandedAccountId] = useState(null);
  const [channelPickerOpen, setChannelPickerOpen] = useState(false);
  const [mediaUrl, setMediaUrl] = useState("");
  const [mediaType, setMediaType] = useState("");
  const [mediaFileName, setMediaFileName] = useState("");
  const [mediaUploading, setMediaUploading] = useState(false);
  const [mediaError, setMediaError] = useState("");
  const [when, setWhen] = useState("now");
  const [scheduledAt, setScheduledAt] = useState("");
  const [createAnother, setCreateAnother] = useState(false);
  const [fullscreen, setFullscreen] = useState(false);
  const [rightPanel, setRightPanel] = useState("preview");

  useEffect(() => {
    if (!open) {
      setText(""); setSelectedAccountIds([]); setContentOverrides({}); setChannelPostTypes({});
      setCustomizeMode(false); setExpandedAccountId(null); setChannelPickerOpen(false); setMediaUrl(""); setMediaType("");
      setMediaFileName(""); setMediaError(""); setWhen("now"); setScheduledAt(""); setCreateAnother(false);
      setFullscreen(false); setRightPanel("preview");
    }
  }, [open]);

  function resetContentOnly() {
    setText(""); setContentOverrides({}); setMediaUrl(""); setMediaType(""); setMediaFileName("");
    setWhen("now"); setScheduledAt("");
  }

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

  async function submit(event) {
    event.preventDefault();
    if (!canSave) return;
    const ok = await onSave(buildPayload({ asDraft: false }), { createAnother });
    if (ok && createAnother) resetContentOnly();
  }

  async function submitDraft() {
    if (!text.trim() && !mediaUrl) return;
    if (selectedAccountIds.length === 0) return;
    const ok = await onSave(buildPayload({ asDraft: true }), { createAnother });
    if (ok && createAnother) resetContentOnly();
  }

  const canSave = (text.trim() || mediaUrl) && selectedAccountIds.length > 0 && (when === "now" || scheduledAt);
  const selectedAccounts = channelAccounts.filter((account) => selectedAccountIds.includes(account.id));

  return (
    <div className="bp-dialog-backdrop" role="presentation" onMouseDown={(event) => { if (event.target === event.currentTarget && !saving) onCancel(); }}>
      <form className={`bp-dialog ${fullscreen ? "is-fullscreen" : ""}`} onSubmit={submit}>
        <header className="bp-header">
          <div className="bp-header-left">
            <h3>Create Post</h3>
            <button type="button" className="bp-tags-btn" disabled title="Post tagging isn't built yet"><LocalOfferOutlined fontSize="small" /> Tags</button>
          </div>
          <div className="bp-header-right">
            <button type="button" className={`bp-header-action ${rightPanel === "templates" ? "is-active" : ""}`} onClick={() => setRightPanel("templates")}>
              <ArticleOutlined fontSize="small" /> Templates
            </button>
            <button type="button" className={`bp-header-action ${rightPanel === "ai" ? "is-active" : ""}`} onClick={() => setRightPanel("ai")}>
              <AutoAwesomeOutlined fontSize="small" /> AI Assistant
            </button>
            <button type="button" className={`bp-header-action ${rightPanel === "preview" ? "is-active" : ""}`} onClick={() => setRightPanel("preview")}>
              <VisibilityOutlined fontSize="small" /> Preview
            </button>
            <button type="button" className="bp-icon-btn" onClick={() => setFullscreen((current) => !current)} title={fullscreen ? "Exit fullscreen" : "Fullscreen"}>
              <OpenInFullOutlined fontSize="small" />
            </button>
            <button type="button" className="bp-icon-btn" onClick={onCancel} disabled={saving}><CloseOutlined fontSize="small" /></button>
          </div>
        </header>

        <div className="bp-body">
          <div className="bp-content">
            <div className="bp-channel-row">
              {selectedAccounts.map((account) => {
                const Icon = channelIcon(account.channel);
                return (
                  <button
                    type="button"
                    key={account.id}
                    className="bp-channel-avatar"
                    data-channel={account.channel}
                    title={account.name}
                    onClick={() => { setCustomizeMode(true); setExpandedAccountId(account.id); }}
                  >
                    <span className="bp-channel-avatar-circle">{account.name.charAt(0).toUpperCase()}</span>
                    <Icon className="bp-channel-avatar-badge" />
                  </button>
                );
              })}
              <div className="bp-channel-picker-wrap">
                <button type="button" className="bp-channel-add-btn" onClick={() => setChannelPickerOpen((current) => !current)}>
                  <AddOutlined fontSize="small" />
                </button>
                {channelPickerOpen ? (
                  <div className="bp-channel-picker">
                    {channelAccounts.length === 0 ? (
                      <p className="publish-no-channels">No Facebook Page or Instagram account connected yet — connect one from Company Settings → Channels first.</p>
                    ) : (
                      channelAccounts.map((account) => {
                        const Icon = channelIcon(account.channel);
                        const selected = selectedAccountIds.includes(account.id);
                        return (
                          <label key={account.id} className="publish-channel-picker-row">
                            <input type="checkbox" checked={selected} onChange={() => toggleAccount(account.id)} />
                            <Icon fontSize="small" />
                            <span>{account.name}</span>
                          </label>
                        );
                      })
                    )}
                  </div>
                ) : null}
              </div>
            </div>

            {!customizeMode ? (
              <div className="bp-composer">
                <textarea
                  rows={8}
                  value={text}
                  disabled={saving}
                  onChange={(event) => setText(event.target.value)}
                  placeholder="Start writing or get inspired with Templates"
                />
                <div className="bp-dropzone">
                  {mediaUrl ? (
                    <div className="broadcast-media-attached">
                      <span>{mediaFileName || mediaUrl} <em>({mediaType})</em></span>
                      <button type="button" onClick={() => { setMediaUrl(""); setMediaType(""); setMediaFileName(""); }}>Remove</button>
                    </div>
                  ) : (
                    <label className="bp-dropzone-label">
                      <ImageOutlined />
                      <span>Drag &amp; drop or <em>select a file</em></span>
                      <input type="file" accept="image/*,video/*" hidden disabled={mediaUploading} onChange={handleMediaFileChange} />
                    </label>
                  )}
                  {mediaUploading ? <span className="broadcast-field-note">Uploading…</span> : null}
                  {mediaError ? <span className="broadcast-field-note broadcast-media-error">{mediaError}</span> : null}
                </div>
                <div className="bp-toolbar">
                  <button type="button" disabled title="Not built yet"><AddOutlined fontSize="small" /></button>
                  <button type="button" disabled title="Not built yet"><InsertEmoticonOutlined fontSize="small" /></button>
                  <button type="button" disabled title="Not built yet"><TagOutlined fontSize="small" /></button>
                </div>
              </div>
            ) : (
              <div className="publish-channel-panels">
                {selectedAccounts.map((account) => {
                  const Icon = channelIcon(account.channel);
                  const isExpanded = expandedAccountId === account.id;
                  const postType = channelPostTypes[account.id] || "feed";
                  const overrideText = contentOverrides[account.id] || "";
                  return (
                    <div key={account.id} className={`publish-channel-panel ${isExpanded ? "is-expanded" : ""}`}>
                      {isExpanded ? (
                        <button type="button" className="publish-channel-panel-head" onClick={() => setExpandedAccountId(null)}>
                          <Icon fontSize="small" />
                          <span>{account.name}</span>
                          <em>{POST_TYPE_LABELS[postType]}</em>
                        </button>
                      ) : (
                        <button type="button" className="publish-channel-panel-collapsed-row" onClick={() => setExpandedAccountId(account.id)}>
                          <Icon fontSize="small" />
                          <span>{overrideText || text || "What would you like to share?"}</span>
                        </button>
                      )}
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
                {mediaUrl ? (
                  <div className="broadcast-media-attached">
                    <span>{mediaFileName || mediaUrl} <em>({mediaType})</em></span>
                    <button type="button" onClick={() => { setMediaUrl(""); setMediaType(""); setMediaFileName(""); }}>Remove</button>
                  </div>
                ) : (
                  <label className="bp-dropzone-label bp-dropzone-label-compact">
                    <ImageOutlined fontSize="small" />
                    <span>Drag &amp; drop or <em>select a file</em></span>
                    <input type="file" accept="image/*,video/*" hidden disabled={mediaUploading} onChange={handleMediaFileChange} />
                  </label>
                )}
              </div>
            )}

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

          <aside className="bp-preview">
            {rightPanel === "templates" ? (
              <>
                <h4><ArticleOutlined fontSize="small" /> Templates</h4>
                <div className="bp-preview-empty">
                  <p>Reusable post templates aren't built yet.</p>
                  <span>This needs its own saved-template library, similar to Saved Replies — ask if you want it next.</span>
                </div>
              </>
            ) : rightPanel === "ai" ? (
              <>
                <h4><AutoAwesomeOutlined fontSize="small" /> AI Assistant</h4>
                <div className="bp-preview-empty">
                  <p>AI post-writing isn't wired up yet.</p>
                  <span>T-ZONE already has a real AI pipeline (used for AI Teaching/replies) — connecting it here to draft or repurpose post text is realistic, ask if you want it next.</span>
                </div>
              </>
            ) : (
              <>
                <h4>Post Previews <InfoIcon /></h4>
                <div className="bp-preview-empty">
                  <div className="bp-preview-card">
                    <span className="bp-preview-card-avatar" />
                    <span className="bp-preview-card-line" />
                  </div>
                  <p>See your post's preview here</p>
                </div>
              </>
            )}
          </aside>
        </div>

        <footer className="bp-footer">
          <label className="bp-create-another">
            <input type="checkbox" checked={createAnother} onChange={(event) => setCreateAnother(event.target.checked)} />
            Create Another
          </label>
          <div className="bp-footer-right">
            <button type="button" className="bp-next-available" disabled title="Not built yet — pick a date & time above instead"><EventOutlined fontSize="small" /> Next Available</button>
            {!customizeMode ? (
              <button
                type="button"
                className="btn btn-primary bp-cta"
                disabled={selectedAccountIds.length === 0}
                onClick={() => setCustomizeMode(true)}
              >
                Customize for each network →
              </button>
            ) : (
              <>
                <button
                  type="button"
                  className="btn btn-secondary"
                  disabled={saving || (!text.trim() && !mediaUrl) || selectedAccountIds.length === 0}
                  onClick={submitDraft}
                >
                  Save Drafts
                </button>
                <button type="submit" className="btn btn-primary bp-cta" disabled={saving || !canSave}>
                  {saving ? "Saving…" : (when === "now" ? "Post now" : "Schedule Posts")}
                </button>
              </>
            )}
          </div>
        </footer>
      </form>
    </div>
  );
}

function InfoIcon() {
  return <InfoOutlined fontSize="inherit" className="bp-info-icon" />;
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


  async function savePost(values, { createAnother } = {}) {
    setSaving(true);
    setSaveError("");
    try {
      await createScheduledPostRequest(values);
      if (!createAnother) setDialogOpen(false);
      await load();
      return true;
    } catch (requestError) {
      setSaveError(requestError.message || "Could not save this post.");
      return false;
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
        <button type="button" className="btn btn-primary" onClick={() => setDialogOpen(true)}>
          <AddOutlined fontSize="small" /> New Post
        </button>
      </div>

      {error ? <ErrorState title="Could not load posts" description={error} action={<button type="button" className="btn btn-primary" onClick={load}>Retry</button>} /> : null}

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
                  <button
                    type="button"
                    className="btn btn-secondary"
                    disabled={publishingId === post.id}
                    onClick={() => publishNow(post)}
                  >
                    <SendOutlined fontSize="small" /> {publishingId === post.id ? "Posting…" : "Post now"}
                  </button>
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
