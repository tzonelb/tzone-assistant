import { useCallback, useEffect, useMemo, useState } from "react";
import {
  AddOutlined,
  BoltOutlined,
  CloseOutlined,
  DeleteOutlineOutlined,
  EditOutlined,
  HistoryOutlined,
  LockOutlined,
  RefreshOutlined,
} from "@mui/icons-material";

import {
  createTriggerRequest,
  deleteTriggerRequest,
  getTriggerFiringsRequest,
  getTriggersRequest,
  getTriggerTypesRequest,
  updateTriggerRequest,
} from "../../api/client";
import {
  AppButton,
  AppCard,
  AppTable,
  ConfirmDialog,
  EmptyState,
  ErrorState,
  PageHeader,
  StatusBadge,
} from "../../components/common";
import { useAuth } from "../../contexts/AuthContext";
import "./TriggersPage.css";

const CHANNEL_OPTIONS = [
  { value: "", label: "All channels" },
  { value: "messenger", label: "Messenger" },
  { value: "instagram", label: "Instagram" },
  { value: "whatsapp", label: "WhatsApp" },
  { value: "telegram", label: "Telegram" },
  { value: "website_chat", label: "Website chat" },
];

const EMPTY_FORM = {
  name: "",
  trigger_type: "new_conversation",
  enabled: true,
  delay_minutes: "",
  channel: "",
  message_text: "",
  notify_team: true,
};

function formatDateTime(value) {
  if (!value) return "—";
  const date = new Date(value);
  if (Number.isNaN(date.getTime())) return value;
  return date.toLocaleString(undefined, {
    month: "short",
    day: "numeric",
    hour: "2-digit",
    minute: "2-digit",
  });
}

function triggerToForm(trigger) {
  return {
    name: trigger?.name || "",
    trigger_type: trigger?.trigger_type || "new_conversation",
    enabled: Boolean(trigger?.enabled),
    delay_minutes:
      trigger?.delay_minutes !== null && trigger?.delay_minutes !== undefined
        ? String(trigger.delay_minutes)
        : "",
    channel: trigger?.channel || "",
    message_text: trigger?.message_text || "",
    notify_team: Boolean(trigger?.notify_team),
  };
}

