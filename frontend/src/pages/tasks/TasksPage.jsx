import { useCallback, useEffect, useState } from "react";
import { useNavigate } from "react-router-dom";
import { AddOutlined, CloseOutlined, DeleteOutlineOutlined, ForumOutlined } from "@mui/icons-material";
import {
  createTaskRequest,
  deleteTaskRequest,
  listCustomersRequest,
  listTasksRequest,
  taskOptionsRequest,
  updateTaskRequest,
} from "../../api/client";
import { AppCard, AppTable, ConfirmDialog, ErrorState, PageHeader, SearchBar, StatusBadge } from "../../components/common";
import "./TasksPage.css";

const PRIORITY_TONE = { low: "neutral", normal: "info", high: "warning", urgent: "danger" };
const FALLBACK_PRIORITIES = ["low", "normal", "high", "urgent"];
const FALLBACK_TASK_TYPES = ["follow_up", "complaint", "service_request", "sales_inquiry", "internal", "other"];

const EMPTY_FORM = {
  title: "",
  description: "",
  taskType: "other",
  priority: "normal",
  assignedUserId: "",
  dueDate: "",
};

function humanize(value) {
  return String(value || "").replaceAll("_", " ").replace(/\b\w/g, (letter) => letter.toUpperCase());
}

function toDateValue(value) {
  if (!value) return null;
  const hasTime = /T/.test(value);
  const normalized = hasTime
    ? (/(?:Z|[+-]\d{2}:?\d{2})$/i.test(value) ? value : `${value}Z`)
    : `${value}T00:00:00Z`;
  const date = new Date(normalized);
  return Number.isNaN(date.getTime()) ? null : date;
}

function formatDueDate(value) {
  const date = toDateValue(value);
  return date ? date.toLocaleDateString() : "—";
}

function isOverdue(value, status) {
  if (status === "done" || status === "cancelled") return false;
  const date = toDateValue(value);
  if (!date) return false;
  const today = new Date();
  today.setHours(0, 0, 0, 0);
  return date.getTime() < today.getTime();
}

