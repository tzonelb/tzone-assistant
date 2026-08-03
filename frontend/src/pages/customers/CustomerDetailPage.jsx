import { useCallback, useEffect, useMemo, useState } from "react";
import { useNavigate, useParams } from "react-router-dom";
import { ArrowBackOutlined, CloseOutlined, HistoryOutlined } from "@mui/icons-material";
import {
  customerOptionsRequest,
  getCustomerRequest,
  getCustomerTimelineRequest,
  updateCustomerRequest,
} from "../../api/client";
import { AppCard, ErrorState, LoadingState } from "../../components/common";
import "./CustomersPage.css";
import "./CustomerDetailPage.css";

const PROFILE_FIELDS = [
  { key: "display_name", label: "Display name" },
  { key: "phone", label: "Phone" },
  { key: "email", label: "Email" },
  { key: "language", label: "Language" },
  { key: "country", label: "Country" },
  { key: "timezone", label: "Timezone" },
];

function humanize(value) {
  return String(value || "").replaceAll("_", " ").replace(/\b\w/g, (letter) => letter.toUpperCase());
}

function formatDateTime(value) {
  if (!value) return "—";
  const normalized = /(?:Z|[+-]\d{2}:?\d{2})$/i.test(value) ? value : `${value}Z`;
  const date = new Date(normalized);
  return Number.isNaN(date.getTime()) ? "—" : date.toLocaleString();
}

function CustomFieldsEditor({ fields, disabled, onAdd, onUpdate, onRemove }) {
  const [draftKey, setDraftKey] = useState("");
  const [draftValue, setDraftValue] = useState("");

  function submit(event) {
    event.preventDefault();
    const key = draftKey.trim();
    if (!key) return;
    onAdd(key, draftValue.trim());
    setDraftKey("");
    setDraftValue("");
  }

  const entries = Object.entries(fields || {});

  return (
    <div className="client-file-kv-editor">
      {entries.length === 0 ? <p className="client-file-empty-hint">No custom fields yet — add whatever this contact needs (ID number, insurance plan, preferred branch...).</p> : null}
      {entries.map(([key, value]) => (
        <div className="client-file-kv-row" key={key}>
          <span className="client-file-kv-key">{key}</span>
          <input
            className="client-file-kv-value"
            defaultValue={value}
            disabled={disabled}
            onBlur={(event) => { if (event.target.value !== value) onUpdate(key, event.target.value); }}
          />
          <button type="button" disabled={disabled} aria-label={`Remove field ${key}`} onClick={() => onRemove(key)}>
            <CloseOutlined fontSize="inherit" />
          </button>
        </div>
      ))}
      <form className="client-file-kv-add-form" onSubmit={submit}>
        <input placeholder="Field name (e.g. ID number)" value={draftKey} disabled={disabled} onChange={(event) => setDraftKey(event.target.value)} />
        <input placeholder="Value" value={draftValue} disabled={disabled} onChange={(event) => setDraftValue(event.target.value)} />
        <button type="submit" className="btn btn-secondary" disabled={disabled || !draftKey.trim()}>Add field</button>
      </form>
    </div>
  );
}

function DocumentsEditor({ documents, disabled, onAdd, onRemove }) {
  const [label, setLabel] = useState("");
  const [url, setUrl] = useState("");

  function submit(event) {
    event.preventDefault();
    const cleanLabel = label.trim();
    const cleanUrl = url.trim();
    if (!cleanLabel || !cleanUrl) return;
    onAdd(cleanLabel, cleanUrl);
    setLabel("");
    setUrl("");
  }

  return (
    <div className="client-file-kv-editor">
      {(!documents || documents.length === 0) ? <p className="client-file-empty-hint">No documents on file — e.g. ID photo, signed contract, warranty card.</p> : null}
      {(documents || []).map((doc, index) => (
        <div className="client-file-kv-row" key={`${doc.label}-${index}`}>
          <a className="client-file-kv-key" href={doc.url} target="_blank" rel="noreferrer">{doc.label}</a>
          <span className="client-file-doc-url">{doc.url}</span>
          <button type="button" disabled={disabled} aria-label={`Remove document ${doc.label}`} onClick={() => onRemove(index)}>
            <CloseOutlined fontSize="inherit" />
          </button>
        </div>
      ))}
      <form className="client-file-kv-add-form" onSubmit={submit}>
        <input placeholder="Document label (e.g. ID photo)" value={label} disabled={disabled} onChange={(event) => setLabel(event.target.value)} />
        <input placeholder="URL" value={url} disabled={disabled} onChange={(event) => setUrl(event.target.value)} />
        <button type="submit" className="btn btn-secondary" disabled={disabled || !label.trim() || !url.trim()}>Add document</button>
      </form>
    </div>
  );
}

