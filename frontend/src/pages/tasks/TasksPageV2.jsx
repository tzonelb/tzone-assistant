import { useCallback, useEffect, useState } from "react";
import { useNavigate } from "react-router-dom";
import {
  AddOutlined,
  CloseOutlined,
  DeleteOutlineOutlined,
  ForumOutlined,
} from "@mui/icons-material";
import {
  createTaskRequest,
  deleteTaskRequest,
  listCustomersRequest,
  listTasksRequest,
  taskOptionsRequest,
  updateTaskRequest,
} from "../../api/client";
import { EmptyState, ErrorState, LoadingState } from "../../components/common";
import "./TasksPageV2.css";

// Same data + actions as TasksPage.jsx (v1) — this is a visual rebuild only,
// matching the mockup's kicker + segmented status filter + bordered table.
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

function isDueToday(value, status) {
  if (status === "done" || status === "cancelled") return false;
  const date = toDateValue(value);
  if (!date) return false;
  const today = new Date();
  today.setHours(0, 0, 0, 0);
  return date.getTime() === today.getTime();
}

export default function TasksPageV2() {
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
  const [deleteError, setDeleteError] = useState("");

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

  function askDeleteTask(row) {
    setDeleteError("");
    setTaskToDelete(row);
  }

  async function confirmDeleteTask() {
    if (!taskToDelete) return;
    setDeleting(true);
    setDeleteError("");
    try {
      await deleteTaskRequest(taskToDelete.id);
      setTaskToDelete(null);
      await load();
    } catch (requestError) {
      setDeleteError(requestError.message || "Could not delete the task.");
    } finally {
      setDeleting(false);
    }
  }

  if (loading && !rows.length) {
    return (
      <div className="tz-screen tzv2-tasks-page">
        <LoadingState title="Loading tasks…" description="Retrieving open work and assignments." />
      </div>
    );
  }

  const openCount = rows.filter((row) => row.status !== "done" && row.status !== "cancelled").length;
  const overdueCount = rows.filter((row) => isOverdue(row.due_at, row.status)).length;
  const dueTodayCount = rows.filter((row) => isDueToday(row.due_at, row.status)).length;

  return (
    <div className="tz-screen tzv2-tasks-page">
      <div className="tzv2-tasks-head">
        <div>
          <span className="tz-kick tzv2-tasks-kick">
            {openCount} open · {overdueCount} overdue · {dueTodayCount} due today
          </span>
        </div>
        <div className="tzv2-tasks-head-actions">
          <div className="seg" role="radiogroup" aria-label="Filter by status">
            <label className="seg-opt">
              <input
                type="radio"
                name="tzv2-task-status"
                checked={statusFilter === ""}
                onChange={() => setStatusFilter("")}
              />
              All
            </label>
            {statuses.map((status) => (
              <label className="seg-opt" key={status}>
                <input
                  type="radio"
                  name="tzv2-task-status"
                  checked={statusFilter === status}
                  onChange={() => setStatusFilter(status)}
                />
                {humanize(status)}
              </label>
            ))}
          </div>
          <select
            className="input tzv2-tasks-assignee-select"
            value={assigneeFilter}
            onChange={(event) => setAssigneeFilter(event.target.value)}
            aria-label="Filter by assignee"
          >
            <option value="">All employees</option>
            {employees.map((employee) => (
              <option value={employee.id} key={employee.id}>{employee.display_name}</option>
            ))}
          </select>
          <button type="button" className="btn btn-primary" onClick={openDialog}>
            <AddOutlined fontSize="small" /> New task
          </button>
        </div>
      </div>

      {error ? (
        <ErrorState title="Could not load tasks" description={error} action={<button type="button" className="btn btn-primary" onClick={load}>Retry</button>} />
      ) : rows.length ? (
        <div className="tz-tablewrap tzv2-tasks-tablewrap">
          <table className="table">
            <thead>
              <tr>
                <th style={{ width: 34 }} />
                <th>Task</th>
                <th>Type</th>
                <th>Priority</th>
                <th>Assigned</th>
                <th>Due</th>
                <th>Status</th>
                <th>Contact</th>
                <th style={{ width: 76 }} />
              </tr>
            </thead>
            <tbody>
              {rows.map((row) => (
                <tr key={row.id}>
                  <td>
                    <input
                      type="checkbox"
                      className="tzv2-tasks-checkbox"
                      checked={row.status === "done"}
                      disabled={savingRowId === row.id}
                      aria-label={row.status === "done" ? `Mark "${row.title}" not done` : `Mark "${row.title}" done`}
                      onChange={() => changeStatus(row, row.status === "done" ? "open" : "done")}
                    />
                  </td>
                  <td>
                    <strong className="tzv2-tasks-title">{row.title}</strong>
                    {row.description ? <span className="tzv2-tasks-desc">{row.description}</span> : null}
                  </td>
                  <td className="tzv2-tasks-type">{humanize(row.task_type)}</td>
                  <td><span className="tag tag-outline">{humanize(row.priority)}</span></td>
                  <td className="tzv2-tasks-assignee">
                    {row.assigned_user_name || <span className="tzv2-tasks-muted">Unassigned</span>}
                  </td>
                  <td className={`tz-num tzv2-tasks-due${isOverdue(row.due_at, row.status) ? " tzv2-tasks-due-overdue" : ""}`}>
                    {formatDueDate(row.due_at)}
                  </td>
                  <td>
                    <select
                      className="input tzv2-tasks-status-select"
                      value={row.status || ""}
                      disabled={savingRowId === row.id}
                      onChange={(event) => changeStatus(row, event.target.value)}
                    >
                      {statuses.map((status) => <option value={status} key={status}>{humanize(status)}</option>)}
                    </select>
                  </td>
                  <td>
                    {row.customer_id ? (
                      <button type="button" className="btn btn-ghost tzv2-tasks-link" onClick={() => navigate(`/customers/${row.customer_id}`)}>
                        {row.customer_name || "View contact"}
                      </button>
                    ) : <span className="tzv2-tasks-muted">—</span>}
                  </td>
                  <td>
                    <div className="tzv2-tasks-row-actions">
                      {row.conversation_channel && row.conversation_external_user_id ? (
                        <button
                          type="button"
                          className="btn btn-ghost btn-icon"
                          aria-label="Open the conversation this task came from"
                          title="Open source conversation"
                          onClick={() => navigate(`/conversations/${encodeURIComponent(row.conversation_channel)}/${encodeURIComponent(row.conversation_external_user_id)}`)}
                        >
                          <ForumOutlined fontSize="small" />
                        </button>
                      ) : null}
                      <button
                        type="button"
                        className="btn btn-ghost btn-icon"
                        aria-label={`Delete task ${row.title}`}
                        onClick={() => askDeleteTask(row)}
                      >
                        <DeleteOutlineOutlined fontSize="small" />
                      </button>
                    </div>
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      ) : (
        <EmptyState title="No tasks found" description="No task matches the current filters." />
      )}

      {dialogOpen ? (
        <div
          className="dialog-backdrop"
          role="presentation"
          onMouseDown={(event) => { if (event.target === event.currentTarget) closeDialog(); }}
        >
          <form className="dialog" role="dialog" aria-modal="true" aria-labelledby="tzv2-task-dialog-title" onSubmit={saveNewTask}>
            <div className="tzv2-tasks-dialog-head">
              <span className="dialog-title" id="tzv2-task-dialog-title">New task</span>
              <button type="button" className="btn btn-ghost btn-icon" aria-label="Close dialog" onClick={closeDialog}>
                <CloseOutlined fontSize="small" />
              </button>
            </div>
            <div className="dialog-body tzv2-tasks-dialog-body">
              <div className="field">
                <label>Title</label>
                <input
                  className="input"
                  value={form.title}
                  onChange={(event) => setForm((current) => ({ ...current, title: event.target.value }))}
                  maxLength={200}
                  autoFocus
                  required
                />
              </div>
              <div className="field">
                <label>Description</label>
                <textarea
                  className="input"
                  value={form.description}
                  onChange={(event) => setForm((current) => ({ ...current, description: event.target.value }))}
                  rows={3}
                />
              </div>
              <div className="tzv2-tasks-field-row">
                <div className="field">
                  <label>Type</label>
                  <select
                    className="input"
                    value={form.taskType}
                    onChange={(event) => setForm((current) => ({ ...current, taskType: event.target.value }))}
                  >
                    {(taskTypes.length ? taskTypes : FALLBACK_TASK_TYPES).map((type) => (
                      <option value={type} key={type}>{humanize(type)}</option>
                    ))}
                  </select>
                </div>
                <div className="field">
                  <label>Priority</label>
                  <select
                    className="input"
                    value={form.priority}
                    onChange={(event) => setForm((current) => ({ ...current, priority: event.target.value }))}
                  >
                    {(priorities.length ? priorities : FALLBACK_PRIORITIES).map((priority) => (
                      <option value={priority} key={priority}>{humanize(priority)}</option>
                    ))}
                  </select>
                </div>
                <div className="field">
                  <label>Assignee</label>
                  <select
                    className="input"
                    value={form.assignedUserId}
                    onChange={(event) => setForm((current) => ({ ...current, assignedUserId: event.target.value }))}
                  >
                    <option value="">Unassigned</option>
                    {employees.map((employee) => <option value={employee.id} key={employee.id}>{employee.display_name}</option>)}
                  </select>
                </div>
                <div className="field">
                  <label>Due date</label>
                  <input
                    type="date"
                    className="input"
                    value={form.dueDate}
                    onChange={(event) => setForm((current) => ({ ...current, dueDate: event.target.value }))}
                  />
                </div>
              </div>

              <div className="field">
                <label>Link to a contact (optional)</label>
                {selectedCustomer ? (
                  <div className="tzv2-tasks-selected-customer">
                    <span>{selectedCustomer.display_name || selectedCustomer.internal_name || "Unnamed contact"}</span>
                    <button type="button" className="btn btn-ghost btn-icon" aria-label="Remove linked contact" onClick={() => setSelectedCustomer(null)}>
                      <CloseOutlined fontSize="small" />
                    </button>
                  </div>
                ) : (
                  <>
                    <input
                      className="input"
                      value={customerQuery}
                      placeholder="Search contacts by name, phone, email..."
                      onChange={(event) => setCustomerQuery(event.target.value)}
                    />
                    {customerQuery.trim() ? (
                      <div className="tzv2-tasks-customer-results">
                        {customerSearching ? <span className="tzv2-tasks-customer-hint">Searching…</span> : null}
                        {!customerSearching && customerResults.length === 0 ? (
                          <span className="tzv2-tasks-customer-hint">No contacts match.</span>
                        ) : null}
                        {customerResults.map((customer) => (
                          <button
                            type="button"
                            className="tzv2-tasks-customer-result"
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

              {formError ? <p className="tzv2-tasks-form-error">{formError}</p> : null}
            </div>
            <div className="dialog-actions">
              <button type="button" className="btn btn-secondary" disabled={saving} onClick={closeDialog}>Cancel</button>
              <button type="submit" className="btn btn-primary" disabled={saving}>{saving ? "Creating…" : "Create task"}</button>
            </div>
          </form>
        </div>
      ) : null}

      {taskToDelete ? (
        <div
          className="dialog-backdrop"
          role="presentation"
          onMouseDown={(event) => { if (event.target === event.currentTarget && !deleting) setTaskToDelete(null); }}
        >
          <section className="dialog" role="dialog" aria-modal="true" aria-labelledby="tzv2-task-delete-title">
            <span className="dialog-title" id="tzv2-task-delete-title">Delete task</span>
            <div className="dialog-body">
              <p>Delete "{taskToDelete.title}"? This cannot be undone.</p>
              {deleteError ? <p className="tzv2-tasks-form-error">{deleteError}</p> : null}
            </div>
            <div className="dialog-actions">
              <button type="button" className="btn btn-secondary" disabled={deleting} onClick={() => setTaskToDelete(null)}>Cancel</button>
              <button type="button" className="btn btn-primary" disabled={deleting} onClick={confirmDeleteTask}>{deleting ? "Deleting…" : "Delete"}</button>
            </div>
          </section>
        </div>
      ) : null}
    </div>
  );
}
