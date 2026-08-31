import { useCallback, useEffect, useMemo, useState } from "react";
import {
  AddOutlined,
  CloseOutlined,
  RefreshOutlined,
} from "@mui/icons-material";

import {
  addTaskCommentRequest,
  assignTaskRequest,
  changeTaskStatusRequest,
  createTaskRequest,
  getTaskOptionsRequest,
  getTaskRequest,
  getTaskSummaryRequest,
  getTasksRequest,
  updateTaskRequest,
} from "../../api/tasks";
import {
  AppButton,
  AppCard,
  AppTable,
  ErrorState,
  LoadingState,
  PageHeader,
  SearchBar,
  StatusBadge,
} from "../../components/common";
import { formatPlatformDateTime } from "../../utils/dateTime";
import "./TasksPage.css";

const PAGE_SIZE = 20;

const UNASSIGNED = "unassigned";

// The tile a count is shown in, and what clicking it filters the list down to.
const SUMMARY_TILES = [
  ["total", "All tasks", { status: "", overdue: "" }],
  ["open", "Open", { status: "open", overdue: "" }],
  ["in_progress", "In progress", { status: "in_progress", overdue: "" }],
  ["overdue", "Overdue", { status: "", overdue: "true" }],
  ["resolved", "Resolved", { status: "resolved", overdue: "" }],
  ["closed", "Closed", { status: "closed", overdue: "" }],
];

const STATUS_TONES = {
  open: "info",
  in_progress: "warning",
  resolved: "success",
  closed: "neutral",
};

const PRIORITY_TONES = {
  low: "neutral",
  normal: "info",
  high: "warning",
  urgent: "danger",
};

function humanize(value) {
  return String(value ?? "")
    .replaceAll("_", " ")
    .replace(/\b\w/g, (letter) => letter.toUpperCase());
}

function emptyForm() {
  return {
    title: "",
    problem: "",
    task_type: "task",
    priority: "normal",
    status: "open",
    due_date: "",
    assigned_user_id: "",
    department: "",
  };
}

function formFromTask(task) {
  const form = emptyForm();

  Object.keys(form).forEach((key) => {
    const value = task?.[key];
    form[key] = value === null || value === undefined ? "" : String(value);
  });

  // The date input only understands YYYY-MM-DD; the server stores the moment
  // the task is actually late, which is the end of that day.
  form.due_date = form.due_date ? form.due_date.slice(0, 10) : "";
  return form;
}

function payloadFromForm(form) {
  return {
    title: form.title.trim(),
    problem: form.problem.trim() || null,
    task_type: form.task_type || "task",
    priority: form.priority || "normal",
    status: form.status || "open",
    due_date: form.due_date || null,
    assigned_user_id: form.assigned_user_id
      ? Number(form.assigned_user_id)
      : null,
    department: form.department.trim() || null,
  };
}

