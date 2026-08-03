import { useEffect, useMemo, useState } from "react";
import { CallMadeOutlined, CallReceivedOutlined, CloseOutlined } from "@mui/icons-material";
import {
  callOptionsRequest,
  createCallLogRequest,
  deleteCallLogRequest,
  listCallLogsRequest,
  listCustomersRequest,
} from "../../api/client";
import { AppCard, AppTable, ConfirmDialog, ErrorState, LoadingState, StatusBadge } from "../../components/common";
import { useAuth } from "../../contexts/AuthContext";
import "../customers/CustomersPage.css";
import "./CallsPage.css";

function humanize(value) {
  return String(value || "").replaceAll("_", " ").replace(/\b\w/g, (letter) => letter.toUpperCase());
}

function formatDuration(seconds) {
  const total = Number(seconds || 0);
  const mins = Math.floor(total / 60);
  const secs = total % 60;
  return `${mins}:${String(secs).padStart(2, "0")}`;
}

function formatDateTime(value) {
  if (!value) return "—";
  const normalized = /(?:Z|[+-]\d{2}:?\d{2})$/i.test(value) ? value : `${value}Z`;
  const date = new Date(normalized);
  return Number.isNaN(date.getTime()) ? "—" : date.toLocaleString();
}

const STATUS_TONE = { completed: "success", missed: "danger", no_answer: "warning", voicemail: "info" };

function NewCallDialog({ open, directions, statuses, saving, error, onCancel, onSave }) {
  const [direction, setDirection] = useState("outbound");
  const [status, setStatus] = useState("completed");
  const [phoneNumber, setPhoneNumber] = useState("");
  const [durationMinutes, setDurationMinutes] = useState("");
  const [durationSeconds, setDurationSeconds] = useState("");
  const [notes, setNotes] = useState("");
  const [customerQuery, setCustomerQuery] = useState("");
  const [customerResults, setCustomerResults] = useState([]);
  const [selectedCustomer, setSelectedCustomer] = useState(null);

  useEffect(() => {
    if (!customerQuery.trim() || selectedCustomer) {
      setCustomerResults([]);
      return;
    }
    const timeout = window.setTimeout(() => {
      listCustomersRequest({ search: customerQuery.trim(), limit: 8 })
        .then((result) => setCustomerResults(Array.isArray(result?.items) ? result.items : []))
        .catch(() => setCustomerResults([]));
    }, 300);
    return () => window.clearTimeout(timeout);
  }, [customerQuery, selectedCustomer]);

  if (!open) return null;

  function submit(event) {
    event.preventDefault();
    const seconds = (Number(durationMinutes) || 0) * 60 + (Number(durationSeconds) || 0);
    onSave({
      direction,
      status,
      phone_number: phoneNumber.trim() || null,
      customer_id: selectedCustomer?.id || null,
      duration_seconds: seconds,
      notes: notes.trim() || null,
    });
  }

  const canSave = Boolean(phoneNumber.trim() || selectedCustomer);

  return (
    <div className="tz-dialog-backdrop" role="presentation" onMouseDown={(event) => { if (event.target === event.currentTarget && !saving) onCancel(); }}>
      <form className="tz-dialog" onSubmit={submit}>
        <header className="tz-dialog-header">
          <h3>Log a call</h3>
          <button type="button" className="tz-dialog-close" onClick={onCancel} disabled={saving}><CloseOutlined fontSize="small" /></button>
        </header>
        <div className="tz-dialog-body">
          <label className="calls-field">
            Direction
            <select className="tz-select" value={direction} disabled={saving} onChange={(event) => setDirection(event.target.value)}>
              {directions.map((value) => <option value={value} key={value}>{humanize(value)}</option>)}
            </select>
          </label>
          <label className="calls-field">
            Contact (optional — search existing contacts)
            {selectedCustomer ? (
              <div className="calls-selected-customer">
                <span>{selectedCustomer.display_name || selectedCustomer.internal_name || "Unnamed contact"}</span>
                <button type="button" onClick={() => setSelectedCustomer(null)}><CloseOutlined fontSize="inherit" /></button>
              </div>
            ) : (
              <>
                <input value={customerQuery} disabled={saving} placeholder="Search by name, phone, email..." onChange={(event) => setCustomerQuery(event.target.value)} />
                {customerResults.length ? (
                  <div className="calls-customer-results">
                    {customerResults.map((customer) => (
                      <button type="button" key={customer.id} onClick={() => { setSelectedCustomer(customer); setCustomerQuery(""); setCustomerResults([]); }}>
                        {customer.display_name || customer.internal_name || "Unnamed contact"}
                        {customer.phone ? <span>{customer.phone}</span> : null}
                      </button>
                    ))}
                  </div>
                ) : null}
              </>
            )}
          </label>
          <label className="calls-field">
            Phone number {selectedCustomer ? "(optional)" : "(required if no contact selected)"}
            <input value={phoneNumber} disabled={saving} onChange={(event) => setPhoneNumber(event.target.value)} placeholder="+961 ..." />
          </label>
          <div className="calls-duration-row">
            <label className="calls-field">
              Duration (min)
              <input type="number" min="0" value={durationMinutes} disabled={saving} onChange={(event) => setDurationMinutes(event.target.value)} />
            </label>
            <label className="calls-field">
              Duration (sec)
              <input type="number" min="0" max="59" value={durationSeconds} disabled={saving} onChange={(event) => setDurationSeconds(event.target.value)} />
            </label>
            <label className="calls-field">
              Outcome
              <select className="tz-select" value={status} disabled={saving} onChange={(event) => setStatus(event.target.value)}>
                {statuses.map((value) => <option value={value} key={value}>{humanize(value)}</option>)}
              </select>
            </label>
          </div>
          <label className="calls-field">
            Notes
            <textarea rows={3} value={notes} disabled={saving} onChange={(event) => setNotes(event.target.value)} />
          </label>
          {error ? <p className="customer-segment-error">{error}</p> : null}
        </div>
        <footer className="tz-dialog-actions">
          <button type="button" className="btn btn-secondary" disabled={saving} onClick={onCancel}>Cancel</button>
          <button type="submit" className="btn btn-primary" disabled={saving || !canSave}>{saving ? "Saving…" : "Save"}</button>
        </footer>
      </form>
    </div>
  );
}