export default function TriggersPage() {
  const { hasPermission } = useAuth();
  const canView = hasPermission("triggers.view");
  const canManage = hasPermission("triggers.manage");

  const [triggers, setTriggers] = useState([]);
  const [types, setTypes] = useState([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState("");

  const [showFirings, setShowFirings] = useState(false);
  const [firings, setFirings] = useState([]);
  const [firingsTotal, setFiringsTotal] = useState(0);
  const [firingsPage, setFiringsPage] = useState(1);
  const [firingsLoading, setFiringsLoading] = useState(false);

  const [editorOpen, setEditorOpen] = useState(false);
  const [isEdit, setIsEdit] = useState(false);
  const [activeTriggerId, setActiveTriggerId] = useState(null);
  const [form, setForm] = useState(EMPTY_FORM);
  const [saving, setSaving] = useState(false);
  const [formError, setFormError] = useState("");

  const [deleteTarget, setDeleteTarget] = useState(null);
  const [deleting, setDeleting] = useState(false);
  const [busyTriggerId, setBusyTriggerId] = useState(null);

  const typeMeta = useMemo(
    () => Object.fromEntries(types.map((t) => [t.type, t])),
    [types],
  );

  const load = useCallback(async () => {
    if (!canView) {
      setLoading(false);
      return;
    }
    setLoading(true);
    setError("");
    try {
      const [triggersResult, typesResult] = await Promise.all([
        getTriggersRequest(),
        getTriggerTypesRequest(),
      ]);
      setTriggers(Array.isArray(triggersResult?.items) ? triggersResult.items : []);
      setTypes(Array.isArray(typesResult?.items) ? typesResult.items : []);
    } catch (requestError) {
      setError(requestError.message || "Triggers could not be loaded.");
    } finally {
      setLoading(false);
    }
  }, [canView]);

  useEffect(() => {
    load();
  }, [load]);

  const loadFirings = useCallback(async () => {
    if (!canView || !showFirings) return;
    setFiringsLoading(true);
    try {
      const result = await getTriggerFiringsRequest({
        limit: 20,
        offset: (firingsPage - 1) * 20,
      });
      setFirings(Array.isArray(result?.items) ? result.items : []);
      setFiringsTotal(result?.total || 0);
    } catch {
      setFirings([]);
      setFiringsTotal(0);
    } finally {
      setFiringsLoading(false);
    }
  }, [canView, showFirings, firingsPage]);

  useEffect(() => {
    loadFirings();
  }, [loadFirings]);

  function openCreate() {
    setForm(EMPTY_FORM);
    setActiveTriggerId(null);
    setIsEdit(false);
    setFormError("");
    setEditorOpen(true);
  }

  function openEdit(trigger) {
    setForm(triggerToForm(trigger));
    setActiveTriggerId(trigger.id);
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

  const selectedTypeMeta = typeMeta[form.trigger_type];

  async function handleSave() {
    const name = form.name.trim();
    if (!name) {
      setFormError("A name is required.");
      return;
    }
    if (selectedTypeMeta?.needs_delay && !form.delay_minutes) {
      setFormError("This trigger type needs a delay (in minutes).");
      return;
    }

    const payload = {
      name,
      trigger_type: form.trigger_type,
      enabled: form.enabled,
      delay_minutes: form.delay_minutes ? Number(form.delay_minutes) : null,
      channel: form.channel || null,
      message_text: form.message_text.trim() || null,
      notify_team: form.notify_team,
    };

    setSaving(true);
    setFormError("");
    try {
      if (isEdit) {
        await updateTriggerRequest(activeTriggerId, payload);
      } else {
        await createTriggerRequest(payload);
      }
      setEditorOpen(false);
      await load();
    } catch (err) {
      setFormError(
        (typeof err?.data?.detail === "string" ? err.data.detail : null) ||
          err.message ||
          "The trigger could not be saved.",
      );
    } finally {
      setSaving(false);
    }
  }

  async function handleToggle(trigger) {
    setBusyTriggerId(trigger.id);
    try {
      await updateTriggerRequest(trigger.id, { enabled: !trigger.enabled });
      await load();
    } catch (err) {
      setError(err.message || "The trigger could not be updated.");
    } finally {
      setBusyTriggerId(null);
    }
  }

  async function handleDelete() {
    if (!deleteTarget) return;
    setDeleting(true);
    try {
      await deleteTriggerRequest(deleteTarget.id);
      setDeleteTarget(null);
      await load();
    } catch (err) {
      setError(err.message || "The trigger could not be deleted.");
      setDeleteTarget(null);
    } finally {
      setDeleting(false);
    }
  }

  function buildColumns() {
    const cols = [
      {
        key: "name",
        label: "Trigger",
        render: (_value, row) => (
          <div className="triggers-name-cell">
            <strong>{row.name}</strong>
            <span>{typeMeta[row.trigger_type]?.label || row.trigger_type}</span>
          </div>
        ),
      },
      {
        key: "trigger_type",
        label: "Kind",
        render: (value) => (
          <StatusBadge
            status={value}
            tone={typeMeta[value]?.kind === "time" ? "warning" : "info"}
            showDot={false}
            label={
              typeMeta[value]?.kind === "time"
                ? `Time-based${" "}`
                : "Event-based"
            }
          />
        ),
      },
      {
        key: "delay_minutes",
        label: "Delay",
        render: (value) => (value ? `${value} min` : "—"),
      },
      {
        key: "channel",
        label: "Channel",
        render: (value) =>
          CHANNEL_OPTIONS.find((option) => option.value === (value || ""))?.label ||
          value ||
          "All channels",
      },
      {
        key: "message_text",
        label: "Auto-message",
        render: (value) =>
          value ? (
            <span className="triggers-message-preview">{value}</span>
          ) : (
            "—"
          ),
      },
      {
        key: "enabled",
        label: "Status",
        render: (value) => (
          <StatusBadge
            status={value ? "active" : "off"}
            tone={value ? "success" : "neutral"}
            label={value ? "Active" : "Off"}
          />
        ),
      },
      {
        key: "firing_count",
        label: "Fired",
        render: (value) => value ?? 0,
      },
    ];

    if (canManage) {
      cols.push({
        key: "actions",
        label: "",
        align: "right",
        render: (_value, row) => (
          <div className="triggers-row-actions">
            <AppButton
              size="small"
              variant="secondary"
              loading={busyTriggerId === row.id}
              onClick={() => handleToggle(row)}
            >
              {row.enabled ? "Turn off" : "Turn on"}
            </AppButton>
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
              icon={<DeleteOutlineOutlined fontSize="small" />}
              onClick={() => setDeleteTarget(row)}
            >
              Delete
            </AppButton>
          </div>
        ),
      });
    }

    return cols;
  }

  if (!canView) {
    return (
      <section className="triggers-page">
        <PageHeader
          eyebrow="BOT TRIGGERS"
          title="Bot Triggers"
          description="Rules that make the bot act automatically on new conversations, silent customers, appointments and more."
        />
        <AppCard padding="large">
          <EmptyState
            icon={<LockOutlined />}
            title="You don't have access to Bot Triggers"
            description="Ask a company administrator to grant you the “View Bot Triggers” permission."
          />
        </AppCard>
      </section>
    );
  }

  const columns = buildColumns();

  return (
    <section className="triggers-page">
      <PageHeader
        eyebrow="BOT TRIGGERS"
        title="Bot Triggers"
        description="Decide what wakes the bot up: a new conversation, a customer going silent, an unanswered chat, an appointment being booked or coming up, a completed task or a logged call."
        actions={
          <div className="triggers-row-actions">
            <AppButton
              variant="secondary"
              icon={<HistoryOutlined fontSize="small" />}
              onClick={() => {
                setShowFirings((current) => !current);
                setFiringsPage(1);
              }}
            >
              {showFirings ? "Hide history" : "Firing history"}
            </AppButton>
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
                New trigger
              </AppButton>
            ) : null}
          </div>
        }
      />

      {!canManage ? (
        <p className="triggers-inline-note">
          <LockOutlined fontSize="small" /> You have read-only access. Ask an
          administrator for the &quot;Manage Bot Triggers&quot; permission to
          create, edit or delete triggers.
        </p>
      ) : null}

      <AppCard padding="medium">
        {error ? (
          <ErrorState
            title="Triggers could not load"
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
            rows={triggers}
            loading={loading}
            rowKey="id"
            emptyTitle="No triggers yet"
            emptyDescription={
              canManage
                ? "Create your first trigger to make the bot act automatically — e.g. greet every new conversation, or follow up when a customer goes silent for an hour."
                : "Triggers configured by your team will appear here."
            }
            renderMobileCard={(row) => (
              <div className="tz-mobile-record-fields">
                <div className="triggers-name-cell">
                  <strong>{row.name}</strong>
                  <span>{typeMeta[row.trigger_type]?.label || row.trigger_type}</span>
                </div>
                <div className="triggers-mobile-meta">
                  <StatusBadge
                    status={row.enabled ? "active" : "off"}
                    tone={row.enabled ? "success" : "neutral"}
                    label={row.enabled ? "Active" : "Off"}
                  />
                  {row.delay_minutes ? <span>{row.delay_minutes} min</span> : null}
                  <span>Fired {row.firing_count ?? 0}×</span>
                </div>
                {canManage ? (
                  <div className="triggers-row-actions">
                    <AppButton size="small" variant="secondary" onClick={() => handleToggle(row)} loading={busyTriggerId === row.id}>
                      {row.enabled ? "Turn off" : "Turn on"}
                    </AppButton>
                    <AppButton size="small" variant="secondary" onClick={() => openEdit(row)}>
                      Edit
                    </AppButton>
                    <AppButton size="small" variant="danger" onClick={() => setDeleteTarget(row)}>
                      Delete
                    </AppButton>
                  </div>
                ) : null}
              </div>
            )}
          />
        )}
      </AppCard>

      {showFirings ? (
        <AppCard padding="medium">
          <h4 className="triggers-firings-heading">
            <HistoryOutlined fontSize="small" /> Firing history
          </h4>
          <AppTable
            columns={[
              { key: "fired_at", label: "When", render: (value) => formatDateTime(value) },
              { key: "trigger_name", label: "Trigger", render: (value) => value || "(deleted)" },
              {
                key: "action_taken",
                label: "Action",
                render: (value) => (
                  <StatusBadge
                    status={value}
                    tone={
                      value === "message_sent"
                        ? "success"
                        : value === "send_failed"
                          ? "danger"
                          : "info"
                    }
                    showDot={false}
                    label={
                      value === "message_sent"
                        ? "Message sent"
                        : value === "send_failed"
                          ? "Send failed"
                          : "Team notified"
                    }
                  />
                ),
              },
              {
                key: "detail",
                label: "Detail",
                render: (value) => (
                  <span className="triggers-message-preview">{value || "—"}</span>
                ),
              },
            ]}
            rows={firings}
            loading={firingsLoading}
            rowKey="id"
            page={firingsPage}
            pageSize={20}
            totalRows={firingsTotal}
            onPageChange={setFiringsPage}
            emptyTitle="No firings yet"
            emptyDescription="When a trigger fires, it shows up here with what it did."
          />
        </AppCard>
      ) : null}

      {editorOpen ? (
        <div
          className="tz-dialog-backdrop"
          role="presentation"
          onMouseDown={(event) => {
            if (event.target === event.currentTarget) closeEditor();
          }}
        >
          <section
            className="tz-dialog triggers-dialog"
            role="dialog"
            aria-modal="true"
            aria-labelledby="triggers-editor-title"
          >
            <header className="tz-dialog-header">
              <h3 id="triggers-editor-title">
                <BoltOutlined fontSize="small" /> {isEdit ? "Edit trigger" : "New trigger"}
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
              <div className="triggers-form">
                <label className="triggers-field">
                  <span>Name</span>
                  <input
                    type="text"
                    value={form.name}
                    disabled={saving}
                    placeholder="e.g. Welcome every new customer"
                    onChange={(event) => updateForm("name", event.target.value)}
                  />
                </label>

                <label className="triggers-field">
                  <span>When should it fire?</span>
                  <select
                    value={form.trigger_type}
                    disabled={saving}
                    onChange={(event) => updateForm("trigger_type", event.target.value)}
                  >
                    {types.map((type) => (
                      <option key={type.type} value={type.type}>
                        {type.label}
                      </option>
                    ))}
                  </select>
                </label>

                <div className="triggers-grid-2">
                  {selectedTypeMeta?.needs_delay ? (
                    <label className="triggers-field">
                      <span>Delay (minutes)</span>
                      <input
                        type="number"
                        min="1"
                        step="1"
                        value={form.delay_minutes}
                        disabled={saving}
                        placeholder="e.g. 60"
                        onChange={(event) => updateForm("delay_minutes", event.target.value)}
                      />
                    </label>
                  ) : null}

                  <label className="triggers-field">
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
                </div>

                <label className="triggers-field">
                  <span>Auto-message to the customer (optional)</span>
                  <textarea
                    value={form.message_text}
                    disabled={saving}
                    placeholder="If set (and the trigger has a customer conversation on Messenger/Instagram/WhatsApp), the bot sends this message automatically."
                    onChange={(event) => updateForm("message_text", event.target.value)}
                  />
                </label>

                <div className="triggers-checks">
                  <label className="triggers-check">
                    <input
                      type="checkbox"
                      checked={form.notify_team}
                      disabled={saving}
                      onChange={(event) => updateForm("notify_team", event.target.checked)}
                    />
                    <span>Notify the team when it fires</span>
                  </label>

                  <label className="triggers-check">
                    <input
                      type="checkbox"
                      checked={form.enabled}
                      disabled={saving}
                      onChange={(event) => updateForm("enabled", event.target.checked)}
                    />
                    <span>Active</span>
                  </label>
                </div>

                {formError ? <p className="triggers-form-error">{formError}</p> : null}
              </div>
            </div>

            <footer className="tz-dialog-actions">
              <AppButton variant="secondary" disabled={saving} onClick={closeEditor}>
                Cancel
              </AppButton>
              <AppButton variant="primary" loading={saving} onClick={handleSave}>
                {isEdit ? "Save changes" : "Create trigger"}
              </AppButton>
            </footer>
          </section>
        </div>
      ) : null}

      <ConfirmDialog
        open={Boolean(deleteTarget)}
        title="Delete trigger"
        confirmLabel="Delete"
        cancelLabel="Cancel"
        confirmVariant="danger"
        loading={deleting}
        onConfirm={handleDelete}
        onCancel={() => (deleting ? null : setDeleteTarget(null))}
        message={
          deleteTarget ? (
            <p>
              Delete <strong>{deleteTarget.name}</strong> and its firing history?
              This cannot be undone.
            </p>
          ) : null
        }
      />
    </section>
  );
}
