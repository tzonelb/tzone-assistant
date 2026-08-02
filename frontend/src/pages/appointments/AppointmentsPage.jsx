import { useEffect, useMemo, useState } from "react";
import { CloseOutlined } from "@mui/icons-material";
import {
  appointmentOptionsRequest,
  createAppointmentRequest,
  deleteAppointmentRequest,
  listAppointmentsRequest,
  listCustomersRequest,
  updateAppointmentRequest,
} from "../../api/client";
import { AppButton, AppCard, AppTable, ConfirmDialog, ErrorState, LoadingState, StatusBadge } from "../../components/common";
import { useAuth } from "../../contexts/AuthContext";
import "../customers/CustomersPage.css";
import "./AppointmentsPage.css";

const STATUS_TONE = { scheduled: "info", completed: "success", cancelled: "neutral", no_show: "danger" };

function humanize(value) {
  return String(value || "").replaceAll("_", " ").replace(/\b\w/g, (letter) => letter.toUpperCase());
}

function formatDateTime(value) {
  if (!value) return "—";
  const normalized = /(?:Z|[+-]\d{2}:?\d{2})$/i.test(value) ? value : `${value}Z`;
  const date = new Date(normalized);
  return Number.isNaN(date.getTime()) ? "—" : date.toLocaleString();
}

function NewAppointmentDialog({ open, statuses, employees, saving, error, onCancel, onSave }) {
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
    <div className="tz-dialog-backdrop" role="presentation" onMouseDown={(event) => { if (event.target === event.currentTarget && !saving) onCancel(); }}>
      <form className="tz-dialog ai-teaching-knowledge-dialog" onSubmit={submit}>
        <header className="tz-dialog-header">
          <h3>New appointment</h3>
          <button type="button" className="tz-dialog-close" onClick={onCancel} disabled={saving}><CloseOutlined fontSize="small" /></button>
        </header>
        <div className="tz-dialog-body">
          <label className="ai-teaching-field">
            Title
            <input value={title} disabled={saving} onChange={(event) => setTitle(event.target.value)} placeholder="Consultation, fitting, delivery..." required />
          </label>
          <label className="ai-teaching-field">
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
          <div className="appointments-form-row">
            <label className="ai-teaching-field">
              Date &amp; time
              <input type="datetime-local" value={scheduledAt} disabled={saving} onChange={(event) => setScheduledAt(event.target.value)} required />
            </label>
            <label className="ai-teaching-field">
              Duration (min)
              <input type="number" min="1" value={durationMinutes} disabled={saving} onChange={(event) => setDurationMinutes(event.target.value)} />
            </label>
          </div>
          <label className="ai-teaching-field">
            Assigned employee
            <select className="tz-select" value={employeeUserId} disabled={saving} onChange={(event) => setEmployeeUserId(event.target.value)}>
              <option value="">Unassigned</option>
              {employees.map((employee) => <option value={employee.id} key={employee.id}>{employee.display_name}</option>)}
            </select>
          </label>
          <label className="ai-teaching-field">
            Notes
            <textarea rows={3} value={notes} disabled={saving} onChange={(event) => setNotes(event.target.value)} />
          </label>
          {error ? <p className="customer-segment-error">{error}</p> : null}
        </div>
        <footer className="tz-dialog-actions">
          <AppButton type="button" variant="secondary" disabled={saving} onClick={onCancel}>Cancel</AppButton>
          <AppButton type="submit" variant="primary" loading={saving} disabled={!title.trim() || !scheduledAt}>Save</AppButton>
        </footer>
      </form>
    </div>
  );
}

export default function AppointmentsPage() {
  const { user, companies } = useAuth();
  // Mirrors backend appointments.py's _can_view_all: only an owner,
  // super admin, or a role granted users.manage can see appointments
  // belonging to other employees. Showing the "All employees" picker to
  // anyone else is misleading - the backend silently forces
  // employee_user_id back to the viewer's own id regardless of what's
  // picked, so a coworker's name would always look like they have zero
  // appointments instead of "you can't see this."
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

  async function load() {
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
  }

  useEffect(() => { load(); }, [statusFilter, employeeFilter]);

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

  async function confirmDelete() {
    if (!toDelete) return;
    setDeleting(true);
    try {
      await deleteAppointmentRequest(toDelete.id);
      setToDelete(null);
      await load();
    } catch (requestError) {
      setError(requestError.message || "Could not delete this appointment.");
    } finally {
      setDeleting(false);
    }
  }

  const columns = useMemo(() => [
    {
      key: "title", label: "Appointment",
      render: (value, row) => (
        <div className="task-title-cell">
          <strong>{value}</strong>
          {row.notes ? <span>{row.notes}</span> : null}
        </div>
      ),
    },
    { key: "scheduled_at", label: "When", render: (value) => formatDateTime(value) },
    { key: "duration_minutes", label: "Duration", render: (value) => `${value} min` },
    {
      key: "status", label: "Status",
      render: (value, row) => (
        <select
          className="tz-select task-status-select"
          value={value || ""}
          disabled={savingRowId === row.id}
          onChange={(event) => changeStatus(row, event.target.value)}
        >
          {statuses.map((status) => <option value={status} key={status}>{humanize(status)}</option>)}
        </select>
      ),
    },
    { key: "employee_name", label: "Employee", render: (value) => value || <span className="task-empty-cell">Unassigned</span> },
    {
      key: "customer_name", label: "Contact",
      render: (value, row) => (
        <div>
          <strong>{value || "—"}</strong>
          {row.customer_phone ? <p className="ai-teaching-content-preview">{row.customer_phone}</p> : null}
        </div>
      ),
    },
    {
      key: "_actions", label: "", align: "right",
      render: (_value, row) => <AppButton variant="danger" size="small" onClick={() => setToDelete(row)}>Delete</AppButton>,
    },
  ], [statuses, savingRowId]);

  if (loading) return <LoadingState title="Loading appointments..." />;
  if (error) return <ErrorState title="Could not load appointments" description={error} action={<AppButton variant="primary" onClick={load}>Retry</AppButton>} />;

  return (
    <section className="customers-page">
      <AppCard padding="medium" className="customer-filter-card">
        <div className="calls-filter-bar">
          <select className="tz-select" value={statusFilter} onChange={(event) => setStatusFilter(event.target.value)}>
            <option value="">All statuses</option>
            {statuses.map((value) => <option value={value} key={value}>{humanize(value)}</option>)}
          </select>
          {canViewAllEmployees ? (
            <select className="tz-select" value={employeeFilter} onChange={(event) => setEmployeeFilter(event.target.value)}>
              <option value="">All employees</option>
              {employees.map((employee) => <option value={employee.id} key={employee.id}>{employee.display_name}</option>)}
            </select>
          ) : null}
          <AppButton variant="secondary" onClick={() => setDialogOpen(true)}>+ New appointment</AppButton>
        </div>
      </AppCard>

      <AppTable columns={columns} rows={rows} emptyTitle="No appointments yet" emptyDescription="Book an appointment to start tracking your schedule." />

      <NewAppointmentDialog
        open={dialogOpen}
        statuses={statuses}
        employees={employees}
        saving={saving}
        error={saveError}
        onCancel={() => setDialogOpen(false)}
        onSave={saveAppointment}
      />
      <ConfirmDialog
        open={Boolean(toDelete)}
        title="Delete appointment"
        message="Delete this appointment? This can't be undone."
        confirmLabel="Delete"
        confirmVariant="danger"
        loading={deleting}
        onConfirm={confirmDelete}
        onCancel={() => setToDelete(null)}
      />
    </section>
  );
}