export default function TasksPage() {
  const navigate = useNavigate();
  const [rows, setRows] = useState([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState("");

  const [statuses, setStatuses] = useState([]);
  const [priorities, setPriorities] = useState([]);
  const [taskTypes, setTaskTypes] = useState([]);
  const [employees, setEmployees] = useState([]);

  const [statusFilter, setStatusFilter] = useState("");
  const [assigneeFilter, setAssigneeFilter] = useState("");

  const [savingRowId, setSavingRowId] = useState(null);
  const [taskToDelete, setTaskToDelete] = useState(null);
  const [deleting, setDeleting] = useState(false);

  const [dialogOpen, setDialogOpen] = useState(false);
  const [form, setForm] = useState(EMPTY_FORM);
  const [saving, setSaving] = useState(false);
  const [formError, setFormError] = useState("");

  const [selectedCustomer, setSelectedCustomer] = useState(null);
  const [customerQuery, setCustomerQuery] = useState("");
  const [customerResults, setCustomerResults] = useState([]);
  const [customerSearching, setCustomerSearching] = useState(false);

  const load = useCallback(async () => {
    setLoading(true);
    setError("");
    try {
      const result = await listTasksRequest({
        status: statusFilter || undefined,
        assignedUserId: assigneeFilter || undefined,
      });
      setRows(Array.isArray(result?.items) ? result.items : []);
    } catch (requestError) {
      setError(requestError.message || "Tasks could not be loaded.");
    } finally {
      setLoading(false);
    }
  }, [statusFilter, assigneeFilter]);

  useEffect(() => { load(); }, [load]);

  useEffect(() => {
    taskOptionsRequest()
      .then((result) => {
        setStatuses(Array.isArray(result?.statuses) ? result.statuses : []);
        setPriorities(Array.isArray(result?.priorities) ? result.priorities : []);
        setTaskTypes(Array.isArray(result?.task_types) ? result.task_types : []);
        setEmployees(Array.isArray(result?.employees) ? result.employees : []);
      })
      .catch(() => {});
  }, []);

  useEffect(() => {
    if (!dialogOpen || !customerQuery.trim()) {
      setCustomerResults([]);
      return undefined;
    }
    const timeout = window.setTimeout(() => {
      setCustomerSearching(true);
      listCustomersRequest({ search: customerQuery.trim(), limit: 8 })
        .then((result) => setCustomerResults(Array.isArray(result?.items) ? result.items : []))
        .catch(() => setCustomerResults([]))
        .finally(() => setCustomerSearching(false));
    }, 300);
    return () => window.clearTimeout(timeout);
  }, [customerQuery, dialogOpen]);

  async function changeStatus(row, status) {
    setSavingRowId(row.id);
    try {
      await updateTaskRequest(row.id, { status });
      await load();
    } catch (requestError) {
      setError(requestError.message || "Could not update task status.");
    } finally {
      setSavingRowId(null);
    }
  }

  function openDialog() {
    setForm(EMPTY_FORM);
    setSelectedCustomer(null);
    setCustomerQuery("");
    setCustomerResults([]);
    setFormError("");
    setDialogOpen(true);
  }

  function closeDialog() {
    setDialogOpen(false);
  }

  function pickCustomer(customer) {
    setSelectedCustomer(customer);
    setCustomerQuery("");
    setCustomerResults([]);
  }

  async function saveNewTask(event) {
    event.preventDefault();
    if (!form.title.trim()) return;
    setSaving(true);
    setFormError("");
    try {
      await createTaskRequest({
        title: form.title.trim(),
        description: form.description.trim() || undefined,
        task_type: form.taskType,
        priority: form.priority,
        assigned_user_id: form.assignedUserId ? Number(form.assignedUserId) : undefined,
        customer_id: selectedCustomer ? selectedCustomer.id : undefined,
        due_at: form.dueDate || undefined,
      });
      closeDialog();
      await load();
    } catch (requestError) {
      setFormError(requestError.message || "Could not create the task.");
    } finally {
      setSaving(false);
    }
  }

  async function confirmDeleteTask() {
    if (!taskToDelete) return;
    setDeleting(true);
    try {
      await deleteTaskRequest(taskToDelete.id);
      setTaskToDelete(null);
      await load();
    } catch (requestError) {
      setError(requestError.message || "Could not delete the task.");
    } finally {
      setDeleting(false);
    }
  }

  const columns = [
    {
      key: "title",
      label: "Task",
      render: (_value, row) => (
        <div className="task-title-cell">
          <strong>{row.title}</strong>
          {row.description ? <span>{row.description}</span> : null}
        </div>
      ),
    },
    {
      key: "task_type",
      label: "Type",
      render: (value) => humanize(value),
    },
    {
      key: "priority",
      label: "Priority",
      render: (value) => (
        <StatusBadge status={value} label={humanize(value)} tone={PRIORITY_TONE[value] || "neutral"} />
      ),
    },
    {
      key: "status",
      label: "Status",
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
    {
      key: "assigned_user_name",
      label: "Assignee",
      render: (value) => value || <span className="task-empty-cell">Unassigned</span>,
    },
    {
      key: "customer_name",
      label: "Contact",
      render: (value, row) => (
        row.customer_id
          ? (
            <button type="button" className="task-customer-link" onClick={() => navigate(`/customers/${row.customer_id}`)}>
              {value || "View contact"}
            </button>
          )
          : <span className="task-empty-cell">—</span>
      ),
    },
    {
      key: "due_at",
      label: "Due",
      render: (value, row) => (
        <span className={isOverdue(value, row.status) ? "task-due-overdue" : undefined}>
          {formatDueDate(value)}
        </span>
      ),
    },
    {
      key: "conversation_channel",
      label: "",
      width: 44,
      render: (_value, row) => (
        row.conversation_channel && row.conversation_external_user_id ? (
          <button
            type="button"
            className="task-delete-button"
            aria-label="Open the conversation this task came from"
            title="Open source conversation"
            onClick={() => navigate(`/conversations/${encodeURIComponent(row.conversation_channel)}/${encodeURIComponent(row.conversation_external_user_id)}`)}
          >
            <ForumOutlined fontSize="small" />
          </button>
        ) : null
      ),
    },
    {
      key: "_actions",
      label: "",
      width: 44,
      render: (_value, row) => (
        <button
          type="button"
          className="task-delete-button"
          aria-label={`Delete task ${row.title}`}
          onClick={() => setTaskToDelete(row)}
        >
          <DeleteOutlineOutlined fontSize="small" />
        </button>
      ),
    },
  ];

  return (
    <section className="tasks-page">
      <PageHeader
        actions={
          <button type="button" className="btn btn-primary" onClick={openDialog}>
            <AddOutlined fontSize="small" /> New Task
          </button>
        }
      />

      <AppCard padding="medium" className="task-filter-card">
        <div className="task-filter-bar">
          <select className="tz-select" value={statusFilter} onChange={(event) => setStatusFilter(event.target.value)}>
            <option value="">All statuses</option>
            {statuses.map((status) => <option value={status} key={status}>{humanize(status)}</option>)}
          </select>
          <select className="tz-select" value={assigneeFilter} onChange={(event) => setAssigneeFilter(event.target.value)}>
            <option value="">All employees</option>
            {employees.map((employee) => <option value={employee.id} key={employee.id}>{employee.display_name}</option>)}
          </select>
        </div>
      </AppCard>

      {error ? (
        <ErrorState title="Could not load tasks" description={error} action={<button type="button" className="btn btn-primary" onClick={load}>Retry</button>} />
      ) : (
        <AppTable
          columns={columns}
          rows={rows}
          loading={loading}
          emptyTitle="No tasks found"
          emptyDescription="No task matches the current filters."
          page={1}
          pageSize={Math.max(rows.length, 1)}
          totalRows={rows.length}
          onPageChange={() => {}}
        />
      )}

      {dialogOpen ? (
        <div
          className="tz-dialog-backdrop"
          role="presentation"
          onMouseDown={(event) => { if (event.target === event.currentTarget) closeDialog(); }}
        >
          <form className="tz-dialog" onSubmit={saveNewTask}>
            <header className="tz-dialog-header">
              <h3>New task</h3>
              <button type="button" className="tz-dialog-close" onClick={closeDialog}>
                <CloseOutlined fontSize="small" />
              </button>
            </header>
            <div className="tz-dialog-body task-new-fields">
              <label className="task-field">
                Title
                <input
                  value={form.title}
                  onChange={(event) => setForm((current) => ({ ...current, title: event.target.value }))}
                  maxLength={200}
                  autoFocus
                  required
                />
              </label>
              <label className="task-field">
                Description
                <textarea
                  value={form.description}
                  onChange={(event) => setForm((current) => ({ ...current, description: event.target.value }))}
                  rows={3}
                />
              </label>
              <div className="task-field-row">
                <label className="task-field">
                  Type
                  <select
                    className="tz-select"
                    value={form.taskType}
                    onChange={(event) => setForm((current) => ({ ...current, taskType: event.target.value }))}
                  >
                    {(taskTypes.length ? taskTypes : FALLBACK_TASK_TYPES).map((type) => (
                      <option value={type} key={type}>{humanize(type)}</option>
                    ))}
                  </select>
                </label>
                <label className="task-field">
                  Priority
                  <select
                    className="tz-select"
                    value={form.priority}
                    onChange={(event) => setForm((current) => ({ ...current, priority: event.target.value }))}
                  >
                    {(priorities.length ? priorities : FALLBACK_PRIORITIES).map((priority) => (
                      <option value={priority} key={priority}>{humanize(priority)}</option>
                    ))}
                  </select>
                </label>
                <label className="task-field">
                  Assignee
                  <select
                    className="tz-select"
                    value={form.assignedUserId}
                    onChange={(event) => setForm((current) => ({ ...current, assignedUserId: event.target.value }))}
                  >
                    <option value="">Unassigned</option>
                    {employees.map((employee) => <option value={employee.id} key={employee.id}>{employee.display_name}</option>)}
                  </select>
                </label>
                <label className="task-field">
                  Due date
                  <input
                    type="date"
                    value={form.dueDate}
                    onChange={(event) => setForm((current) => ({ ...current, dueDate: event.target.value }))}
                  />
                </label>
              </div>

              <div className="task-field">
                <span className="task-field-label">Link to a contact (optional)</span>
                {selectedCustomer ? (
                  <div className="task-selected-customer">
                    <span>{selectedCustomer.display_name || selectedCustomer.internal_name || "Unnamed contact"}</span>
                    <button type="button" aria-label="Remove linked contact" onClick={() => setSelectedCustomer(null)}>
                      <CloseOutlined fontSize="inherit" />
                    </button>
                  </div>
                ) : (
                  <>
                    <SearchBar
                      value={customerQuery}
                      placeholder="Search contacts by name, phone, email..."
                      onChange={setCustomerQuery}
                    />
                    {customerQuery.trim() ? (
                      <div className="task-customer-results">
                        {customerSearching ? <span className="task-customer-results-hint">Searching…</span> : null}
                        {!customerSearching && customerResults.length === 0 ? (
                          <span className="task-customer-results-hint">No contacts match.</span>
                        ) : null}
                        {customerResults.map((customer) => (
                          <button
                            type="button"
                            className="task-customer-result"
                            key={customer.id}
                            onClick={() => pickCustomer(customer)}
                          >
                            {customer.display_name || customer.internal_name || "Unnamed contact"}
                          </button>
                        ))}
                      </div>
                    ) : null}
                  </>
                )}
              </div>

              {formError ? <p className="task-form-error">{formError}</p> : null}
            </div>
            <footer className="tz-dialog-actions">
              <button type="button" className="btn btn-secondary" disabled={saving} onClick={closeDialog}>Cancel</button>
              <button type="submit" className="btn btn-primary" disabled={saving}>{saving ? "Creating…" : "Create task"}</button>
            </footer>
          </form>
        </div>
      ) : null}

      <ConfirmDialog
        open={Boolean(taskToDelete)}
        title="Delete task"
        message={`Delete "${taskToDelete?.title}"? This cannot be undone.`}
        confirmLabel="Delete"
        confirmVariant="danger"
        loading={deleting}
        onConfirm={confirmDeleteTask}
        onCancel={() => setTaskToDelete(null)}
      />
    </section>
  );
}
