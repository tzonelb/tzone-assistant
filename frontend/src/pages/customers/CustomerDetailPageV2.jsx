import { useCallback, useEffect, useMemo, useState } from "react";
import { useNavigate, useParams } from "react-router-dom";
import { ArrowBackOutlined, CloseOutlined, HistoryOutlined } from "@mui/icons-material";
import {
  customerOptionsRequest,
  getCustomerRequest,
  getCustomerTimelineRequest,
  updateCustomerRequest,
} from "../../api/client";
import { ErrorState, LoadingState } from "../../components/common";
import "./CustomerDetailPageV2.css";

// Same real data + actions as CustomerDetailPage.jsx (v1) — this is a
// visual rebuild only. There is no mockup screen for the single-contact
// profile, so it borrows the card-based vocabulary already established by
// CustomersPageV2 and the other converted V2 screens instead of copying a
// literal layout.
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
    <div className="tzv2-custdet-kv-editor">
      {entries.length === 0 ? <p className="tzv2-custdet-empty-hint">No custom fields yet — add whatever this contact needs (ID number, insurance plan, preferred branch...).</p> : null}
      {entries.map(([key, value]) => (
        <div className="tzv2-custdet-kv-row" key={key}>
          <span className="tzv2-custdet-kv-key">{key}</span>
          <input
            className="input tzv2-custdet-kv-value"
            defaultValue={value}
            disabled={disabled}
            onBlur={(event) => { if (event.target.value !== value) onUpdate(key, event.target.value); }}
          />
          <button type="button" className="btn btn-ghost btn-icon" disabled={disabled} aria-label={`Remove field ${key}`} onClick={() => onRemove(key)}>
            <CloseOutlined fontSize="inherit" />
          </button>
        </div>
      ))}
      <form className="tzv2-custdet-kv-add-form" onSubmit={submit}>
        <input className="input" placeholder="Field name (e.g. ID number)" value={draftKey} disabled={disabled} onChange={(event) => setDraftKey(event.target.value)} />
        <input className="input" placeholder="Value" value={draftValue} disabled={disabled} onChange={(event) => setDraftValue(event.target.value)} />
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
    <div className="tzv2-custdet-kv-editor">
      {(!documents || documents.length === 0) ? <p className="tzv2-custdet-empty-hint">No documents on file — e.g. ID photo, signed contract, warranty card.</p> : null}
      {(documents || []).map((doc, index) => (
        <div className="tzv2-custdet-kv-row" key={`${doc.label}-${index}`}>
          <a className="tzv2-custdet-kv-key" href={doc.url} target="_blank" rel="noreferrer">{doc.label}</a>
          <span className="tzv2-custdet-doc-url">{doc.url}</span>
          <button type="button" className="btn btn-ghost btn-icon" disabled={disabled} aria-label={`Remove document ${doc.label}`} onClick={() => onRemove(index)}>
            <CloseOutlined fontSize="inherit" />
          </button>
        </div>
      ))}
      <form className="tzv2-custdet-kv-add-form" onSubmit={submit}>
        <input className="input" placeholder="Document label (e.g. ID photo)" value={label} disabled={disabled} onChange={(event) => setLabel(event.target.value)} />
        <input className="input" placeholder="URL" value={url} disabled={disabled} onChange={(event) => setUrl(event.target.value)} />
        <button type="submit" className="btn btn-secondary" disabled={disabled || !label.trim() || !url.trim()}>Add document</button>
      </form>
    </div>
  );
}

function TimelineEvent({ event }) {
  if (event.type === "conversation_started") {
    return (
      <article className="tzv2-custdet-timeline-event">
        <div className="tzv2-custdet-timeline-marker" />
        <div>
          <strong>{humanize(event.channel)} conversation started</strong>
          <p>
            {event.topic ? `${event.topic} · ` : ""}
            {event.department || "Unassigned"} · {humanize(event.status)}
            {event.handled_by_name ? ` · Handled by ${event.handled_by_name}` : ""}
          </p>
          <time className="tz-num">{formatDateTime(event.created_at)}</time>
        </div>
      </article>
    );
  }
  const changedFields = Object.keys(event.changes || {});
  return (
    <article className="tzv2-custdet-timeline-event">
      <div className="tzv2-custdet-timeline-marker" />
      <div>
        <strong>Profile updated{event.actor_name ? ` by ${event.actor_name}` : ""}</strong>
        {changedFields.length ? <p>{changedFields.map((field) => humanize(field)).join(", ")}</p> : null}
        <time className="tz-num">{formatDateTime(event.created_at)}</time>
      </div>
    </article>
  );
}

