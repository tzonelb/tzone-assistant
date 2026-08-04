import { useCallback, useEffect, useRef, useState } from "react";
import {
  AddOutlined,
  CheckCircleOutlined,
  CloseOutlined,
  DeleteOutlineOutlined,
  EditOutlined,
  LockOutlined,
  PublishOutlined,
  RefreshOutlined,
  ScheduleSendOutlined,
  UndoOutlined,
} from "@mui/icons-material";

import {
  changeScheduledPostStatusRequest,
  createScheduledPostRequest,
  deleteScheduledPostRequest,
  getScheduledPostsRequest,
  updateScheduledPostRequest,
} from "../../api/client";
import {
  AppButton,
  AppCard,
  AppTable,
  ConfirmDialog,
  EmptyState,
  ErrorState,
  PageHeader,
  SearchBar,
  StatusBadge,
} from "../../components/common";
import { useAuth } from "../../contexts/AuthContext";
import "./SchedulerPage.css";

const PAGE_SIZE = 20;

const STATUS_OPTIONS = [
  { value: "draft", label: "Draft" },
  { value: "scheduled", label: "Scheduled" },
  { value: "published", label: "Published" },
  { value: "cancelled", label: "Cancelled" },
];

const CHANNEL_OPTIONS = [
  { value: "facebook", label: "Facebook" },
  { value: "instagram", label: "Instagram" },
  { value: "whatsapp_status", label: "WhatsApp Status" },
  { value: "telegram", label: "Telegram" },
];

const STATUS_TONE = {
  draft: "neutral",
  scheduled: "info",
  published: "success",
  cancelled: "danger",
};

const STATUS_LABEL = Object.fromEntries(
  STATUS_OPTIONS.map((option) => [option.value, option.label]),
);
const CHANNEL_LABEL = Object.fromEntries(
  CHANNEL_OPTIONS.map((option) => [option.value, option.label]),
);

const EMPTY_FORM = {
  title: "",
  content: "",
  channel: "facebook",
  media_url: "",
  scheduled_at: "",
};

function toDatetimeLocalValue(value) {
  if (!value) return "";
  const date = new Date(value);
  if (Number.isNaN(date.getTime())) return "";
  const pad = (n) => String(n).padStart(2, "0");
  return `${date.getFullYear()}-${pad(date.getMonth() + 1)}-${pad(date.getDate())}T${pad(date.getHours())}:${pad(date.getMinutes())}`;
}

function formatDateTime(value) {
  if (!value) return "—";
  const date = new Date(value);
  if (Number.isNaN(date.getTime())) return value;
  return date.toLocaleString(undefined, {
    year: "numeric",
    month: "short",
    day: "numeric",
    hour: "2-digit",
    minute: "2-digit",
  });
}

function postToForm(post) {
  return {
    title: post?.title || "",
    content: post?.content || "",
    channel: post?.channel || "facebook",
    media_url: post?.media_url || "",
    scheduled_at: toDatetimeLocalValue(post?.scheduled_at),
  };
}

