import { useCallback, useEffect, useMemo, useState } from "react";
import {
  AddOutlined,
  CloseOutlined,
  DeleteOutlineOutlined,
  EventOutlined,
} from "@mui/icons-material";
import {
  appointmentOptionsRequest,
  createAppointmentRequest,
  deleteAppointmentRequest,
  listAppointmentsRequest,
  listCustomersRequest,
  updateAppointmentRequest,
} from "../../api/client";
import { EmptyState, ErrorState, LoadingState } from "../../components/common";
import { useAuth } from "../../contexts/AuthContext";
import "./AppointmentsPageV2.css";

// Same data + actions as AppointmentsPage.jsx (v1) — this is a visual
// rebuild only, matching the mockup's kicker + agenda list + side panel.
// The agenda list is not filtered to a single calendar day (v1 never
// supported that), so each row shows its own date, not just a time.

function humanize(value) {
  return String(value || "").replaceAll("_", " ").replace(/\b\w/g, (letter) => letter.toUpperCase());
}

function toDate(value) {
  if (!value) return null;
  const normalized = /(?:Z|[+-]\d{2}:?\d{2})$/i.test(value) ? value : `${value}Z`;
  const date = new Date(normalized);
  return Number.isNaN(date.getTime()) ? null : date;
}

function formatRowTime(value) {
  const date = toDate(value);
  return date ? date.toLocaleTimeString(undefined, { hour: "2-digit", minute: "2-digit" }) : "—";
}

function formatRowDate(value) {
  const date = toDate(value);
  return date ? date.toLocaleDateString(undefined, { day: "numeric", month: "short" }) : "—";
}

function formatHeaderDate() {
  const now = new Date();
  const weekday = now.toLocaleDateString(undefined, { weekday: "long" });
  const date = now.toLocaleDateString(undefined, { day: "numeric", month: "long" });
  return `${weekday}, ${date}`;
}