export default function CustomerDetailPageV2() {
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
      const previousCustomer = customer;
      setCustomer(result);
      // profileForm is a separate local snapshot (so the form can hold
      // in-progress edits without saving on every keystroke) — without
      // re-syncing it here, any OTHER action (stage/tag/assignee/custom
      // field) left it stale, so a later "Save profile" click for an
      // unrelated reason could silently overwrite newer server-side data
      // (from another tab, or an inbound message updating display_name)
      // with the snapshot captured at page load. Only refresh fields the
      // user hasn't actively edited since — a field whose current form
      // value still matches what it was before this update is safe to
      // pull forward; one that's been typed into is left alone.
      setProfileForm((currentForm) => {
        const next = { ...currentForm };
        for (const { key } of PROFILE_FIELDS) {
          const untouchedSinceLastSync = currentForm[key] === (previousCustomer?.[key] || "");
          if (untouchedSinceLastSync) next[key] = result?.[key] || "";
        }
        return next;
      });
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

  if (loading) {
    return (
      <div className="tz-screen tzv2-custdet-page">
        <LoadingState title="Loading client file…" description="Retrieving contact profile and timeline." />
      </div>
    );
  }
  if (error && !customer) {
    return (
      <div className="tz-screen tzv2-custdet-page">
        <ErrorState title="Could not load this contact" description={error} action={<button type="button" className="btn btn-primary" onClick={load}>Retry</button>} />
      </div>
    );
  }
  if (!customer) return null;

  return (
    <div className="tz-screen tzv2-custdet-page">
      <button type="button" className="btn btn-ghost tzv2-custdet-back" onClick={() => navigate("/customers")}>
        <ArrowBackOutlined fontSize="small" /> Back to contacts
      </button>

      <div className="tzv2-custdet-head">
        <span className="tzv2-custdet-avatar">{title.charAt(0).toUpperCase()}</span>
        <div className="tzv2-custdet-head-main">
          <span className="tz-kick tzv2-custdet-kick">{customer.lifecycle_stage ? humanize(customer.lifecycle_stage) : "Contact"}</span>
          <h1>{title}</h1>
          <div className="tzv2-custdet-channels">
            {(customer.channels || []).map((channel) => (
              <span className="tag tag-outline" key={channel}>{humanize(channel)}</span>
            ))}
          </div>
        </div>
        <div className="tzv2-custdet-head-controls">
          <div className="field">
            <label>Lifecycle stage</label>
            <select className="input" value={customer.lifecycle_stage || ""} disabled={saving} onChange={(event) => changeStage(event.target.value)}>
              {lifecycleStages.map((stage) => <option value={stage} key={stage}>{humanize(stage)}</option>)}
            </select>
          </div>
          <div className="field">
            <label>Assigned to</label>
            <select className="input" value={customer.assigned_user_id || ""} disabled={saving} onChange={(event) => changeAssignee(event.target.value)}>
              <option value="">Unassigned</option>
              {employees.map((employee) => <option value={employee.id} key={employee.id}>{employee.display_name}</option>)}
            </select>
          </div>
        </div>
      </div>

      {error ? <p className="tzv2-custdet-form-error">{error}</p> : null}

      <div className="tzv2-custdet-grid">
        <div className="tzv2-custdet-main">
          <section className="card">
            <span className="card-kicker">Profile</span>
            <form className="tzv2-custdet-profile-form" onSubmit={saveProfile}>
              {PROFILE_FIELDS.map(({ key, label }) => (
                <div className="field" key={key}>
                  <label>{label}</label>
                  <input
                    className="input"
                    value={profileForm[key] || ""}
                    disabled={saving}
                    onChange={(event) => setProfileForm((current) => ({ ...current, [key]: event.target.value }))}
                  />
                </div>
              ))}
              <div className="field tzv2-custdet-notes-field">
                <label>Notes</label>
                <textarea
                  className="input"
                  rows={3}
                  value={profileForm.notes ?? customer.notes ?? ""}
                  disabled={saving}
                  onChange={(event) => setProfileForm((current) => ({ ...current, notes: event.target.value }))}
                />
              </div>
              <button type="submit" className="btn btn-primary tzv2-custdet-save-btn" disabled={saving}>{saving ? "Saving…" : "Save profile"}</button>
            </form>
          </section>

          <section className="card">
            <span className="card-kicker">Tags</span>
            <div className="tzv2-custdet-tag-editor">
              <div className="tzv2-custdet-tag-list">
                {(customer.tags || []).map((tag) => (
                  <span className="tag tag-outline tzv2-custdet-tag-chip" key={tag}>
                    {tag}
                    <button type="button" disabled={saving} aria-label={`Remove tag ${tag}`} onClick={() => removeTag(tag)}><CloseOutlined fontSize="inherit" /></button>
                  </span>
                ))}
              </div>
              <form
                className="tzv2-custdet-tag-add-form"
                onSubmit={(event) => { event.preventDefault(); if (tagDraft.trim()) { addTag(tagDraft); setTagDraft(""); } }}
              >
                <input className="input" value={tagDraft} placeholder="+ tag" disabled={saving} onChange={(event) => setTagDraft(event.target.value)} />
              </form>
            </div>
          </section>

          <section className="card">
            <span className="card-kicker">Custom fields</span>
            <CustomFieldsEditor
              fields={customer.custom_fields}
              disabled={saving}
              onAdd={addCustomField}
              onUpdate={updateCustomField}
              onRemove={removeCustomField}
            />
          </section>

          <section className="card">
            <span className="card-kicker">Documents</span>
            <DocumentsEditor documents={customer.documents} disabled={saving} onAdd={addDocument} onRemove={removeDocument} />
          </section>
        </div>

        <section className="card tzv2-custdet-timeline-card">
          <span className="card-kicker"><HistoryOutlined fontSize="inherit" /> Timeline</span>
          {timelineLoading ? (
            <LoadingState title="Loading timeline…" />
          ) : timeline.length === 0 ? (
            <p className="tzv2-custdet-empty-hint">No activity recorded yet.</p>
          ) : (
            <div className="tzv2-custdet-timeline">
              {timeline.map((event, index) => <TimelineEvent event={event} key={index} />)}
            </div>
          )}
        </section>
      </div>
    </div>
  );
}