export default function TasksPage() {
  const [searchInput, setSearchInput] = useState("");
  const [search, setSearch] = useState("");
  const [status, setStatus] = useState("");
  const [taskType, setTaskType] = useState("");
  const [priority, setPriority] = useState("");
  const [assignee, setAssignee] = useState("");
  const [overdue, setOverdue] = useState("");
  const [mine, setMine] = useState(false);
  const [page, setPage] = useState(1);

  const [rows, setRows] = useState([]);
  const [total, setTotal] = useState(0);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState("");

  const [summary, setSummary] = useState({});
  const [options, setOptions] = useState({
    statuses: [],
    priorities: [],
    task_types: [],
    employees: [],
  });

  const [editorOpen, setEditorOpen] = useState(false);
  const [selectedId, setSelectedId] = useState(null);
  const [form, setForm] = useState(emptyForm);
  const [detailLoading, setDetailLoading] = useState(false);
  const [detailError, setDetailError] = useState("");
  const [saving, setSaving] = useState(false);
  const [saveStatus, setSaveStatus] = useState("");
  const [statusSaving, setStatusSaving] = useState(false);

  const [comments, setComments] = useState([]);
  const [commentBody, setCommentBody] = useState("");
  const [commentSaving, setCommentSaving] = useState(false);
  const [commentError, setCommentError] = useState("");

  const loadTasks = useCallback(async () => {
    setLoading(true);
    setError("");

    try {
      const result = await getTasksRequest({
        status,
        taskType,
        priority,
        assignee: assignee && assignee !== UNASSIGNED ? Number(assignee) : "",
        unassigned: assignee === UNASSIGNED,
        overdue: overdue === "" ? null : overdue === "true",
        mine,
        search,
        limit: PAGE_SIZE,
        offset: (page - 1) * PAGE_SIZE,
      });

      setRows(Array.isArray(result?.items) ? result.items : []);
      setTotal(Number(result?.total || 0));
    } catch (requestError) {
      setError(requestError.message || "Tasks could not be loaded.");
      setRows([]);
      setTotal(0);
    } finally {
      setLoading(false);
    }
  }, [status, taskType, priority, assignee, overdue, mine, search, page]);

  const loadSummary = useCallback(async () => {
    try {
      setSummary(await getTaskSummaryRequest({ mine }));
    } catch {
      // The tiles are a headline, not the screen. Losing them must not blank
      // the task list underneath.
      setSummary({});
    }
  }, [mine]);

  const loadOptions = useCallback(async () => {
    try {
      const result = await getTaskOptionsRequest();

      setOptions({
        statuses: Array.isArray(result?.statuses) ? result.statuses : [],
        priorities: Array.isArray(result?.priorities) ? result.priorities : [],
        task_types: Array.isArray(result?.task_types) ? result.task_types : [],
        employees: Array.isArray(result?.employees) ? result.employees : [],
      });
    } catch (requestError) {
      setError(
        requestError.message ||
          "The filters and the employee list could not be loaded.",
      );
    }
  }, []);

  useEffect(() => {
    loadTasks();
  }, [loadTasks]);

  useEffect(() => {
    loadSummary();
  }, [loadSummary]);

  useEffect(() => {
    loadOptions();
  }, [loadOptions]);

  useEffect(() => {
    const timeout = window.setTimeout(() => {
      setSearch(searchInput.trim());
      setPage(1);
    }, 300);

    return () => window.clearTimeout(timeout);
  }, [searchInput]);

  const employeeNames = useMemo(() => {
    const map = new Map();
    options.employees.forEach((employee) => {
      map.set(Number(employee.id), employee.display_name);
    });
    return map;
  }, [options.employees]);

  const assigneeLabel = useCallback(
    (task) => {
      if (!task?.assigned_user_id) {
        return "Unassigned";
      }

      // The server resolves the name from the control database; the employee
      // list is only the fallback for a row that arrived before it loaded.
      return (
        task.assigned_user_name ||
        employeeNames.get(Number(task.assigned_user_id)) ||
        `User #${task.assigned_user_id}`
      );
    },
    [employeeNames],
  );

  const handleStatusChange = useCallback(
    async (taskId, nextStatus) => {
      setStatusSaving(true);
      setDetailError("");

      try {
        const saved = await changeTaskStatusRequest(taskId, nextStatus);

        if (selectedId === taskId) {
          setForm(formFromTask(saved));
          setSaveStatus(`Moved to ${humanize(nextStatus)}.`);
        }

        await Promise.all([loadTasks(), loadSummary()]);
      } catch (requestError) {
        const message =
          requestError.message || "The status could not be changed.";
        setDetailError(message);
        setError(message);
      } finally {
        setStatusSaving(false);
      }
    },
    [selectedId, loadTasks, loadSummary],
  );

  const loadComments = useCallback(async (taskId) => {
    try {
      const result = await getTaskRequest(taskId);
      setComments(Array.isArray(result?.comments) ? result.comments : []);
      return result;
    } catch (requestError) {
      setComments([]);
      throw requestError;
    }
  }, []);

  const openTask = useCallback(
    async (taskId) => {
      setEditorOpen(true);
      setSelectedId(taskId);
      setDetailLoading(true);
      setDetailError("");
      setSaveStatus("");
      setCommentBody("");
      setCommentError("");

      try {
        const task = await loadComments(taskId);
        setForm(formFromTask(task));
      } catch (requestError) {
        setDetailError(requestError.message || "This task could not be loaded.");
      } finally {
        setDetailLoading(false);
      }
    },
    [loadComments],
  );

  const openCreate = useCallback(() => {
    setEditorOpen(true);
    setSelectedId(null);
    setForm(emptyForm());
    setComments([]);
    setDetailError("");
    setSaveStatus("");
    setCommentBody("");
    setCommentError("");
  }, []);

  function closeEditor() {
    setEditorOpen(false);
    setSelectedId(null);
    setForm(emptyForm());
    setComments([]);
    setDetailError("");
    setSaveStatus("");
  }

  function updateField(key, value) {
    setSaveStatus("");
    setForm((current) => ({ ...current, [key]: value }));
  }

  function applyTileFilter(filter) {
    setStatus(filter.status);
    setOverdue(filter.overdue);
    setPage(1);
  }

  async function refreshAll() {
    await Promise.all([loadTasks(), loadSummary(), loadOptions()]);
  }

  async function handleSave(event) {
    event.preventDefault();

    const payload = payloadFromForm(form);

    if (!payload.title) {
      setDetailError("Give this task a title.");
      return;
    }

    setSaving(true);
    setDetailError("");
    setSaveStatus("");

    try {
      const saved = selectedId
        ? await updateTaskRequest(selectedId, payload)
        : await createTaskRequest(payload);

      setSelectedId(saved.id);
      setForm(formFromTask(saved));
      setSaveStatus("Task saved.");
      await Promise.all([loadTasks(), loadSummary()]);
    } catch (requestError) {
      setDetailError(requestError.message || "This task could not be saved.");
    } finally {
      setSaving(false);
    }
  }

  async function handleAssign(taskId, userId) {
    setStatusSaving(true);
    setDetailError("");

    try {
      const saved = await assignTaskRequest(taskId, userId);

      if (selectedId === taskId) {
        setForm(formFromTask(saved));
        setSaveStatus(
          saved.assigned_user_id
            ? `Assigned to ${assigneeLabel(saved)}.`
            : "Assignment cleared.",
        );
      }

      await Promise.all([loadTasks(), loadSummary()]);
    } catch (requestError) {
      setDetailError(
        requestError.message || "This task could not be assigned.",
      );
    } finally {
      setStatusSaving(false);
    }
  }

  async function handleAddComment(event) {
    event.preventDefault();

    if (!selectedId) {
      return;
    }

    const body = commentBody.trim();

    if (!body) {
      setCommentError("Write something before posting the comment.");
      return;
    }

    setCommentSaving(true);
    setCommentError("");

    try {
      const comment = await addTaskCommentRequest(selectedId, body);
      setComments((current) => [...current, comment]);
      setCommentBody("");
    } catch (requestError) {
      setCommentError(
        requestError.message || "The comment could not be posted.",
      );
    } finally {
      setCommentSaving(false);
    }
  }

  const columns = useMemo(
    () => [
      {
        key: "title",
        label: "Task",
        render: (value, row) => (
          <button
            type="button"
            className="task-title-button"
            onClick={() => openTask(row.id)}
          >
            <strong>{row.title || `Task #${row.id}`}</strong>
            <span>{humanize(row.task_type)}</span>
          </button>
        ),
      },
      {
        key: "assigned_user_id",
        label: "Assignee",
        render: (value, row) => (
          <span
            className={row.assigned_user_id ? "" : "task-unassigned"}
          >
            {assigneeLabel(row)}
          </span>
        ),
      },
      {
        key: "due_date",
        label: "Due",
        render: (value, row) =>
          value ? (
            <span
              className={`task-due ${row.is_overdue ? "is-overdue" : ""}`}
            >
              {formatPlatformDateTime(value)}
              {row.is_overdue ? <small>Overdue</small> : null}
            </span>
          ) : (
            "—"
          ),
      },
      {
        key: "priority",
        label: "Priority",
        render: (value) => (
          <StatusBadge
            status={value}
            label={humanize(value)}
            tone={PRIORITY_TONES[value] || "neutral"}
          />
        ),
      },
      {
        key: "status",
        label: "Status",
        render: (value) => (
          <StatusBadge
            status={value}
            label={humanize(value)}
            tone={STATUS_TONES[value] || "neutral"}
          />
        ),
      },
      {
        key: "updated_at",
        label: "Updated",
        render: (value) => formatPlatformDateTime(value),
      },
      {
        key: "actions",
        label: "",
        align: "right",
        render: (value, row) => (
          <div className="task-row-actions">
            <AppButton
              variant="ghost"
              size="small"
              onClick={() => openTask(row.id)}
            >
              Open
            </AppButton>

            {row.status !== "closed" ? (
              <AppButton
                variant="ghost"
                size="small"
                disabled={statusSaving}
                onClick={() =>
                  handleStatusChange(
                    row.id,
                    row.status === "open" ? "in_progress" : "resolved",
                  )
                }
              >
                {row.status === "open" ? "Start" : "Resolve"}
              </AppButton>
            ) : null}
          </div>
        ),
      },
    ],
    [openTask, statusSaving, assigneeLabel, handleStatusChange],
  );

  const filtersActive = Boolean(
    search || status || taskType || priority || assignee || overdue || mine,
  );

  return (
    <div className="tasks-page">
      <PageHeader
        eyebrow="TEAM WORK"
        title="Tasks"
        description="Everything this company owes somebody: support escalations from the assistant and the follow-ups the team writes down, with one owner and one deadline each."
        actions={
          <>
            <AppButton
              variant="secondary"
              icon={<RefreshOutlined fontSize="small" />}
              onClick={refreshAll}
            >
              Refresh
            </AppButton>

            <AppButton
              variant="primary"
              icon={<AddOutlined fontSize="small" />}
              onClick={openCreate}
            >
              New task
            </AppButton>
          </>
        }
      />

      <div className="tasks-summary">
        {SUMMARY_TILES.map(([key, label, filter]) => (
          <button
            type="button"
            key={key}
            className={`tasks-summary-tile ${
              key === "overdue" ? "is-overdue" : ""
            } ${
              status === filter.status && overdue === filter.overdue
                ? "is-active"
                : ""
            }`}
            onClick={() => applyTileFilter(filter)}
          >
            <span>{label}</span>
            <strong>{Number(summary?.[key] || 0)}</strong>
          </button>
        ))}
      </div>

      <div className={`tasks-layout ${editorOpen ? "has-editor" : ""}`}>
        <AppCard padding="medium" className="tasks-list-card">
          <div className="tasks-toolbar">
            <SearchBar
              value={searchInput}
              placeholder="Search title, details, department or customer id..."
              ariaLabel="Search tasks"
              onChange={setSearchInput}
            />

            <label className="tasks-filter" htmlFor="tasks-status-filter">
              <span>Status</span>

              <select
                id="tasks-status-filter"
                value={status}
                onChange={(event) => {
                  setStatus(event.target.value);
                  setPage(1);
                }}
              >
                <option value="">All statuses</option>

                {options.statuses.map((name) => (
                  <option key={name} value={name}>
                    {humanize(name)}
                  </option>
                ))}
              </select>
            </label>

            <label className="tasks-filter" htmlFor="tasks-type-filter">
              <span>Type</span>

              <select
                id="tasks-type-filter"
                value={taskType}
                onChange={(event) => {
                  setTaskType(event.target.value);
                  setPage(1);
                }}
              >
                <option value="">All types</option>

                {options.task_types.map((name) => (
                  <option key={name} value={name}>
                    {humanize(name)}
                  </option>
                ))}
              </select>
            </label>

            <label className="tasks-filter" htmlFor="tasks-priority-filter">
              <span>Priority</span>

              <select
                id="tasks-priority-filter"
                value={priority}
                onChange={(event) => {
                  setPriority(event.target.value);
                  setPage(1);
                }}
              >
                <option value="">Any priority</option>

                {options.priorities.map((name) => (
                  <option key={name} value={name}>
                    {humanize(name)}
                  </option>
                ))}
              </select>
            </label>

            <label className="tasks-filter" htmlFor="tasks-assignee-filter">
              <span>Assignee</span>

              <select
                id="tasks-assignee-filter"
                value={assignee}
                disabled={mine}
                onChange={(event) => {
                  setAssignee(event.target.value);
                  setPage(1);
                }}
              >
                <option value="">Anyone</option>
                <option value={UNASSIGNED}>Unassigned</option>

                {options.employees.map((employee) => (
                  <option key={employee.id} value={String(employee.id)}>
                    {employee.display_name}
                  </option>
                ))}
              </select>
            </label>

            <label className="tasks-filter" htmlFor="tasks-overdue-filter">
              <span>Deadline</span>

              <select
                id="tasks-overdue-filter"
                value={overdue}
                onChange={(event) => {
                  setOverdue(event.target.value);
                  setPage(1);
                }}
              >
                <option value="">Any deadline</option>
                <option value="true">Overdue only</option>
                <option value="false">Not overdue</option>
              </select>
            </label>

            <AppButton
              variant={mine ? "primary" : "secondary"}
              size="small"
              onClick={() => {
                setMine((current) => !current);
                setPage(1);
              }}
            >
              {mine ? "My tasks: on" : "My tasks"}
            </AppButton>

            <span className="tasks-total">
              {total} {total === 1 ? "task" : "tasks"}
            </span>
          </div>

          {error ? (
            <ErrorState
              title="Tasks could not load"
              description={error}
              action={
                <AppButton variant="primary" onClick={loadTasks}>
                  Try again
                </AppButton>
              }
            />
          ) : (
            <AppTable
              columns={columns}
              rows={rows}
              loading={loading}
              emptyTitle="No tasks"
              emptyDescription={
                filtersActive
                  ? "No task matches these filters."
                  : "Nothing is outstanding. Create a task to give a piece of work an owner and a deadline."
              }
              page={page}
              pageSize={PAGE_SIZE}
              totalRows={total}
              onPageChange={setPage}
              renderMobileCard={(row) => (
                <button
                  type="button"
                  className={`task-mobile-card ${
                    row.is_overdue ? "is-overdue" : ""
                  }`}
                  onClick={() => openTask(row.id)}
                >
                  <strong>{row.title || `Task #${row.id}`}</strong>
                  <span>{assigneeLabel(row)}</span>
                  <small>
                    {humanize(row.status)} ·{" "}
                    {row.due_date
                      ? `due ${formatPlatformDateTime(row.due_date)}`
                      : "no deadline"}
                    {row.is_overdue ? " · overdue" : ""}
                  </small>
                </button>
              )}
            />
          )}
        </AppCard>

        {editorOpen ? (
          <AppCard padding="medium" className="tasks-editor-card">
            <header className="tasks-editor-head">
              <div>
                <span>{selectedId ? "EDIT TASK" : "NEW TASK"}</span>
                <h3>{form.title || "Untitled task"}</h3>
              </div>

              <button
                type="button"
                className="tasks-editor-close"
                aria-label="Close task editor"
                onClick={closeEditor}
              >
                <CloseOutlined fontSize="small" />
              </button>
            </header>

            {detailLoading ? <LoadingState title="Loading task..." /> : null}

            {!detailLoading ? (
              <>
                {selectedId ? (
                  <div className="tasks-transitions">
                    <span>Move to</span>

                    <div>
                      {options.statuses
                        .filter((name) => name !== form.status)
                        .map((name) => (
                          <AppButton
                            key={name}
                            variant="secondary"
                            size="small"
                            disabled={statusSaving}
                            onClick={() =>
                              handleStatusChange(selectedId, name)
                            }
                          >
                            {humanize(name)}
                          </AppButton>
                        ))}
                    </div>
                  </div>
                ) : null}

                <form className="tasks-form" onSubmit={handleSave}>
                  <label htmlFor="task-title">
                    <span>Title</span>

                    <input
                      id="task-title"
                      type="text"
                      value={form.title}
                      maxLength={200}
                      placeholder="Call the customer back about the refund"
                      onChange={(event) =>
                        updateField("title", event.target.value)
                      }
                    />
                  </label>

                  <div className="tasks-form-grid">
                    <label htmlFor="task-type">
                      <span>Type</span>

                      <select
                        id="task-type"
                        value={form.task_type}
                        onChange={(event) =>
                          updateField("task_type", event.target.value)
                        }
                      >
                        {options.task_types.map((name) => (
                          <option key={name} value={name}>
                            {humanize(name)}
                          </option>
                        ))}
                      </select>
                    </label>

                    <label htmlFor="task-priority">
                      <span>Priority</span>

                      <select
                        id="task-priority"
                        value={form.priority}
                        onChange={(event) =>
                          updateField("priority", event.target.value)
                        }
                      >
                        {options.priorities.map((name) => (
                          <option key={name} value={name}>
                            {humanize(name)}
                          </option>
                        ))}
                      </select>
                    </label>

                    <label htmlFor="task-status">
                      <span>Status</span>

                      <select
                        id="task-status"
                        value={form.status}
                        onChange={(event) =>
                          updateField("status", event.target.value)
                        }
                      >
                        {options.statuses.map((name) => (
                          <option key={name} value={name}>
                            {humanize(name)}
                          </option>
                        ))}
                      </select>
                    </label>

                    <label htmlFor="task-due-date">
                      <span>Due date</span>

                      <input
                        id="task-due-date"
                        type="date"
                        value={form.due_date}
                        onChange={(event) =>
                          updateField("due_date", event.target.value)
                        }
                      />
                    </label>

                    <label htmlFor="task-assignee">
                      <span>Assignee</span>

                      <select
                        id="task-assignee"
                        value={form.assigned_user_id}
                        onChange={(event) => {
                          updateField("assigned_user_id", event.target.value);

                          if (selectedId) {
                            handleAssign(selectedId, event.target.value);
                          }
                        }}
                      >
                        <option value="">Unassigned</option>

                        {options.employees.map((employee) => (
                          <option
                            key={employee.id}
                            value={String(employee.id)}
                          >
                            {employee.display_name}
                            {employee.role_name
                              ? ` — ${employee.role_name}`
                              : ""}
                          </option>
                        ))}
                      </select>
                    </label>

                    <label htmlFor="task-department">
                      <span>Department</span>

                      <input
                        id="task-department"
                        type="text"
                        value={form.department}
                        maxLength={60}
                        placeholder="support"
                        onChange={(event) =>
                          updateField("department", event.target.value)
                        }
                      />
                    </label>
                  </div>

                  <p className="tasks-form-hint">
                    A task with a due date is counted as overdue from the end of
                    that day until somebody resolves or closes it.
                  </p>

                  <label htmlFor="task-problem">
                    <span>Details</span>

                    <textarea
                      id="task-problem"
                      rows={5}
                      value={form.problem}
                      maxLength={4000}
                      placeholder="What has to happen, and anything the next person needs to know."
                      onChange={(event) =>
                        updateField("problem", event.target.value)
                      }
                    />
                  </label>

                  <footer className="tasks-form-footer">
                    <span className={detailError ? "is-error" : "is-success"}>
                      {detailError || saveStatus}
                    </span>

                    <div>
                      <AppButton
                        variant="secondary"
                        disabled={saving}
                        onClick={closeEditor}
                      >
                        Cancel
                      </AppButton>

                      <AppButton
                        type="submit"
                        variant="primary"
                        loading={saving}
                      >
                        {selectedId ? "Save task" : "Create task"}
                      </AppButton>
                    </div>
                  </footer>
                </form>

                {selectedId ? (
                  <section className="tasks-comments">
                    <h4>Comments</h4>

                    {comments.length ? (
                      <ul>
                        {comments.map((comment) => (
                          <li key={comment.id}>
                            <header>
                              <strong>
                                {comment.author_name || "Removed employee"}
                              </strong>

                              <small>
                                {formatPlatformDateTime(comment.created_at)}
                              </small>
                            </header>

                            <p>{comment.body}</p>
                          </li>
                        ))}
                      </ul>
                    ) : (
                      <p className="tasks-comments-empty">
                        No comments yet. Anything decided about this task
                        belongs here, where the next person will find it.
                      </p>
                    )}

                    <form
                      className="tasks-comment-form"
                      onSubmit={handleAddComment}
                    >
                      <label htmlFor="task-comment">
                        <span>Add a comment</span>

                        <textarea
                          id="task-comment"
                          rows={3}
                          value={commentBody}
                          maxLength={4000}
                          placeholder="Called the customer, waiting for the replacement device."
                          onChange={(event) => {
                            setCommentError("");
                            setCommentBody(event.target.value);
                          }}
                        />
                      </label>

                      <div className="tasks-comment-actions">
                        {commentError ? (
                          <span className="is-error">{commentError}</span>
                        ) : (
                          <span />
                        )}

                        <AppButton
                          type="submit"
                          variant="primary"
                          size="small"
                          loading={commentSaving}
                        >
                          Post comment
                        </AppButton>
                      </div>
                    </form>
                  </section>
                ) : null}
              </>
            ) : null}
          </AppCard>
        ) : null}
      </div>
    </div>
  );
}
