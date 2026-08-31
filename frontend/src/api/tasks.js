import { apiRequest } from "./client";

/*
 * The tasks API.
 *
 * No call here sends a company id. The server reads it from the bearer token,
 * so a tampered request body cannot reach another company's task list.
 */

function queryString(params) {
  const search = new URLSearchParams();

  Object.entries(params).forEach(([key, value]) => {
    if (
      value === undefined ||
      value === null ||
      value === "" ||
      value === false
    ) {
      return;
    }

    search.append(key, String(value));
  });

  const query = search.toString();
  return query ? `?${query}` : "";
}

export async function getTasksRequest({
  status = "",
  taskType = "",
  priority = "",
  assignee = "",
  unassigned = false,
  overdue = null,
  mine = false,
  search = "",
  limit = 20,
  offset = 0,
} = {}) {
  const query = queryString({
    status,
    task_type: taskType,
    priority,
    assignee,
    unassigned,
    // `overdue` is a three-state filter: unset means "no opinion", so only a
    // real boolean is sent.
    overdue: overdue === null ? "" : String(overdue),
    mine,
    search,
    limit,
    offset,
  });

  return apiRequest(`/api/tasks${query}`);
}

export async function getTaskOptionsRequest() {
  return apiRequest("/api/tasks/options");
}

export async function getTaskSummaryRequest({ mine = false } = {}) {
  return apiRequest(`/api/tasks/summary${queryString({ mine })}`);
}

export async function getTaskRequest(taskId) {
  return apiRequest(`/api/tasks/${encodeURIComponent(taskId)}`);
}

export async function createTaskRequest(values) {
  return apiRequest("/api/tasks", {
    method: "POST",
    body: values,
  });
}

export async function updateTaskRequest(taskId, values) {
  return apiRequest(`/api/tasks/${encodeURIComponent(taskId)}`, {
    method: "PUT",
    body: values,
  });
}

export async function changeTaskStatusRequest(taskId, status) {
  return apiRequest(`/api/tasks/${encodeURIComponent(taskId)}/status`, {
    method: "PATCH",
    body: { status },
  });
}

export async function assignTaskRequest(taskId, assignedUserId) {
  return apiRequest(`/api/tasks/${encodeURIComponent(taskId)}/assign`, {
    method: "POST",
    body: {
      assigned_user_id: assignedUserId ? Number(assignedUserId) : null,
    },
  });
}

export async function getTaskCommentsRequest(taskId) {
  return apiRequest(`/api/tasks/${encodeURIComponent(taskId)}/comments`);
}

export async function addTaskCommentRequest(taskId, body) {
  return apiRequest(`/api/tasks/${encodeURIComponent(taskId)}/comments`, {
    method: "POST",
    body: { body },
  });
}