function NewAppointmentDialog({ open, employees, saving, error, onCancel, onSave }) {
  const [title, setTitle] = useState("");
  const [scheduledAt, setScheduledAt] = useState("");
  const [durationMinutes, setDurationMinutes] = useState("30");
  const [employeeUserId, setEmployeeUserId] = useState("");
  const [notes, setNotes] = useState("");
  const [customerQuery, setCustomerQuery] = useState("");
  const [customerResults, setCustomerResults] = useState([]);
  const [selectedCustomer, setSelectedCustomer] = useState(null);

  useEffect(() => {
    if (!open) {
      setTitle(""); setScheduledAt(""); setDurationMinutes("30"); setEmployeeUserId("");
      setNotes(""); setCustomerQuery(""); setCustomerResults([]); setSelectedCustomer(null);
    }
  }, [open]);

  useEffect(() => {
    if (!open || !customerQuery.trim() || selectedCustomer) {
      setCustomerResults([]);
      return undefined;
    }
    const timeout = window.setTimeout(() => {
      listCustomersRequest({ search: customerQuery.trim(), limit: 8 })
        .then((result) => setCustomerResults(Array.isArray(result?.items) ? result.items : []))
        .catch(() => setCustomerResults([]));
    }, 300);
    return () => window.clearTimeout(timeout);
  }, [customerQuery, selectedCustomer, open]);

  if (!open) return null;

  function submit(event) {
    event.preventDefault();
    if (!title.trim() || !scheduledAt) return;
    onSave({
      title: title.trim(),
      scheduled_at: new Date(scheduledAt).toISOString(),
      duration_minutes: Number(durationMinutes) || 30,
      employee_user_id: employeeUserId ? Number(employeeUserId) : null,
      customer_id: selectedCustomer?.id || null,
      notes: notes.trim() || null,
    });
  }

  return (
    <div className="dialog-backdrop" role="presentation" onMouseDown={(event) => { if (event.target === event.currentTarget && !saving) onCancel(); }}>
      <form className="dialog" role="dialog" aria-modal="true" aria-labelledby="tzv2-appt-dialog-title" onSubmit={submit}>
        <div className="tzv2-appt-dialog-head">
          <span className="dialog-title" id="tzv2-appt-dialog-title">New appointment</span>
          <button type="button" className="btn btn-ghost btn-icon" aria-label="Close dialog" onClick={onCancel} disabled={saving}>
            <CloseOutlined fontSize="small" />
          </button>
        </div>
        <div className="dialog-body tzv2-appt-dialog-body">
          <div className="field">
            <label>Title</label>
            <input className="input" value={title} disabled={saving} onChange={(event) => setTitle(event.target.value)} placeholder="Consultation, fitting, delivery..." required />
          </div>
          <div className="field">
            <label>Contact (optional — search existing contacts)</label>
            {selectedCustomer ? (
              <div className="tzv2-appt-selected-customer">
                <span>{selectedCustomer.display_name || selectedCustomer.internal_name || "Unnamed contact"}</span>
                <button type="button" className="btn btn-ghost btn-icon" aria-label="Remove linked contact" onClick={() => setSelectedCustomer(null)}>
                  <CloseOutlined fontSize="small" />
                </button>
              </div>
            ) : (
              <>
                <input className="input" value={customerQuery} disabled={saving} placeholder="Search by name, phone, email..." onChange={(event) => setCustomerQuery(event.target.value)} />
                {customerResults.length ? (
                  <div className="tzv2-appt-customer-results">
                    {customerResults.map((customer) => (
                      <button
                        type="button"
                        className="tzv2-appt-customer-result"
                        key={customer.id}
                        onClick={() => { setSelectedCustomer(customer); setCustomerQuery(""); setCustomerResults([]); }}
                      >
                        {customer.display_name || customer.internal_name || "Unnamed contact"}
                        {customer.phone ? <span>{customer.phone}</span> : null}
                      </button>
                    ))}
                  </div>
                ) : null}
              </>
            )}
          </div>
          <div className="tzv2-appt-field-row">
            <div className="field">
              <label>Date &amp; time</label>
              <input type="datetime-local" className="input" value={scheduledAt} disabled={saving} onChange={(event) => setScheduledAt(event.target.value)} required />
            </div>
            <div className="field">
              <label>Duration (min)</label>
              <input type="number" min="1" className="input" value={durationMinutes} disabled={saving} onChange={(event) => setDurationMinutes(event.target.value)} />
            </div>
          </div>
          <div className="field">
            <label>Assigned employee</label>
            <select className="input" value={employeeUserId} disabled={saving} onChange={(event) => setEmployeeUserId(event.target.value)}>
              <option value="">Unassigned</option>
              {employees.map((employee) => <option value={employee.id} key={employee.id}>{employee.display_name}</option>)}
            </select>
          </div>
          <div className="field">
            <label>Notes</label>
            <textarea className="input" rows={3} value={notes} disabled={saving} onChange={(event) => setNotes(event.target.value)} />
          </div>
          {error ? <p className="tzv2-appt-form-error">{error}</p> : null}
        </div>
        <div className="dialog-actions">
          <button type="button" className="btn btn-secondary" disabled={saving} onClick={onCancel}>Cancel</button>
          <button type="submit" className="btn btn-primary" disabled={saving || !title.trim() || !scheduledAt}>{saving ? "Saving…" : "Save"}</button>
        </div>
      </form>
    </div>
  );
}

