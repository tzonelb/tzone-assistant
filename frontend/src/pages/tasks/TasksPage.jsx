import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import {
  AddOutlined,
  AssignmentTurnedInOutlined,
  CloseOutlined,
  DeleteOutlineOutlined,
  EditOutlined,
  LockOutlined,
  RefreshOutlined,
} from "@mui/icons-material";

import {
  createTaskRequest,
  deleteTaskRequest,
  getAssignableTaskUsersRequest,
  getCustomersRequest,
  getTasksRequest,
  updateTaskRequest,
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
import "./TasksPage.css";

const PAGE_SIZE = 20;

const STATUS_OPTIONS = [
  { value: "open", label: "Open" },
  { value: "in_progress", label: "In progress" },
  { value: "done", label: "Done" },
  { value: "cancelled", label: "Cancelled" },
];

const PRIORITY_OPTIONS = [
  { value: "low", label: "Low" },
  { value: "normal", label: "Normal" },
  { value: "high", label: "High" },
  { value: "urgent", label: "Urgent" },
];

const STATUS_TONE = {
  open: "info",
  in_progress: "warning",
  done: "success",
  cancelled: "danger",
};

const PRIORITY_TONE = {
  low: "neutral",
  normal: "info",
  high: "warning",
  urgent: "danger",
};

const STATUS_LABEL = Object.fromEntries(
  STATUS_OPTIONS.map((option) => [option.value, option.label]),
);
const PRIORITY_LABEL = Object.fromEntries(
  PRIORITY_OPTIONS.map((option) => [option.value, option.label]),
);

const EMPTY_FORM = {
  title: "",
  description: "",
  status: "open",
  priority: "normal",
  assignee_user_id: "",
  due_date: "",
  related_customer_id: "",
};

function formatDate(value) {
  if (!value) return "—";
  // Bare "YYYY-MM-DD" values (from <input type="date">) parse as UTC
  // midnight, which can display as the previous day in western timezones.
  // Parse those as local-time midnight instead.
  const isDateOnly = /^\d{4}-\d{2}-\d{2}$/.test(value);
  const date = new Date(isDateOnly ? `${value}T00:00:00` : value);
  if (Number.isNaN(date.getTime())) return value;
  return date.toLocaleDateString(undefined, {
    year: "numeric",
    month: "short",
    day: "numeric",
  });
}

function formatDateTime(value) {
  if (!value) return "—";
  const date = new Date(value);
  if (Number.isNaN(date.getTime())) return "—";
  return date.toLocaleString(undefined, {
    year: "numeric",
    month: "short",
    day: "numeric",
    hour: "2-digit",
    minute: "2-digit",
  });
}

function taskToForm(task) {
  return {
    title: task?.title || "",
    description: task?.description || "",
    status: task?.status || "open",
    priority: task?.priority || "normal",
    assignee_user_id: task?.assignee_user_id ? String(task.assignee_user_id) : "",
    due_date: task?.due_date || "",
    related_customer_id: task?.related_customer_id
      ? String(task.related_customer_id)
      : "",
  };
}

export default function TasksPage() {
  const { hasPermission } = useAuth();
  const canView = hasPermission("tasks.view");
  const canManage = hasPermission("tasks.manage");

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
  const [activeTaskId, setActiveTaskId] = useState(null);
  const [activeTaskCreatedAt, setActiveTaskCreatedAt] = useState(null);
  const [form, setForm] = useState(EMPTY_FORM);
  const [baseUpdatedAt, setBaseUpdatedAt] = useState(null);
  const [saving, setSaving] = useState(false);
  const [formError, setFormError] = useState("");

  const [deleteTarget, setDeleteTarget] = useState(null);
  const [deleting, setDeleting] = useState(false);
  const [busyTaskId, setBusyTaskId] = useState(null);

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
      const result = await getTasksRequest({
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
      setError(requestError.message || "Tasks could not be loaded.");
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
    getAssignableTaskUsersRequest()
      .then((result) => setEmployees(Array.isArray(result?.items) ? result.items : []))
      .catch(() => setEmployees([]));
  }, [canView]);

  // Debounce the search box and reset to the first page on a new query.
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
    setActiveTaskId(null);
    setActiveTaskCreatedAt(null);
    setIsEdit(false);
    setFormError("");
    setCustomerSearch("");
    loadCustomersIfNeeded();
    setEditorOpen(true);
  }

  function openEdit(task) {
    setForm(taskToForm(task));
    setBaseUpdatedAt(task?.updated_at ?? null);
    setActiveTaskId(task.id);
    setActiveTaskCreatedAt(task?.created_at ?? null);
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

    const payload = {
      title,
      description: form.description.trim() || null,
      status: form.status,
      priority: form.priority,
      assignee_user_id: form.assignee_user_id ? Number(form.assignee_user_id) : null,
      due_date: form.due_date || null,
      related_customer_id: form.related_customer_id
        ? Number(form.related_customer_id)
        : null,
    };

    setSaving(true);
    setFormError("");
    try {
      if (isEdit) {
        await updateTaskRequest(activeTaskId, {
          ...payload,
          expected_updated_at: baseUpdatedAt,
        });
      } else {
        await createTaskRequest(payload);
      }
      setEditorOpen(false);
      await load();
    } catch (err) {
      if (err?.status === 409) {
        const current = err?.data?.detail?.current;
        if (current) {
          setForm(taskToForm(current));
          setBaseUpdatedAt(current?.updated_at ?? null);
        }
        setFormError(
          err?.data?.detail?.message ||
            "This task was changed elsewhere. It has been reloaded — review and save again.",
        );
      } else {
        setFormError(err.message || "The task could not be saved.");
      }
    } finally {
      setSaving(false);
    }
  }

  async function handleMarkDone(task) {
    setBusyTaskId(task.id);
    try {
      await updateTaskRequest(task.id, {
        status: "done",
        expected_updated_at: task.updated_at,
      });
      await load();
    } catch (err) {
      setError(err.message || "The task could not be updated.");
    } finally {
      setBusyTaskId(null);
    }
  }

  async function handleDelete() {
    if (!deleteTarget) return;
    setDeleting(true);
    try {
      await deleteTaskRequest(deleteTarget.id);
      setDeleteTarget(null);
      await load();
    } catch (err) {
      setError(err.message || "The task could not be deleted.");
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
        label: "Task",
        render: (_value, row) => (
          <div className="task-title-cell">
            <strong>{row.title}</strong>
            {row.description ? (
              <span className="task-description-preview">{row.description}</span>
            ) : null}
            {row.related_customer_name ? (
              <span className="task-customer-chip">For {row.related_customer_name}</span>
            ) : null}
          </div>
        ),
      },
      {
        key: "status",
        label: "Status",
        render: (value) => (
          <StatusBadge status={value} tone={STATUS_TONE[value]} label={STATUS_LABEL[value] || value} />
        ),
      },
      {
        key: "priority",
        label: "Priority",
        render: (value) => (
          <StatusBadge
            status={value}
            tone={PRIORITY_TONE[value]}
            label={PRIORITY_LABEL[value] || value}
            showDot={false}
          />
        ),
      },
      {
        key: "assignee_name",
        label: "Assigned to",
        render: (_value, row) => row.assignee_name || row.assignee_email || "Unassigned",
      },
      {
        key: "due_date",
        label: "Due",
        render: (value) => formatDate(value),
      },
    ];

    if (canManage) {
      cols.push({
        key: "actions",
        label: "",
        align: "right",
        render: (_value, row) => (
          <div className="task-row-actions">
            {row.status !== "done" && row.status !== "cancelled" ? (
              <AppButton
                size="small"
                variant="secondary"
                icon={<AssignmentTurnedInOutlined fontSize="small" />}
                loading={busyTaskId === row.id}
                onClick={() => handleMarkDone(row)}
              >
                Mark done
              </AppButton>
            ) : null}
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
      <section className="tasks-page">
        <PageHeader
          eyebrow="TASKS"
          title="Tasks"
          description="Tasks, follow-ups, payments, services and internal cases assigned to the team."
        />
        <AppCard padding="large">
          <EmptyState
            icon={<LockOutlined />}
            title="You don't have access to Tasks"
            description="Ask a company administrator to grant you the “View Tasks” permission."
          />
        </AppCard>
      </section>
    );
  }

  const columns = buildColumns();

  return (
    <section className="tasks-page">
      <PageHeader
        eyebrow="TASKS"
        title="Tasks"
        description="Assign follow-ups, payments, services and internal cases to your team and track them to done."
        actions={
          <div className="task-row-actions">
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
                New task
              </AppButton>
            ) : null}
          </div>
        }
      />

      {!canManage ? (
        <p className="tasks-inline-note">
          <LockOutlined fontSize="small" /> You have read-only access. Ask an
          administrator for the &quot;Manage Tasks&quot; permission to create,
          edit or delete tasks.
        </p>
      ) : null}

      <AppCard padding="medium">
        <div className="tasks-toolbar">
          <SearchBar
            value={searchInput}
            placeholder="Search tasks by title or description..."
            ariaLabel="Search tasks"
            onChange={setSearchInput}
            onClear={() => setSearchInput("")}
          />

          <label className="tasks-filter">
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

          <label className="tasks-filter">
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
            label={`${total} task${total === 1 ? "" : "s"}`}
          />
        </div>

        {error ? (
          <ErrorState
            title="Tasks could not load"
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
            emptyTitle="No tasks found"
            emptyDescription={
              search || statusFilter !== "all" || assigneeFilter !== "all"
                ? "No tasks match your filters. Try widening your search."
                : canManage
                  ? "Create your first task to start assigning follow-ups, payments and internal cases to the team."
                  : "Tasks assigned to the team will appear here."
            }
            renderMobileCard={(row) => (
              <div className="tz-mobile-record-fields">
                <div className="task-title-cell">
                  <strong>{row.title}</strong>
                  {row.related_customer_name ? (
                    <span className="task-customer-chip">For {row.related_customer_name}</span>
                  ) : null}
                </div>
                <div className="task-mobile-meta">
                  <StatusBadge status={row.status} tone={STATUS_TONE[row.status]} label={STATUS_LABEL[row.status] || row.status} />
                  <StatusBadge status={row.priority} tone={PRIORITY_TONE[row.priority]} label={PRIORITY_LABEL[row.priority] || row.priority} showDot={false} />
                </div>
                <span>{row.assignee_name || row.assignee_email || "Unassigned"} · Due {formatDate(row.due_date)}</span>
                {canManage ? (
                  <div className="task-row-actions">
                    {row.status !== "done" && row.status !== "cancelled" ? (
                      <AppButton size="small" variant="secondary" onClick={() => handleMarkDone(row)} loading={busyTaskId === row.id}>
                        Mark done
                      </AppButton>
                    ) : null}
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
            className="tz-dialog tasks-dialog"
            role="dialog"
            aria-modal="true"
            aria-labelledby="task-editor-title"
          >
            <header className="tz-dialog-header">
              <h3 id="task-editor-title">{isEdit ? "Edit task" : "New task"}</h3>
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
              <div className="tasks-form">
                <label className="tasks-field">
                  <span>Title</span>
                  <input
                    type="text"
                    value={form.title}
                    disabled={saving}
                    placeholder="e.g. Follow up on renewal payment"
                    onChange={(event) => updateForm("title", event.target.value)}
                  />
                </label>

                <label className="tasks-field">
                  <span>Description</span>
                  <textarea
                    value={form.description}
                    disabled={saving}
                    placeholder="Notes, instructions or context for whoever picks this up"
                    onChange={(event) => updateForm("description", event.target.value)}
                  />
                </label>

                <div className="tasks-grid-3">
                  <label className="tasks-field">
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

                  <label className="tasks-field">
                    <span>Priority</span>
                    <select
                      value={form.priority}
                      disabled={saving}
                      onChange={(event) => updateForm("priority", event.target.value)}
                    >
                      {PRIORITY_OPTIONS.map((option) => (
                        <option key={option.value} value={option.value}>
                          {option.label}
                        </option>
                      ))}
                    </select>
                  </label>

                  <label className="tasks-field">
                    <span>Due date</span>
                    <input
                      type="date"
                      value={form.due_date || ""}
                      disabled={saving}
                      onChange={(event) => updateForm("due_date", event.target.value)}
                    />
                  </label>
                </div>

                <div className="tasks-grid-2">
                  <label className="tasks-field">
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

                  <label className="tasks-field">
                    <span>Related customer (optional)</span>
                    <select
                      value={form.related_customer_id}
                      disabled={saving}
                      onChange={(event) => updateForm("related_customer_id", event.target.value)}
                    >
                      <option value="">None</option>
                      {filteredCustomerOptions.map((customer) => (
                        <option key={customer.id} value={customer.id}>
                          {customer.display_name || customer.internal_name || `Customer ${customer.id}`}
                        </option>
                      ))}
                    </select>
                  </label>
                </div>

                {customers.length > 8 ? (
                  <label className="tasks-field">
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
                  <p className="tasks-meta-line">
                    Created {formatDateTime(activeTaskCreatedAt)}
                  </p>
                ) : null}

                {formError ? <p className="tasks-form-error">{formError}</p> : null}
              </div>
            </div>

            <footer className="tz-dialog-actions">
              <AppButton variant="secondary" disabled={saving} onClick={closeEditor}>
                Cancel
              </AppButton>
              <AppButton variant="primary" loading={saving} onClick={handleSave}>
                {isEdit ? "Save changes" : "Create task"}
              </AppButton>
            </footer>
          </section>
        </div>
      ) : null}

      <ConfirmDialog
        open={Boolean(deleteTarget)}
        title="Delete task"
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