function TimelineEvent({ event }) {
  if (event.type === "conversation_started") {
    return (
      <article className="client-file-timeline-event">
        <div className="client-file-timeline-marker" />
        <div>
          <strong>{humanize(event.channel)} conversation started</strong>
          <p>
            {event.topic ? `${event.topic} · ` : ""}
            {event.department || "Unassigned"} · {humanize(event.status)}
            {event.handled_by_name ? ` · Handled by ${event.handled_by_name}` : ""}
          </p>
          <time>{formatDateTime(event.created_at)}</time>
        </div>
      </article>
    );
  }
  const changedFields = Object.keys(event.changes || {});
  return (
    <article className="client-file-timeline-event">
      <div className="client-file-timeline-marker" />
      <div>
        <strong>Profile updated{event.actor_name ? ` by ${event.actor_name}` : ""}</strong>
        {changedFields.length ? <p>{changedFields.map((field) => humanize(field)).join(", ")}</p> : null}
        <time>{formatDateTime(event.created_at)}</time>
      </div>
    </article>
  );
}

export default function CustomerDetailPage() {
  const { customerId } = useParams();
  const navigate = useNavigate();

  const [customer, setCustomer] = useState(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState("");
  const [saving, setSaving] = useState(false);

  const [timeline, setTimeline] = useState([]);
  const [timelineLoading, setTimelineLoading] = useState(true);

  const [lifecycleStages, setLifecycleStages] = useState([]);
  const [employees, setEmployees] = useState([]);
  const [profileForm, setProfileForm] = useState({});

  const load = useCallback(async () => {
    setLoading(true);
    setError("");
    try {
      const result = await getCustomerRequest(customerId);
      setCustomer(result);
      setProfileForm(Object.fromEntries(PROFILE_FIELDS.map(({ key }) => [key, result?.[key] || ""])));
    } catch (requestError) {
      setError(requestError.message || "Contact could not be loaded.");
    } finally {
      setLoading(false);
    }
  }, [customerId]);

  useEffect(() => { load(); }, [load]);

  useEffect(() => {
    setTimelineLoading(true);
    getCustomerTimelineRequest(customerId)
      .then((result) => setTimeline(Array.isArray(result?.items) ? result.items : []))
      .catch(() => {})
      .finally(() => setTimelineLoading(false));
  }, [customerId]);

  useEffect(() => {
    customerOptionsRequest()
      .then((result) => {
        setLifecycleStages(Array.isArray(result?.lifecycle_stages) ? result.lifecycle_stages : []);
        setEmployees(Array.isArray(result?.employees) ? result.employees : []);
      })
      .catch(() => {});
  }, []);

  async function persist(updates) {
    setSaving(true);
    setError("");
    try {
      const result = await updateCustomerRequest(customerId, updates);
      setCustomer(result);
      return result;
    } catch (requestError) {
      setError(requestError.message || "Update failed.");
      throw requestError;
    } finally {
      setSaving(false);
    }
  }

  async function saveProfile(event) {
    event.preventDefault();
    await persist(profileForm).catch(() => {});
  }

  async function changeStage(stage) {
    await persist({ lifecycle_stage: stage }).catch(() => {});
  }

  async function changeAssignee(value) {
    await persist({ assigned_user_id: value ? Number(value) : null }).catch(() => {});
  }

  async function removeTag(tag) {
    await persist({ tags: (customer.tags || []).filter((item) => item !== tag) }).catch(() => {});
  }

  async function addTag(tag) {
    const cleaned = tag.trim();
    if (!cleaned || (customer.tags || []).includes(cleaned)) return;
    await persist({ tags: [...(customer.tags || []), cleaned] }).catch(() => {});
  }

  async function addCustomField(key, value) {
    await persist({ custom_fields: { ...(customer.custom_fields || {}), [key]: value } }).catch(() => {});
  }

  async function updateCustomField(key, value) {
    await persist({ custom_fields: { ...(customer.custom_fields || {}), [key]: value } }).catch(() => {});
  }

  async function removeCustomField(key) {
    const next = { ...(customer.custom_fields || {}) };
    delete next[key];
    await persist({ custom_fields: next }).catch(() => {});
  }

  async function addDocument(label, url) {
    await persist({ documents: [...(customer.documents || []), { label, url }] }).catch(() => {});
  }

  async function removeDocument(index) {
    await persist({ documents: (customer.documents || []).filter((_, i) => i !== index) }).catch(() => {});
  }

  const [tagDraft, setTagDraft] = useState("");

  const title = useMemo(() => customer?.display_name || customer?.internal_name || "Unnamed contact", [customer]);

  if (loading) return <LoadingState title="Loading client file..." />;
  if (error && !customer) return <ErrorState title="Could not load this contact" description={error} action={<button type="button" className="btn btn-primary" onClick={load}>Retry</button>} />;
  if (!customer) return null;

  return (
    <section className="customers-page client-file-page">
      <button type="button" className="client-file-back" onClick={() => navigate("/customers")}>
        <ArrowBackOutlined fontSize="small" /> Back to Contacts
      </button>

      <div className="client-file-header">
        <div className="customer-avatar client-file-avatar">{title.charAt(0).toUpperCase()}</div>
        <div>
          <h2>{title}</h2>
          <div className="customer-channel-list">
            {(customer.channels || []).map((channel) => (
              <span className="customer-channel-chip" style={{ "--channel-color": `var(--tz-channel-${channel}, var(--tz-text-muted))` }} key={channel}>{humanize(channel)}</span>
            ))}
          </div>
        </div>
        <div className="client-file-header-controls">
          <label>
            Lifecycle stage
            <select className="tz-select" value={customer.lifecycle_stage || ""} disabled={saving} onChange={(event) => changeStage(event.target.value)}>
              {lifecycleStages.map((stage) => <option value={stage} key={stage}>{humanize(stage)}</option>)}
            </select>
          </label>
          <label>
            Assigned to
            <select className="tz-select" value={customer.assigned_user_id || ""} disabled={saving} onChange={(event) => changeAssignee(event.target.value)}>
              <option value="">Unassigned</option>
              {employees.map((employee) => <option value={employee.id} key={employee.id}>{employee.display_name}</option>)}
            </select>
          </label>
        </div>
      </div>

      {error ? <p className="customer-segment-error">{error}</p> : null}

      <div className="client-file-grid">
        <div className="client-file-main">
          <AppCard padding="medium">
            <h3 className="client-file-section-title">Profile</h3>
            <form className="client-file-profile-form" onSubmit={saveProfile}>
              {PROFILE_FIELDS.map(({ key, label }) => (
                <label key={key}>
                  {label}
                  <input
                    value={profileForm[key] || ""}
                    disabled={saving}
                    onChange={(event) => setProfileForm((current) => ({ ...current, [key]: event.target.value }))}
                  />
                </label>
              ))}
              <label className="client-file-notes-field">
                Notes
                <textarea
                  rows={3}
                  value={profileForm.notes ?? customer.notes ?? ""}
                  disabled={saving}
                  onChange={(event) => setProfileForm((current) => ({ ...current, notes: event.target.value }))}
                />
              </label>
              <button type="submit" className="btn btn-primary" disabled={saving}>{saving ? "Saving…" : "Save profile"}</button>
            </form>
          </AppCard>

          <AppCard padding="medium">
            <h3 className="client-file-section-title">Tags</h3>
            <div className="customer-tag-editor">
              <div className="customer-tag-list">
                {(customer.tags || []).map((tag) => (
                  <span className="customer-tag-chip" key={tag}>
                    {tag}
                    <button type="button" disabled={saving} aria-label={`Remove tag ${tag}`} onClick={() => removeTag(tag)}><CloseOutlined fontSize="inherit" /></button>
                  </span>
                ))}
              </div>
              <form
                className="customer-tag-add-form"
                onSubmit={(event) => { event.preventDefault(); if (tagDraft.trim()) { addTag(tagDraft); setTagDraft(""); } }}
              >
                <input value={tagDraft} placeholder="+ tag" disabled={saving} onChange={(event) => setTagDraft(event.target.value)} />
              </form>
            </div>
          </AppCard>

          <AppCard padding="medium">
            <h3 className="client-file-section-title">Custom fields</h3>
            <CustomFieldsEditor
              fields={customer.custom_fields}
              disabled={saving}
              onAdd={addCustomField}
              onUpdate={updateCustomField}
              onRemove={removeCustomField}
            />
          </AppCard>

          <AppCard padding="medium">
            <h3 className="client-file-section-title">Documents</h3>
            <DocumentsEditor documents={customer.documents} disabled={saving} onAdd={addDocument} onRemove={removeDocument} />
          </AppCard>
        </div>

        <AppCard padding="medium" className="client-file-timeline-card">
          <h3 className="client-file-section-title"><HistoryOutlined fontSize="small" /> Timeline</h3>
          {timelineLoading ? (
            <LoadingState title="Loading timeline..." />
          ) : timeline.length === 0 ? (
            <p className="client-file-empty-hint">No activity recorded yet.</p>
          ) : (
            <div className="client-file-timeline">
              {timeline.map((event, index) => <TimelineEvent event={event} key={index} />)}
            </div>
          )}
        </AppCard>
      </div>
    </section>
  );
}