export default function AppointmentsPageV2() {
  const { user, companies } = useAuth();
  // Mirrors backend appointments.py's _can_view_all: only an owner, super
  // admin, or a role granted users.manage can see appointments belonging
  // to other employees. The backend silently forces employee_user_id back
  // to the viewer's own id regardless of what's picked, so hiding the
  // "All employees" picker from anyone else avoids a misleading result.
  const canViewAllEmployees = useMemo(() => {
    if (user?.is_super_admin) return true;
    const activeCompany = companies.find((company) => company.id === user?.active_company_id) || companies[0];
    return activeCompany?.role_code === "owner" || (activeCompany?.permission_codes || []).includes("users.manage");
  }, [user, companies]);

  const [rows, setRows] = useState([]);
  const [statuses, setStatuses] = useState([]);
  const [employees, setEmployees] = useState([]);
  const [statusFilter, setStatusFilter] = useState("");
  const [employeeFilter, setEmployeeFilter] = useState("");
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState("");

  const [savingRowId, setSavingRowId] = useState(null);
  const [dialogOpen, setDialogOpen] = useState(false);
  const [saving, setSaving] = useState(false);
  const [saveError, setSaveError] = useState("");
  const [toDelete, setToDelete] = useState(null);
  const [deleting, setDeleting] = useState(false);
  const [deleteError, setDeleteError] = useState("");

  const load = useCallback(async () => {
    setLoading(true);
    setError("");
    try {
      const result = await listAppointmentsRequest({
        status: statusFilter || undefined,
        employeeUserId: employeeFilter || undefined,
      });
      setRows(Array.isArray(result?.items) ? result.items : []);
    } catch (requestError) {
      setError(requestError.message || "Appointments could not be loaded.");
    } finally {
      setLoading(false);
    }
  }, [statusFilter, employeeFilter]);

  useEffect(() => { load(); }, [load]);

  useEffect(() => {
    appointmentOptionsRequest()
      .then((result) => {
        setStatuses(Array.isArray(result?.statuses) ? result.statuses : []);
        setEmployees(Array.isArray(result?.employees) ? result.employees : []);
      })
      .catch(() => {});
  }, []);

  async function saveAppointment(values) {
    setSaving(true);
    setSaveError("");
    try {
      await createAppointmentRequest(values);
      setDialogOpen(false);
      await load();
    } catch (requestError) {
      setSaveError(requestError.message || "Could not save this appointment.");
    } finally {
      setSaving(false);
    }
  }

  async function changeStatus(row, status) {
    setSavingRowId(row.id);
    try {
      await updateAppointmentRequest(row.id, { status });
      await load();
    } catch (requestError) {
      setError(requestError.message || "Could not update appointment status.");
    } finally {
      setSavingRowId(null);
    }
  }

  function askDelete(row) {
    setDeleteError("");
    setToDelete(row);
  }

  async function confirmDelete() {
    if (!toDelete) return;
    setDeleting(true);
    setDeleteError("");
    try {
      await deleteAppointmentRequest(toDelete.id);
      setToDelete(null);
      await load();
    } catch (requestError) {
      setDeleteError(requestError.message || "Could not delete this appointment.");
    } finally {
      setDeleting(false);
    }
  }

  const statusCounts = useMemo(() => {
    const counts = {};
    rows.forEach((row) => { counts[row.status] = (counts[row.status] || 0) + 1; });
    return counts;
  }, [rows]);

  const employeeCounts = useMemo(() => {
    const counts = new Map();
    rows.forEach((row) => {
      const label = row.employee_name || "Unassigned";
      counts.set(label, (counts.get(label) || 0) + 1);
    });
    return Array.from(counts.entries()).sort((a, b) => b[1] - a[1]);
  }, [rows]);

  if (loading && !rows.length) {
    return (
      <div className="tz-screen tzv2-appt-page">
        <LoadingState title="Loading appointments…" description="Retrieving the schedule and employee assignments." />
      </div>
    );
  }

  return (
    <div className="tz-screen tzv2-appt-page">
      <div className="tzv2-appt-head">
        <div>
          <span className="tz-kick tzv2-appt-kick">{formatHeaderDate()} · {rows.length} appointment{rows.length === 1 ? "" : "s"}</span>
        </div>
        <div className="tzv2-appt-head-actions">
          <select className="input tzv2-appt-filter-select" value={statusFilter} onChange={(event) => setStatusFilter(event.target.value)} aria-label="Filter by status">
            <option value="">All statuses</option>
            {statuses.map((value) => <option value={value} key={value}>{humanize(value)}</option>)}
          </select>
          {canViewAllEmployees ? (
            <select className="input tzv2-appt-filter-select" value={employeeFilter} onChange={(event) => setEmployeeFilter(event.target.value)} aria-label="Filter by employee">
              <option value="">All employees</option>
              {employees.map((employee) => <option value={employee.id} key={employee.id}>{employee.display_name}</option>)}
            </select>
          ) : null}
          <button type="button" className="btn btn-primary" onClick={() => setDialogOpen(true)}>
            <AddOutlined fontSize="small" /> New appointment
          </button>
        </div>
      </div>

      {error ? (
        <ErrorState title="Could not load appointments" description={error} action={<button type="button" className="btn btn-primary" onClick={load}>Retry</button>} />
      ) : (
        <div className="tzv2-appt-layout">
          <section className="tzv2-appt-agenda">
            {rows.length ? (
              rows.map((row) => (
                <div className="tz-row tzv2-appt-row" key={row.id}>
                  <div className="tzv2-appt-time">
                    <div className="tz-fig tz-num tzv2-appt-time-fig">{formatRowTime(row.scheduled_at)}</div>
                    <span className="tz-kick tzv2-appt-time-kick">{formatRowDate(row.scheduled_at)} · {row.duration_minutes} min</span>
                  </div>
                  <div className="tzv2-appt-content">
                    <strong>{row.title}</strong>
                    <div className="tzv2-appt-meta">{row.customer_name || "No contact"} · staff: {row.employee_name || "Unassigned"}</div>
                    {row.notes ? <div className="tzv2-appt-notes">{row.notes}</div> : null}
                  </div>
                  <div className="tzv2-appt-row-actions">
                    <select
                      className="input tzv2-appt-status-select"
                      value={row.status || ""}
                      disabled={savingRowId === row.id}
                      onChange={(event) => changeStatus(row, event.target.value)}
                      aria-label={`Status for ${row.title}`}
                    >
                      {statuses.map((status) => <option value={status} key={status}>{humanize(status)}</option>)}
                    </select>
                    <button
                      type="button"
                      className="btn btn-ghost btn-icon"
                      aria-label={`Delete appointment ${row.title}`}
                      onClick={() => askDelete(row)}
                    >
                      <DeleteOutlineOutlined fontSize="small" />
                    </button>
                  </div>
                </div>
              ))
            ) : (
              <EmptyState icon={<EventOutlined />} title="No appointments yet" description="Book an appointment to start tracking your schedule." />
            )}
          </section>

          <section className="card tzv2-appt-side">
            <span className="tz-kick tzv2-appt-side-kick">Overview</span>
            <h3 className="tzv2-appt-side-title">Status breakdown</h3>
            {statuses.length ? (
              <div className="tzv2-appt-side-list">
                {statuses.map((status) => (
                  <div className="tzv2-appt-side-row" key={status}>
                    <span className="tag tag-neutral">{humanize(status)}</span>
                    <span className="tz-num">{statusCounts[status] || 0}</span>
                  </div>
                ))}
              </div>
            ) : (
              <p className="tzv2-appt-side-empty">No status options loaded yet.</p>
            )}

            {canViewAllEmployees && employeeCounts.length ? (
              <>
                <div className="hr" />
                <h3 className="tzv2-appt-side-title">By employee</h3>
                <div className="tzv2-appt-side-list">
                  {employeeCounts.map(([label, count]) => (
                    <div className="tzv2-appt-side-row" key={label}>
                      <span>{label}</span>
                      <span className="tz-num">{count}</span>
                    </div>
                  ))}
                </div>
              </>
            ) : null}

            <div className="hr" />
            <p className="tzv2-appt-side-note">Appointments can optionally be linked to an existing contact when they're created, so the customer shows up right here in the schedule.</p>
          </section>
        </div>
      )}

      <NewAppointmentDialog
        open={dialogOpen}
        employees={employees}
        saving={saving}
        error={saveError}
        onCancel={() => setDialogOpen(false)}
        onSave={saveAppointment}
      />

      {toDelete ? (
        <div
          className="dialog-backdrop"
          role="presentation"
          onMouseDown={(event) => { if (event.target === event.currentTarget && !deleting) setToDelete(null); }}
        >
          <section className="dialog" role="dialog" aria-modal="true" aria-labelledby="tzv2-appt-delete-title">
            <span className="dialog-title" id="tzv2-appt-delete-title">Delete appointment</span>
            <div className="dialog-body">
              <p>Delete "{toDelete.title}"? This can't be undone.</p>
              {deleteError ? <p className="tzv2-appt-form-error">{deleteError}</p> : null}
            </div>
            <div className="dialog-actions">
              <button type="button" className="btn btn-secondary" disabled={deleting} onClick={() => setToDelete(null)}>Cancel</button>
              <button type="button" className="btn btn-primary" disabled={deleting} onClick={confirmDelete}>{deleting ? "Deleting…" : "Delete"}</button>
            </div>
          </section>
        </div>
      ) : null}
    </div>
  );
}