export default function CallsPage() {
  const { user, companies } = useAuth();
  const canManageSettings = useMemo(() => {
    if (user?.is_super_admin) return true;
    const activeCompany = companies.find((company) => company.id === user?.active_company_id) || companies[0];
    return activeCompany?.role_code === "owner" || (activeCompany?.permission_codes || []).includes("settings.manage");
  }, [user, companies]);
  const [rows, setRows] = useState([]);
  const [directions, setDirections] = useState([]);
  const [statuses, setStatuses] = useState([]);
  const [directionFilter, setDirectionFilter] = useState("");
  const [statusFilter, setStatusFilter] = useState("");
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState("");

  const [dialogOpen, setDialogOpen] = useState(false);
  const [saving, setSaving] = useState(false);
  const [saveError, setSaveError] = useState("");
  const [toDelete, setToDelete] = useState(null);
  const [deleting, setDeleting] = useState(false);
  const [deleteError, setDeleteError] = useState("");

  async function load() {
    setLoading(true);
    setError("");
    try {
      const result = await listCallLogsRequest({ direction: directionFilter || undefined, status: statusFilter || undefined });
      setRows(Array.isArray(result?.items) ? result.items : []);
    } catch (requestError) {
      setError(requestError.message || "Calls could not be loaded.");
    } finally {
      setLoading(false);
    }
  }

  useEffect(() => { load(); }, [directionFilter, statusFilter]);

  useEffect(() => {
    callOptionsRequest()
      .then((result) => {
        setDirections(Array.isArray(result?.directions) ? result.directions : []);
        setStatuses(Array.isArray(result?.statuses) ? result.statuses : []);
      })
      .catch(() => {});
  }, []);

  async function saveCall(values) {
    setSaving(true);
    setSaveError("");
    try {
      await createCallLogRequest(values);
      setDialogOpen(false);
      await load();
    } catch (requestError) {
      setSaveError(requestError.message || "Could not save this call.");
    } finally {
      setSaving(false);
    }
  }

  async function confirmDelete() {
    if (!toDelete) return;
    setDeleting(true);
    setDeleteError("");
    try {
      await deleteCallLogRequest(toDelete.id);
      setToDelete(null);
      await load();
    } catch (requestError) {
      setDeleteError(requestError.message || "Could not delete this call.");
    } finally {
      setDeleting(false);
    }
  }

  const columns = useMemo(() => [
    {
      key: "direction", label: "",
      width: 36,
      render: (value) => value === "inbound"
        ? <CallReceivedOutlined fontSize="small" className="calls-direction-icon is-inbound" />
        : <CallMadeOutlined fontSize="small" className="calls-direction-icon is-outbound" />,
    },
    {
      key: "customer_name", label: "Contact",
      render: (value, row) => (
        <div>
          <strong>{value || "Unknown contact"}</strong>
          <p className="calls-contact-preview">{row.phone_number || "—"}</p>
        </div>
      ),
    },
    { key: "duration_seconds", label: "Duration", render: (value) => formatDuration(value) },
    { key: "status", label: "Outcome", render: (value) => <StatusBadge status={value} label={humanize(value)} tone={STATUS_TONE[value]} /> },
    { key: "notes", label: "Notes", render: (value) => value || "—" },
    { key: "called_by_name", label: "Logged by", render: (value) => value || "—" },
    { key: "created_at", label: "When", render: (value) => formatDateTime(value) },
    ...(canManageSettings ? [{
      key: "_actions", label: "", align: "right",
      render: (_value, row) => <button type="button" className="btn btn-primary" onClick={() => setToDelete(row)}>Delete</button>,
    }] : []),
  ], [canManageSettings]);

  if (loading) return <LoadingState title="Loading calls..." />;
  if (error) return <ErrorState title="Could not load calls" description={error} action={<button type="button" className="btn btn-primary" onClick={load}>Retry</button>} />;

  return (
    <section className="customers-page">
      <AppCard padding="medium" className="customer-filter-card">
        <div className="calls-filter-bar">
          <select className="tz-select" value={directionFilter} onChange={(event) => setDirectionFilter(event.target.value)}>
            <option value="">All directions</option>
            {directions.map((value) => <option value={value} key={value}>{humanize(value)}</option>)}
          </select>
          <select className="tz-select" value={statusFilter} onChange={(event) => setStatusFilter(event.target.value)}>
            <option value="">All outcomes</option>
            {statuses.map((value) => <option value={value} key={value}>{humanize(value)}</option>)}
          </select>
          <button type="button" className="btn btn-secondary" onClick={() => setDialogOpen(true)}>+ Log a call</button>
        </div>
      </AppCard>

      <AppTable columns={columns} rows={rows} emptyTitle="No calls logged yet" emptyDescription="Log a call to start building a real call history per contact." />

      <NewCallDialog
        open={dialogOpen}
        directions={directions}
        statuses={statuses}
        saving={saving}
        error={saveError}
        onCancel={() => setDialogOpen(false)}
        onSave={saveCall}
      />
      <ConfirmDialog
        open={Boolean(toDelete)}
        title="Delete call log"
        message="Delete this call record? This can't be undone."
        error={deleteError}
        confirmLabel="Delete"
        confirmVariant="danger"
        loading={deleting}
        onConfirm={confirmDelete}
        onCancel={() => { setToDelete(null); setDeleteError(""); }}
      />
    </section>
  );
}