export default function SchedulerPage() {
  const { hasPermission } = useAuth();
  const canView = hasPermission("scheduler.view");
  const canManage = hasPermission("scheduler.manage");

  const [statusFilter, setStatusFilter] = useState("all");
  const [channelFilter, setChannelFilter] = useState("all");
  const [searchInput, setSearchInput] = useState("");
  const [search, setSearch] = useState("");
  const [page, setPage] = useState(1);

  const [rows, setRows] = useState([]);
  const [total, setTotal] = useState(0);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState("");

  const [editorOpen, setEditorOpen] = useState(false);
  const [isEdit, setIsEdit] = useState(false);
  const [activePostId, setActivePostId] = useState(null);
  const [form, setForm] = useState(EMPTY_FORM);
  const [baseUpdatedAt, setBaseUpdatedAt] = useState(null);
  const [saving, setSaving] = useState(false);
  const [formError, setFormError] = useState("");

  const [deleteTarget, setDeleteTarget] = useState(null);
  const [deleting, setDeleting] = useState(false);
  const [busyPostId, setBusyPostId] = useState(null);

  const requestSeq = useRef(0);

  const load = useCallback(async () => {
    if (!canView) {
      setLoading(false);
      return;
    }
    const seq = ++requestSeq.current;
    setLoading(true);
    setError("");
    try {
      const result = await getScheduledPostsRequest({
        status: statusFilter,
        channel: channelFilter,
        search,
        limit: PAGE_SIZE,
        offset: (page - 1) * PAGE_SIZE,
      });
      if (seq !== requestSeq.current) return;
      setRows(result?.items || []);
      setTotal(result?.total || 0);
    } catch (requestError) {
      if (seq !== requestSeq.current) return;
      setError(requestError.message || "Scheduled posts could not be loaded.");
      setRows([]);
      setTotal(0);
    } finally {
      if (seq === requestSeq.current) setLoading(false);
    }
  }, [canView, statusFilter, channelFilter, search, page]);

  useEffect(() => {
    load();
  }, [load]);

  useEffect(() => {
    const handle = setTimeout(() => {
      setSearch(searchInput.trim());
      setPage(1);
    }, 350);
    return () => clearTimeout(handle);
  }, [searchInput]);

  useEffect(() => {
    setPage(1);
  }, [statusFilter, channelFilter]);

  function openCreate() {
    setForm(EMPTY_FORM);
    setBaseUpdatedAt(null);
    setActivePostId(null);
    setIsEdit(false);
    setFormError("");
    setEditorOpen(true);
  }

  function openEdit(post) {
    setForm(postToForm(post));
    setBaseUpdatedAt(post?.updated_at ?? null);
    setActivePostId(post.id);
    setIsEdit(true);
    setFormError("");
    setEditorOpen(true);
  }

  function closeEditor() {
    if (saving) return;
    setEditorOpen(false);
  }

  function updateForm(key, value) {
    setForm((current) => ({ ...current, [key]: value }));
  }

  async function handleSave() {
    const title = form.title.trim();
    const content = form.content.trim();
    if (!title) {
      setFormError("A title is required.");
      return;
    }
    if (!content) {
      setFormError("Post content is required.");
      return;
    }

    const payload = {
      title,
      content,
      channel: form.channel,
      media_url: form.media_url.trim() || null,
      scheduled_at: form.scheduled_at
        ? new Date(form.scheduled_at).toISOString()
        : null,
    };

    setSaving(true);
    setFormError("");
    try {
      if (isEdit) {
        await updateScheduledPostRequest(activePostId, {
          ...payload,
          expected_updated_at: baseUpdatedAt,
        });
      } else {
        await createScheduledPostRequest(payload);
      }
      setEditorOpen(false);
      await load();
    } catch (err) {
      if (err?.status === 409) {
        const current = err?.data?.detail?.current;
        if (current) {
          setForm(postToForm(current));
          setBaseUpdatedAt(current?.updated_at ?? null);
        }
        setFormError(
          err?.data?.detail?.message ||
            "This post was changed elsewhere. It has been reloaded — review and save again.",
        );
      } else {
        setFormError(err.message || "The post could not be saved.");
      }
    } finally {
      setSaving(false);
    }
  }

  async function handleStatusChange(post, newStatus) {
    setBusyPostId(post.id);
    setError("");
    try {
      await changeScheduledPostStatusRequest(post.id, {
        status: newStatus,
        expected_updated_at: post.updated_at,
      });
      await load();
    } catch (err) {
      const detail = err?.data?.detail;
      setError(
        (typeof detail === "string" ? detail : detail?.message) ||
          err.message ||
          "The post status could not be changed.",
      );
    } finally {
      setBusyPostId(null);
    }
  }

  async function handleDelete() {
    if (!deleteTarget) return;
    setDeleting(true);
    try {
      await deleteScheduledPostRequest(deleteTarget.id);
      setDeleteTarget(null);
      await load();
    } catch (err) {
      setError(err.message || "The post could not be deleted.");
      setDeleteTarget(null);
    } finally {
      setDeleting(false);
    }
  }

  function renderStatusActions(row) {
    if (!canManage) return null;
    const busy = busyPostId === row.id;
    return (
      <div className="scheduler-row-actions">
        {row.status === "draft" ? (
          <AppButton
            size="small"
            variant="secondary"
            icon={<CheckCircleOutlined fontSize="small" />}
            loading={busy}
            onClick={() => handleStatusChange(row, "scheduled")}
          >
            Approve
          </AppButton>
        ) : null}
        {row.status === "scheduled" ? (
          <>
            <AppButton
              size="small"
              variant="secondary"
              icon={<PublishOutlined fontSize="small" />}
              loading={busy}
              onClick={() => handleStatusChange(row, "published")}
            >
              Mark published
            </AppButton>
            <AppButton
              size="small"
              variant="secondary"
              icon={<UndoOutlined fontSize="small" />}
              loading={busy}
              onClick={() => handleStatusChange(row, "draft")}
            >
              Back to draft
            </AppButton>
          </>
        ) : null}
        {row.status === "draft" || row.status === "scheduled" ? (
          <>
            <AppButton
              size="small"
              variant="secondary"
              icon={<EditOutlined fontSize="small" />}
              onClick={() => openEdit(row)}
            >
              Edit
            </AppButton>
            <AppButton
              size="small"
              variant="danger"
              icon={<CloseOutlined fontSize="small" />}
              loading={busy}
              onClick={() => handleStatusChange(row, "cancelled")}
            >
              Cancel post
            </AppButton>
          </>
        ) : null}
        <AppButton
          size="small"
          variant="danger"
          icon={<DeleteOutlineOutlined fontSize="small" />}
          onClick={() => setDeleteTarget(row)}
        >
          Delete
        </AppButton>
      </div>
    );
  }

  function buildColumns() {
    const cols = [
      {
        key: "title",
        label: "Post",
        render: (_value, row) => (
          <div className="scheduler-title-cell">
            <strong>{row.title}</strong>
            <span className="scheduler-content-preview">{row.content}</span>
          </div>
        ),
      },
      {
        key: "channel",
        label: "Channel",
        render: (value) => CHANNEL_LABEL[value] || value,
      },
      {
        key: "scheduled_at",
        label: "Scheduled for",
        render: (value) => formatDateTime(value),
      },
      {
        key: "status",
        label: "Status",
        render: (value, row) => (
          <div className="scheduler-status-cell">
            <StatusBadge status={value} tone={STATUS_TONE[value]} label={STATUS_LABEL[value] || value} />
            {value === "published" && row.published_at ? (
              <span className="scheduler-published-at">at {formatDateTime(row.published_at)}</span>
            ) : null}
          </div>
        ),
      },
    ];

    if (canManage) {
      cols.push({
        key: "actions",
        label: "",
        align: "right",
        render: (_value, row) => renderStatusActions(row),
      });
    }

    return cols;
  }

  if (!canView) {
    return (
      <section className="scheduler-page">
        <PageHeader
          eyebrow="SCHEDULER"
          title="Scheduler"
          description="Create, approve and schedule social posts from one place."
        />
        <AppCard padding="large">
          <EmptyState
            icon={<LockOutlined />}
            title="You don't have access to the Scheduler"
            description="Ask a company administrator to grant you the “View Scheduler” permission."
          />
        </AppCard>
      </section>
    );
  }

  const columns = buildColumns();

  return (
    <section className="scheduler-page">
      <PageHeader
        eyebrow="SCHEDULER"
        title="Scheduler"
        description="Draft, approve and track social posts across your channels. Publishing to the platform itself is confirmed manually."
        actions={
          <div className="scheduler-row-actions">
            <AppButton
              variant="secondary"
              icon={<RefreshOutlined fontSize="small" />}
              onClick={load}
            >
              Refresh
            </AppButton>
            {canManage ? (
              <AppButton
                variant="primary"
                icon={<AddOutlined fontSize="small" />}
                onClick={openCreate}
              >
                New post
              </AppButton>
            ) : null}
          </div>
        }
      />

      {!canManage ? (
        <p className="scheduler-inline-note">
          <LockOutlined fontSize="small" /> You have read-only access. Ask an
          administrator for the &quot;Manage Scheduler&quot; permission to
          create, approve or publish posts.
        </p>
      ) : null}

      <AppCard padding="medium">
        <div className="scheduler-toolbar">
          <SearchBar
            value={searchInput}
            placeholder="Search posts by title or content..."
            ariaLabel="Search scheduled posts"
            onChange={setSearchInput}
            onClear={() => setSearchInput("")}
          />

          <label className="scheduler-filter">
            <span>Status</span>
            <select value={statusFilter} onChange={(event) => setStatusFilter(event.target.value)}>
              <option value="all">All statuses</option>
              {STATUS_OPTIONS.map((option) => (
                <option key={option.value} value={option.value}>
                  {option.label}
                </option>
              ))}
            </select>
          </label>

          <label className="scheduler-filter">
            <span>Channel</span>
            <select value={channelFilter} onChange={(event) => setChannelFilter(event.target.value)}>
              <option value="all">All channels</option>
              {CHANNEL_OPTIONS.map((option) => (
                <option key={option.value} value={option.value}>
                  {option.label}
                </option>
              ))}
            </select>
          </label>

          <StatusBadge
            status="info"
            tone="info"
            showDot={false}
            label={`${total} post${total === 1 ? "" : "s"}`}
          />
        </div>

        {error ? (
          <ErrorState
            title="Something went wrong"
            description={error}
            action={
              <AppButton
                variant="primary"
                icon={<RefreshOutlined fontSize="small" />}
                onClick={load}
              >
                Try again
              </AppButton>
            }
          />
        ) : (
          <AppTable
            columns={columns}
            rows={rows}
            loading={loading}
            rowKey="id"
            page={page}
            pageSize={PAGE_SIZE}
            totalRows={total}
            onPageChange={setPage}
            emptyTitle="No posts found"
            emptyDescription={
              search || statusFilter !== "all" || channelFilter !== "all"
                ? "No posts match your filters. Try widening your search."
                : canManage
                  ? "Draft your first post to start planning your social calendar."
                  : "Posts drafted by the team will appear here."
            }
            renderMobileCard={(row) => (
              <div className="tz-mobile-record-fields">
                <div className="scheduler-title-cell">
                  <strong>{row.title}</strong>
                  <span className="scheduler-content-preview">{row.content}</span>
                </div>
                <span>{CHANNEL_LABEL[row.channel] || row.channel} · {formatDateTime(row.scheduled_at)}</span>
                <StatusBadge status={row.status} tone={STATUS_TONE[row.status]} label={STATUS_LABEL[row.status] || row.status} />
                {renderStatusActions(row)}
              </div>
            )}
          />
        )}
      </AppCard>

      {editorOpen ? (
        <div
          className="tz-dialog-backdrop"
          role="presentation"
          onMouseDown={(event) => {
            if (event.target === event.currentTarget) closeEditor();
          }}
        >
          <section
            className="tz-dialog scheduler-dialog"
            role="dialog"
            aria-modal="true"
            aria-labelledby="scheduler-editor-title"
          >
            <header className="tz-dialog-header">
              <h3 id="scheduler-editor-title">
                <ScheduleSendOutlined fontSize="small" /> {isEdit ? "Edit post" : "New post"}
              </h3>
              <button
                type="button"
                className="tz-dialog-close"
                aria-label="Close editor"
                onClick={closeEditor}
              >
                <CloseOutlined fontSize="small" />
              </button>
            </header>

            <div className="tz-dialog-body">
              <div className="scheduler-form">
                <label className="scheduler-field">
                  <span>Title</span>
                  <input
                    type="text"
                    value={form.title}
                    disabled={saving}
                    placeholder="Internal name for this post"
                    onChange={(event) => updateForm("title", event.target.value)}
                  />
                </label>

                <label className="scheduler-field">
                  <span>Content</span>
                  <textarea
                    value={form.content}
                    disabled={saving}
                    placeholder="The post text that will go out"
                    onChange={(event) => updateForm("content", event.target.value)}
                  />
                </label>

                <div className="scheduler-grid-2">
                  <label className="scheduler-field">
                    <span>Channel</span>
                    <select
                      value={form.channel}
                      disabled={saving}
                      onChange={(event) => updateForm("channel", event.target.value)}
                    >
                      {CHANNEL_OPTIONS.map((option) => (
                        <option key={option.value} value={option.value}>
                          {option.label}
                        </option>
                      ))}
                    </select>
                  </label>

                  <label className="scheduler-field">
                    <span>Scheduled for</span>
                    <input
                      type="datetime-local"
                      value={form.scheduled_at}
                      disabled={saving}
                      onChange={(event) => updateForm("scheduled_at", event.target.value)}
                    />
                  </label>
                </div>

                <label className="scheduler-field">
                  <span>Media URL (optional)</span>
                  <input
                    type="url"
                    value={form.media_url}
                    disabled={saving}
                    placeholder="https://... image or video link"
                    onChange={(event) => updateForm("media_url", event.target.value)}
                  />
                </label>

                {formError ? <p className="scheduler-form-error">{formError}</p> : null}
              </div>
            </div>

            <footer className="tz-dialog-actions">
              <AppButton variant="secondary" disabled={saving} onClick={closeEditor}>
                Cancel
              </AppButton>
              <AppButton variant="primary" loading={saving} onClick={handleSave}>
                {isEdit ? "Save changes" : "Create draft"}
              </AppButton>
            </footer>
          </section>
        </div>
      ) : null}

      <ConfirmDialog
        open={Boolean(deleteTarget)}
        title="Delete post"
        confirmLabel="Delete"
        cancelLabel="Cancel"
        confirmVariant="danger"
        loading={deleting}
        onConfirm={handleDelete}
        onCancel={() => (deleting ? null : setDeleteTarget(null))}
        message={
          deleteTarget ? (
            <p>
              Delete <strong>{deleteTarget.title}</strong>? This cannot be undone.
            </p>
          ) : null
        }
      />
    </section>
  );
}
