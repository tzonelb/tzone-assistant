import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import {
  AddOutlined,
  CalendarMonthOutlined,
  CloseOutlined,
  DeleteOutlineOutlined,
  EditOutlined,
  LockOutlined,
  RefreshOutlined,
} from "@mui/icons-material";

import {
  createAppointmentRequest,
  deleteAppointmentRequest,
  getAppointmentsRequest,
  getAssignableAppointmentUsersRequest,
  getCustomersRequest,
  updateAppointmentRequest,
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
import "./AppointmentsPage.css";

const PAGE_SIZE = 20;

const STATUS_OPTIONS = [
  { value: "scheduled", label: "Scheduled" },
  { value: "completed", label: "Completed" },
  { value: "cancelled", label: "Cancelled" },
  { value: "no_show", label: "No-show" },
];

const STATUS_TONE = {
  scheduled: "info",
  completed: "success",
  cancelled: "neutral",
  no_show: "danger",
};

const STATUS_LABEL = Object.fromEntries(
  STATUS_OPTIONS.map((option) => [option.value, option.label]),
);

const EMPTY_FORM = {
  title: "",
  description: "",
  customer_id: "",
  assignee_user_id: "",
  starts_at: "",
  ends_at: "",
  location: "",
  status: "scheduled",
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

function appointmentToForm(appointment) {
  return {
    title: appointment?.title || "",
    description: appointment?.description || "",
    customer_id: appointment?.customer_id ? String(appointment.customer_id) : "",
    assignee_user_id: appointment?.assignee_user_id
      ? String(appointment.assignee_user_id)
      : "",
    starts_at: toDatetimeLocalValue(appointment?.starts_at),
    ends_at: toDatetimeLocalValue(appointment?.ends_at),
    location: appointment?.location || "",
    status: appointment?.status || "scheduled",
  };
}

export default function AppointmentsPage() {
  const { hasPermission } = useAuth();
  const canView = hasPermission("appointments.view");
  const canManage = hasPermission("appointments.manage");

  const [statusFilter, setStatusFilter] = useState("all");
  const [assigneeFilter, setAssigneeFilter] = useState("all");
  const [searchInput, setSearchInput] = useState("");
  const [search, setSearch] = useState("");
  const [page, setPage] = useState(1);

  const [rows, setRows] = useState([]);
  const [total, setTotal] = useState(0);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState("");

  const [employees, setEmployees] = useState([]);
  const [customers, setCustomers] = useState([]);
  const [customerSearch, setCustomerSearch] = useState("");

  const [editorOpen, setEditorOpen] = useState(false);
  const [isEdit, setIsEdit] = useState(false);
  const [activeAppointmentId, setActiveAppointmentId] = useState(null);
  const [form, setForm] = useState(EMPTY_FORM);
  const [baseUpdatedAt, setBaseUpdatedAt] = useState(null);
  const [saving, setSaving] = useState(false);
  const [formError, setFormError] = useState("");

  const [deleteTarget, setDeleteTarget] = useState(null);
  const [deleting, setDeleting] = useState(false);

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
      const result = await getAppointmentsRequest({
        status: statusFilter,
        assigneeUserId: assigneeFilter === "all" ? null : Number(assigneeFilter),
        search,
        limit: PAGE_SIZE,
        offset: (page - 1) * PAGE_SIZE,
      });
      if (seq !== requestSeq.current) return;
      setRows(result?.items || []);
      setTotal(result?.total || 0);
    } catch (requestError) {
      if (seq !== requestSeq.current) return;
      setError(requestError.message || "Appointments could not be loaded.");
      setRows([]);
      setTotal(0);
    } finally {
      if (seq === requestSeq.current) setLoading(false);
    }
  }, [canView, statusFilter, assigneeFilter, search, page]);

  useEffect(() => {
    load();
  }, [load]);

  useEffect(() => {
    if (!canView) return;
    getAssignableAppointmentUsersRequest()
      .then((result) => setEmployees(Array.isArray(result?.items) ? result.items : []))
      .catch(() => setEmployees([]));
  }, [canView]);

  useEffect(() => {
    const handle = setTimeout(() => {
      setSearch(searchInput.trim());
      setPage(1);
    }, 350);
    return () => clearTimeout(handle);
  }, [searchInput]);

  useEffect(() => {
    setPage(1);
  }, [statusFilter, assigneeFilter]);

  function loadCustomersIfNeeded() {
    if (customers.length || !canManage) return;
    getCustomersRequest({ limit: 100 })
      .then((result) => setCustomers(Array.isArray(result?.items) ? result.items : []))
      .catch(() => setCustomers([]));
  }

  function openCreate() {
    setForm(EMPTY_FORM);
    setBaseUpdatedAt(null);
    setActiveAppointmentId(null);
    setIsEdit(false);
    setFormError("");
    setCustomerSearch("");
    loadCustomersIfNeeded();
    setEditorOpen(true);
  }

  function openEdit(appointment) {
    setForm(appointmentToForm(appointment));
    setBaseUpdatedAt(appointment?.updated_at ?? null);
    setActiveAppointmentId(appointment.id);
    setIsEdit(true);
    setFormError("");
    setCustomerSearch("");
    loadCustomersIfNeeded();
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
    if (!title) {
      setFormError("A title is required.");
      return;
    }
    if (!form.starts_at) {
      setFormError("A start time is required.");
      return;
    }

    const payload = {
      title,
      description: form.description.trim() || null,
      customer_id: form.customer_id ? Number(form.customer_id) : null,
      assignee_user_id: form.assignee_user_id ? Number(form.assignee_user_id) : null,
      starts_at: new Date(form.starts_at).toISOString(),
      ends_at: form.ends_at ? new Date(form.ends_at).toISOString() : null,
      location: form.location.trim() || null,
      status: form.status,
    };

    setSaving(true);
    setFormError("");
    try {
      if (isEdit) {
        await updateAppointmentRequest(activeAppointmentId, {
          ...payload,
          expected_updated_at: baseUpdatedAt,
        });
      } else {
        await createAppointmentRequest(payload);
      }
      setEditorOpen(false);
      await load();
    } catch (err) {
      if (err?.status === 409) {
        const detail = err?.data?.detail;
        // The two 409 causes are distinguished by the *shape* of `detail`,
        // not by whether a "current" record came back: the concurrency
        // conflict always sends an object ({message, current}), even when
        // `current` is null because the record was deleted by someone else
        // in the same race; the overlap conflict always sends a plain
        // string. Branching on `current` truthiness would misclassify that
        // null-current race as an overlap and show the wrong message.
        if (detail && typeof detail === "object" && !Array.isArray(detail)) {
          const current = detail.current;
          if (current) {
            setForm(appointmentToForm(current));
            setBaseUpdatedAt(current?.updated_at ?? null);
          }
          setFormError(
            detail.message ||
              "This appointment was changed elsewhere. It has been reloaded — review and save again.",
          );
        } else {
          // Overlap conflict: the service raised a plain string detail, no
          // "current" record to reload (nothing on this record changed).
          setFormError(
            typeof detail === "string"
              ? detail
              : "This time slot conflicts with another scheduled appointment for this assignee.",
          );
        }
      } else {
        setFormError(err.message || "The appointment could not be saved.");
      }
    } finally {
      setSaving(false);
    }
  }

  async function handleDelete() {
    if (!deleteTarget) return;
    setDeleting(true);
    try {
      await deleteAppointmentRequest(deleteTarget.id);
      setDeleteTarget(null);
      await load();
    } catch (err) {
      setError(err.message || "The appointment could not be deleted.");
      setDeleteTarget(null);
    } finally {
      setDeleting(false);
    }
  }

  const filteredCustomerOptions = useMemo(() => {
    const needle = customerSearch.trim().toLowerCase();
    if (!needle) return customers;
    return customers.filter((customer) => {
      const name = (customer.display_name || customer.internal_name || "").toLowerCase();
      return name.includes(needle);
    });
  }, [customers, customerSearch]);

  function buildColumns() {
    const cols = [
      {
        key: "title",
        label: "Appointment",
        render: (_value, row) => (
          <div className="appointment-title-cell">
            <strong>{row.title}</strong>
            {row.customer_name ? (
              <span className="appointment-customer-chip">For {row.customer_name}</span>
            ) : null}
          </div>
        ),
      },
      {
        key: "starts_at",
        label: "When",
        render: (_value, row) => (
          <div className="appointment-when-cell">
            <span>{formatDateTime(row.starts_at)}</span>
            {row.location ? <span className="appointment-location">{row.location}</span> : null}
          </div>
        ),
      },
      {
        key: "assignee_name",
        label: "Assigned to",
        render: (_value, row) => row.assignee_name || row.assignee_email || "Unassigned",
      },
      {
        key: "status",
        label: "Status",
        render: (value) => (
          <StatusBadge status={value} tone={STATUS_TONE[value]} label={STATUS_LABEL[value] || value} />
        ),
      },
    ];

    if (canManage) {
      cols.push({
        key: "actions",
        label: "",
        align: "right",
        render: (_value, row) => (
          <div className="appointment-row-actions">
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
      <section className="appointments-page">
        <PageHeader
          eyebrow="APPOINTMENTS"
          title="Appointments"
          description="Optional booking module connected to calendars, employees and customer profiles."
        />
        <AppCard padding="large">
          <EmptyState
            icon={<LockOutlined />}
            title="You don't have access to Appointments"
            description="Ask a company administrator to grant you the “View Appointments” permission."
          />
        </AppCard>
      </section>
    );
  }

  const columns = buildColumns();

  return (
    <section className="appointments-page">
      <PageHeader
        eyebrow="APPOINTMENTS"
        title="Appointments"
        description="Book and track appointments connected to your team's calendars and customer profiles."
        actions={
          <div className="appointment-row-actions">
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
                New appointment
              </AppButton>
            ) : null}
          </div>
        }
      />

      {!canManage ? (
        <p className="appointments-inline-note">
          <LockOutlined fontSize="small" /> You have read-only access. Ask an
          administrator for the &quot;Manage Appointments&quot; permission to
          book, edit or cancel appointments.
        </p>
      ) : null}

      <AppCard padding="medium">
        <div className="appointments-toolbar">
          <SearchBar
            value={searchInput}
            placeholder="Search appointments by title or description..."
            ariaLabel="Search appointments"
            onChange={setSearchInput}
            onClear={() => setSearchInput("")}
          />

          <label className="appointments-filter">
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

          <label className="appointments-filter">
            <span>Assignee</span>
            <select value={assigneeFilter} onChange={(event) => setAssigneeFilter(event.target.value)}>
              <option value="all">All team members</option>
              {employees.map((employee) => (
                <option key={employee.id} value={employee.id}>
                  {employee.display_name}
                </option>
              ))}
            </select>
          </label>

          <StatusBadge
            status="info"
            tone="info"
            showDot={false}
            label={`${total} appointment${total === 1 ? "" : "s"}`}
          />
        </div>

        {error ? (
          <ErrorState
            title="Appointments could not load"
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
            emptyTitle="No appointments found"
            emptyDescription={
              search || statusFilter !== "all" || assigneeFilter !== "all"
                ? "No appointments match your filters. Try widening your search."
                : canManage
                  ? "Book your first appointment to start filling the calendar."
                  : "Appointments booked by the team will appear here."
            }
            renderMobileCard={(row) => (
              <div className="tz-mobile-record-fields">
                <div className="appointment-title-cell">
                  <strong>{row.title}</strong>
                  {row.customer_name ? (
                    <span className="appointment-customer-chip">For {row.customer_name}</span>
                  ) : null}
                </div>
                <span>{formatDateTime(row.starts_at)}</span>
                <StatusBadge status={row.status} tone={STATUS_TONE[row.status]} label={STATUS_LABEL[row.status] || row.status} />
                <span>{row.assignee_name || row.assignee_email || "Unassigned"}</span>
                {canManage ? (
                  <div className="appointment-row-actions">
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

      {editorOpen ? (
        <div
          className="tz-dialog-backdrop"
          role="presentation"
          onMouseDown={(event) => {
            if (event.target === event.currentTarget) closeEditor();
          }}
        >
          <section
            className="tz-dialog appointments-dialog"
            role="dialog"
            aria-modal="true"
            aria-labelledby="appointment-editor-title"
          >
            <header className="tz-dialog-header">
              <h3 id="appointment-editor-title">
                <CalendarMonthOutlined fontSize="small" /> {isEdit ? "Edit appointment" : "New appointment"}
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
              <div className="appointments-form">
                <label className="appointments-field">
                  <span>Title</span>
                  <input
                    type="text"
                    value={form.title}
                    disabled={saving}
                    placeholder="e.g. Installation visit"
                    onChange={(event) => updateForm("title", event.target.value)}
                  />
                </label>

                <label className="appointments-field">
                  <span>Description</span>
                  <textarea
                    value={form.description}
                    disabled={saving}
                    placeholder="Notes or context for whoever handles this appointment"
                    onChange={(event) => updateForm("description", event.target.value)}
                  />
                </label>

                <div className="appointments-grid-2">
                  <label className="appointments-field">
                    <span>Starts</span>
                    <input
                      type="datetime-local"
                      value={form.starts_at}
                      disabled={saving}
                      onChange={(event) => updateForm("starts_at", event.target.value)}
                    />
                  </label>

                  <label className="appointments-field">
                    <span>Ends (optional)</span>
                    <input
                      type="datetime-local"
                      value={form.ends_at}
                      disabled={saving}
                      onChange={(event) => updateForm("ends_at", event.target.value)}
                    />
                  </label>
                </div>

                <div className="appointments-grid-2">
                  <label className="appointments-field">
                    <span>Assign to</span>
                    <select
                      value={form.assignee_user_id}
                      disabled={saving}
                      onChange={(event) => updateForm("assignee_user_id", event.target.value)}
                    >
                      <option value="">Unassigned</option>
                      {employees.map((employee) => (
                        <option key={employee.id} value={employee.id}>
                          {employee.display_name}
                        </option>
                      ))}
                    </select>
                  </label>

                  <label className="appointments-field">
                    <span>Location</span>
                    <input
                      type="text"
                      value={form.location}
                      disabled={saving}
                      placeholder="e.g. Customer site, Branch office"
                      onChange={(event) => updateForm("location", event.target.value)}
                    />
                  </label>
                </div>

                <label className="appointments-field">
                  <span>Related customer (optional)</span>
                  <select
                    value={form.customer_id}
                    disabled={saving}
                    onChange={(event) => updateForm("customer_id", event.target.value)}
                  >
                    <option value="">None</option>
                    {filteredCustomerOptions.map((customer) => (
                      <option key={customer.id} value={customer.id}>
                        {customer.display_name || customer.internal_name || `Customer ${customer.id}`}
                      </option>
                    ))}
                  </select>
                </label>

                {customers.length > 8 ? (
                  <label className="appointments-field">
                    <span>Filter customers</span>
                    <input
                      type="text"
                      value={customerSearch}
                      disabled={saving}
                      placeholder="Type to narrow the customer list above"
                      onChange={(event) => setCustomerSearch(event.target.value)}
                    />
                  </label>
                ) : null}

                {isEdit ? (
                  <label className="appointments-field">
                    <span>Status</span>
                    <select
                      value={form.status}
                      disabled={saving}
                      onChange={(event) => updateForm("status", event.target.value)}
                    >
                      {STATUS_OPTIONS.map((option) => (
                        <option key={option.value} value={option.value}>
                          {option.label}
                        </option>
                      ))}
                    </select>
                  </label>
                ) : null}

                {formError ? <p className="appointments-form-error">{formError}</p> : null}
              </div>
            </div>

            <footer className="tz-dialog-actions">
              <AppButton variant="secondary" disabled={saving} onClick={closeEditor}>
                Cancel
              </AppButton>
              <AppButton variant="primary" loading={saving} onClick={handleSave}>
                {isEdit ? "Save changes" : "Book appointment"}
              </AppButton>
            </footer>
          </section>
        </div>
      ) : null}

      <ConfirmDialog
        open={Boolean(deleteTarget)}
        title="Delete appointment"
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
