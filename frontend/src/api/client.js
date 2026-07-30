
const API_BASE_URL =
  import.meta.env.VITE_API_BASE_URL ||
  "http://127.0.0.1:8000";

const TOKEN_KEY = "tzone_access_token";

export function getAccessToken() {
  return localStorage.getItem(TOKEN_KEY);
}

export function saveAccessToken(token) {
  if (token) {
    localStorage.setItem(TOKEN_KEY, token);
  }
}

export function clearAccessToken() {
  localStorage.removeItem(TOKEN_KEY);
}

async function parseResponse(response) {
  const contentType =
    response.headers.get("content-type") || "";

  if (!contentType.includes("application/json")) {
    return null;
  }

  try {
    return await response.json();
  } catch {
    return null;
  }
}

function resolveApiErrorMessage(data, status) {
  const detail = data?.detail;
  if (typeof detail === "string" && detail.trim()) return detail;
  if (detail && typeof detail === "object" && !Array.isArray(detail)) {
    const nested = detail.message || detail.detail || detail.error;
    if (typeof nested === "string" && nested.trim()) return nested;
  }
  if (Array.isArray(detail)) {
    const messages = detail
      .map((item) => item?.msg || item?.message || item)
      .filter((item) => typeof item === "string" && item.trim());
    if (messages.length) return messages.join(" · ");
  }
  if (typeof data?.message === "string" && data.message.trim()) return data.message;
  return `Request failed with status ${status}.`;
}

export async function apiRequest(
  path,
  {
    method = "GET",
    body = null,
    authenticated = true,
    headers: customHeaders = {},
    signal,
  } = {},
) {
  const headers = {
    Accept: "application/json",
    ...customHeaders,
  };

  if (body !== null) {
    headers["Content-Type"] = "application/json";
  }

  if (authenticated) {
    const token = getAccessToken();

    if (token) {
      headers.Authorization = `Bearer ${token}`;
    }
  }

  let response;

  try {
    response = await fetch(`${API_BASE_URL}${path}`, {
      method,
      headers,
      signal,
      body: body !== null
        ? JSON.stringify(body)
        : undefined,
    });
  } catch (error) {
    if (error?.name === "AbortError") {
      throw error;
    }

    throw new Error(
      "Cannot connect to the T-ZONE server. Make sure FastAPI is running on port 8000.",
    );
  }

  const data = await parseResponse(response);

  if (!response.ok) {
    const message = resolveApiErrorMessage(data, response.status);

    const error = new Error(message);
    error.status = response.status;
    error.data = data;
    throw error;
  }

  return data;
}

function createQueryString(parameters) {
  const searchParameters = new URLSearchParams();

  Object.entries(parameters).forEach(([key, value]) => {
    if (
      value === undefined ||
      value === null ||
      value === ""
    ) {
      return;
    }

    searchParameters.set(key, String(value));
  });

  const value = searchParameters.toString();
  return value ? `?${value}` : "";
}

function conversationPath(channel, userId) {
  return (
    "/conversations/" +
    `${encodeURIComponent(channel)}/` +
    `${encodeURIComponent(userId)}`
  );
}

export async function loginRequest(company, email, password) {
  return apiRequest("/api/auth/login", {
    method: "POST",
    authenticated: false,
    body: { company, email, password },
  });
}

export async function sendVerificationCodeRequest(purpose) {
  return apiRequest("/api/security/send-code", { method: "POST", body: { purpose } });
}

export async function verifyCodeRequest(purpose, code) {
  return apiRequest("/api/security/verify-code", { method: "POST", body: { purpose, code } });
}

export async function getSessionChangesRequest(purpose, token) {
  return apiRequest(`/api/security/changes?purpose=${encodeURIComponent(purpose)}&token=${encodeURIComponent(token)}`);
}

export async function getPlansCatalogRequest() {
  return apiRequest("/api/platform/plans-catalog");
}

export async function requestPlanChangeRequest(planId, note) {
  return apiRequest("/api/platform/subscription-requests", { method: "POST", body: { plan_id: planId, note } });
}

export async function getMySubscriptionRequestsRequest() {
  return apiRequest("/api/platform/my-subscription-requests");
}

export async function listSubscriptionRequestsRequest(status) {
  return apiRequest(`/api/platform/subscription-requests${status ? `?status=${status}` : ""}`);
}

export async function reviewSubscriptionRequestRequest(requestId, approve) {
  return apiRequest(`/api/platform/subscription-requests/${requestId}/review`, { method: "POST", body: { approve } });
}

export async function updatePlatformCompanyModulesRequest(companyId, modules) {
  return apiRequest(`/api/platform/companies/${companyId}/modules`, { method: "PATCH", body: modules });
}

export async function getMyModulesRequest() {
  return apiRequest("/api/platform/my-modules");
}

export async function listInstructionsRequest() {
  return apiRequest("/api/instructions");
}

export async function createInstructionRequest(text, tags) {
  return apiRequest("/api/instructions", { method: "POST", body: { text, tags: tags || [] } });
}

export async function updateInstructionRequest(id, text, tags) {
  return apiRequest(`/api/instructions/${id}`, { method: "PATCH", body: { text, tags } });
}

export async function deleteInstructionRequest(id) {
  return apiRequest(`/api/instructions/${id}`, { method: "DELETE" });
}

export async function reorderInstructionsRequest(orderedIds) {
  return apiRequest("/api/instructions/reorder", { method: "POST", body: { ordered_ids: orderedIds } });
}

export async function listDepartmentsRequest() {
  return apiRequest("/api/departments");
}

export async function createDepartmentRequest(name) {
  return apiRequest("/api/departments", { method: "POST", body: { name } });
}

export async function deleteDepartmentRequest(name) {
  return apiRequest(`/api/departments/${encodeURIComponent(name)}`, { method: "DELETE" });
}

export async function customerOptionsRequest() {
  return apiRequest("/api/customers/options");
}

export async function listCustomersRequest({ search, lifecycleStage, tag, assignedUserId, segmentId, limit, offset } = {}) {
  const query = createQueryString({
    search,
    lifecycle_stage: lifecycleStage,
    tag,
    assigned_user_id: assignedUserId,
    segment_id: segmentId,
    limit,
    offset,
  });
  return apiRequest(`/api/customers${query}`);
}

export async function getCustomerRequest(customerId) {
  return apiRequest(`/api/customers/${customerId}`);
}

export async function createCustomerRequest(payload) {
  return apiRequest("/api/customers", { method: "POST", body: payload });
}

export async function bulkUpdateCustomersRequest(payload) {
  return apiRequest("/api/customers/bulk-update", { method: "POST", body: payload });
}

export async function updateCustomerRequest(customerId, updates) {
  return apiRequest(`/api/customers/${customerId}`, { method: "PUT", body: updates });
}

export async function getCustomerTimelineRequest(customerId) {
  return apiRequest(`/api/customers/${customerId}/timeline`);
}

export async function listCustomerSegmentsRequest() {
  return apiRequest("/api/customer-segments");
}

export async function createCustomerSegmentRequest(name, filters) {
  return apiRequest("/api/customer-segments", { method: "POST", body: { name, filters: filters || {} } });
}

export async function deleteCustomerSegmentRequest(segmentId) {
  return apiRequest(`/api/customer-segments/${segmentId}`, { method: "DELETE" });
}

export async function taskOptionsRequest() {
  return apiRequest("/api/tasks/options");
}

export async function listTasksRequest({ status, assignedUserId, customerId } = {}) {
  const query = createQueryString({
    status,
    assigned_user_id: assignedUserId,
    customer_id: customerId,
  });
  return apiRequest(`/api/tasks${query}`);
}

export async function createTaskRequest(payload) {
  return apiRequest("/api/tasks", { method: "POST", body: payload });
}

export async function updateTaskRequest(taskId, updates) {
  return apiRequest(`/api/tasks/${taskId}`, { method: "PUT", body: updates });
}

export async function deleteTaskRequest(taskId) {
  return apiRequest(`/api/tasks/${taskId}`, { method: "DELETE" });
}

export async function getAnalyticsSummaryRequest() {
  return apiRequest("/api/analytics");
}

export async function listKnowledgeEntriesRequest() {
  return apiRequest("/api/knowledge");
}

export async function createKnowledgeEntryRequest(title, content, department, tags) {
  return apiRequest("/api/knowledge", { method: "POST", body: { title, content, department, tags: tags || [] } });
}

export async function updateKnowledgeEntryRequest(id, title, content, department, tags) {
  return apiRequest(`/api/knowledge/${id}`, { method: "PATCH", body: { title, content, department, tags } });
}

export async function deleteKnowledgeEntryRequest(id) {
  return apiRequest(`/api/knowledge/${id}`, { method: "DELETE" });
}

export async function listSavedRepliesRequest() {
  return apiRequest("/api/saved-replies");
}

export async function createSavedReplyRequest(title, body) {
  return apiRequest("/api/saved-replies", { method: "POST", body: { title, body } });
}

export async function updateSavedReplyRequest(id, title, body) {
  return apiRequest(`/api/saved-replies/${id}`, { method: "PATCH", body: { title, body } });
}

export async function deleteSavedReplyRequest(id) {
  return apiRequest(`/api/saved-replies/${id}`, { method: "DELETE" });
}

export async function setConversationReminderRequest(channel, userId, reminderAt, note, autoSend, messageText) {
  return apiRequest(`/conversations/${channel}/${userId}/reminder`, {
    method: "POST",
    body: {
      reminder_at: reminderAt,
      note: note || null,
      auto_send: Boolean(autoSend),
      message_text: messageText || null,
    },
  });
}

export async function clearConversationReminderRequest(channel, userId) {
  return apiRequest(`/conversations/${channel}/${userId}/reminder`, { method: "DELETE" });
}

export async function getMySubscriptionRequest() {
  return apiRequest("/api/platform/my-subscription");
}

export async function startFacebookOAuthRequest() {
  return apiRequest("/api/channels/facebook/oauth/start");
}

export async function listMyChannelsRequest() {
  return apiRequest("/api/channels");
}

export async function connectTelegramRequest(botToken, name, elevatedToken) {
  return apiRequest("/api/channels/telegram/connect", {
    method: "POST",
    body: { bot_token: botToken, name: name || null },
    headers: { "X-Elevated-Token": elevatedToken },
  });
}

export async function connectWhatsAppRequest(phoneNumberId, accessToken, name, elevatedToken) {
  return apiRequest("/api/channels/whatsapp/connect", {
    method: "POST",
    body: { phone_number_id: phoneNumberId, access_token: accessToken, name: name || null },
    headers: { "X-Elevated-Token": elevatedToken },
  });
}

export async function connectInstagramRequest(pageId, accessToken, name, elevatedToken) {
  return apiRequest("/api/channels/instagram/connect", {
    method: "POST",
    body: { page_id: pageId, access_token: accessToken, name: name || null },
    headers: { "X-Elevated-Token": elevatedToken },
  });
}

export async function disconnectChannelRequest(accountId, elevatedToken) {
  return apiRequest(`/api/channels/${accountId}`, {
    method: "DELETE",
    headers: { "X-Elevated-Token": elevatedToken },
  });
}

export async function getAccessOverviewRequest() {
  return apiRequest("/api/admin/access/overview");
}

export async function listPlatformCompaniesRequest() {
  return apiRequest("/api/platform/companies");
}

export async function getPlatformCompanyRequest(companyId) {
  return apiRequest(`/api/platform/companies/${companyId}`);
}

export async function createPlatformCompanyRequest(payload) {
  return apiRequest("/api/platform/companies", { method: "POST", body: payload });
}

export async function setPlatformCompanyStatusRequest(companyId, status) {
  return apiRequest(`/api/platform/companies/${companyId}/status`, {
    method: "PATCH",
    body: { status },
  });
}

export async function listPlatformPlansRequest(activeOnly = true) {
  return apiRequest(`/api/platform/plans?active_only=${activeOnly}`);
}

export async function createPlatformPlanRequest(payload) {
  return apiRequest("/api/platform/plans", { method: "POST", body: payload });
}

export async function updatePlatformPlanRequest(planId, payload) {
  return apiRequest(`/api/platform/plans/${planId}`, { method: "PATCH", body: payload });
}

export async function changePlatformCompanyPlanRequest(companyId, planId, durationDays) {
  return apiRequest(`/api/platform/companies/${companyId}/plan`, {
    method: "POST",
    body: { plan_id: planId, duration_days: durationDays },
  });
}

export async function getPlatformUsageRequest() {
  return apiRequest("/api/platform/usage");
}

export async function createAccessRoleRequest(payload) {
  return apiRequest("/api/admin/access/roles", { method: "POST", body: payload });
}

export async function updateAccessRoleRequest(roleId, payload) {
  return apiRequest(`/api/admin/access/roles/${roleId}`, { method: "PATCH", body: payload });
}

export async function createCompanyUserRequest(payload) {
  return apiRequest("/api/admin/access/users", { method: "POST", body: payload });
}

export async function updateCompanyUserRequest(userId, payload) {
  return apiRequest(`/api/admin/access/users/${userId}`, { method: "PATCH", body: payload });
}

export async function logoutRequest() {
  return apiRequest("/api/auth/logout", {
    method: "POST",
  });
}

export async function getCurrentUserRequest() {
  return apiRequest("/api/auth/me");
}

export async function getDashboardSummaryRequest() {
  return apiRequest("/api/dashboard/summary");
}

export async function getConversationsRequest({
  search = "",
  channel = "all",
  status = "all",
  department = "all",
  assignedUserId = null,
  folder = "inbox",
  tag = "",
  readStatus = "all",
  page = 1,
  pageSize = 20,
} = {}) {
  const query = createQueryString({
    search,
    channel,
    status,
    department,
    assigned_user_id: assignedUserId,
    folder,
    tag,
    read_status: readStatus,
    page,
    page_size: pageSize,
  });

  return apiRequest(`/conversations/${query}`);
}

export async function getConversationOptionsRequest() {
  return apiRequest("/conversations/options");
}

export async function getConversationMessagesRequest(
  channel,
  userId,
  limit = 200,
) {
  const query = createQueryString({ limit });
  return apiRequest(
    `${conversationPath(channel, userId)}${query}`,
  );
}

export async function getConversationControlRequest(
  channel,
  userId,
) {
  return apiRequest(
    `${conversationPath(channel, userId)}/control`,
  );
}

export async function takeOverConversationRequest(
  channel,
  userId,
) {
  return apiRequest(
    `${conversationPath(channel, userId)}/take-over`,
    { method: "POST" },
  );
}

export async function releaseConversationRequest(
  channel,
  userId,
) {
  return apiRequest(
    `${conversationPath(channel, userId)}/release`,
    { method: "POST" },
  );
}

export async function returnConversationToAiRequest(
  channel,
  userId,
) {
  return apiRequest(
    `${conversationPath(channel, userId)}/return-to-ai`,
    { method: "POST" },
  );
}

export async function updateConversationControlRequest(
  channel,
  userId,
  updates,
) {
  return apiRequest(
    `${conversationPath(channel, userId)}/control`,
    {
      method: "PATCH",
      body: updates,
    },
  );
}

export async function addConversationNoteRequest(
  channel,
  userId,
  note,
) {
  return apiRequest(
    `${conversationPath(channel, userId)}/notes`,
    {
      method: "POST",
      body: { note },
    },
  );
}

export async function sendConversationReplyRequest(
  channel,
  userId,
  text,
) {
  return apiRequest(
    `${conversationPath(channel, userId)}/reply`,
    {
      method: "POST",
      body: { text },
    },
  );
}

export function getConversationExportUrl(
  channel,
  userId,
  {
    scope = "full",
    format = "json",
  } = {},
) {
  const query = createQueryString({ scope, format });

  return (
    `${API_BASE_URL}` +
    `${conversationPath(channel, userId)}/export` +
    query
  );
}

export async function downloadConversationExport(
  channel,
  userId,
  {
    scope = "full",
    format = "json",
  } = {},
) {
  const token = getAccessToken();
  const response = await fetch(
    getConversationExportUrl(channel, userId, {
      scope,
      format,
    }),
    {
      headers: token
        ? { Authorization: `Bearer ${token}` }
        : {},
    },
  );

  if (!response.ok) {
    const data = await parseResponse(response);
    throw new Error(
      data?.detail ||
      "Export could not be generated.",
    );
  }

  const blob = await response.blob();
  const disposition =
    response.headers.get("content-disposition") || "";
  const filenameMatch = disposition.match(
    /filename="([^"]+)"/,
  );
  const filename =
    filenameMatch?.[1] ||
    `conversation.${format}`;
  const url = URL.createObjectURL(blob);
  const anchor = document.createElement("a");

  anchor.href = url;
  anchor.download = filename;
  document.body.appendChild(anchor);
  anchor.click();
  anchor.remove();
  URL.revokeObjectURL(url);
}

export async function subscribeConversationEvents({
  onEvent,
  onOpen,
  onError,
  signal,
} = {}) {
  const token = getAccessToken();
  const headers = {
    Accept: "text/event-stream",
  };

  if (token) {
    headers.Authorization = `Bearer ${token}`;
  }

  try {
    const response = await fetch(
      `${API_BASE_URL}/conversations/live/events`,
      {
        headers,
        signal,
        cache: "no-store",
      },
    );

    if (!response.ok || !response.body) {
      throw new Error(
        `Live connection failed with status ${response.status}.`,
      );
    }

    onOpen?.();

    const reader = response.body.getReader();
    const decoder = new TextDecoder();
    let buffer = "";

    while (!signal?.aborted) {
      const { value, done } = await reader.read();

      if (done) {
        break;
      }

      buffer += decoder.decode(value, {
        stream: true,
      });

      const blocks = buffer.split("\n\n");
      buffer = blocks.pop() || "";

      blocks.forEach((block) => {
        if (!block || block.startsWith(":")) {
          return;
        }

        const lines = block.split("\n");
        const eventName =
          lines
            .find((line) => line.startsWith("event:"))
            ?.slice(6)
            .trim() || "message";
        const dataText = lines
          .filter((line) => line.startsWith("data:"))
          .map((line) => line.slice(5).trim())
          .join("\n");

        if (!dataText) {
          return;
        }

        try {
          onEvent?.({
            event: eventName,
            data: JSON.parse(dataText),
          });
        } catch {
          // Ignore malformed stream events without closing the connection.
        }
      });
    }
  } catch (error) {
    if (error?.name !== "AbortError") {
      onError?.(error);
    }
  }
}
export async function getNotificationsRequest({
  status = "all",
  type = "",
  channel = "",
  date = "",
  page = 1,
  pageSize = 30,
} = {}) {
  const safePage = Math.max(1, Number(page) || 1);
  const safePageSize = Math.max(1, Math.min(250, Number(pageSize) || 30));
  const query = createQueryString({
    status,
    type,
    channel,
    date,
    limit: safePageSize,
    offset: (safePage - 1) * safePageSize,
  });
  return apiRequest(`/api/notifications${query}`);
}

export async function getNotificationSummaryRequest() {
  return apiRequest("/api/notifications/summary");
}

export async function markNotificationReadRequest(notificationId, notificationIds = []) {
  return apiRequest(
    `/api/notifications/${encodeURIComponent(notificationId)}/read`,
    { method: "POST", body: { notification_ids: notificationIds } },
  );
}

export async function markNotificationUnreadRequest(notificationId, notificationIds = []) {
  return apiRequest(
    `/api/notifications/${encodeURIComponent(notificationId)}/unread`,
    { method: "POST", body: { notification_ids: notificationIds } },
  );
}

export async function markAllNotificationsReadRequest() {
  return apiRequest("/api/notifications/read-all", { method: "POST" });
}

export async function clearVisibleNotificationsRequest(notificationIds) {
  return apiRequest("/api/notifications/clear-visible", {
    method: "DELETE",
    body: { notification_ids: notificationIds },
  });
}

export async function getCompanySettingSectionRequest(section) {
  return apiRequest(`/api/company-settings/${encodeURIComponent(section)}`);
}

export async function updateCompanySettingSectionRequest(section, values) {
  return apiRequest(`/api/company-settings/${encodeURIComponent(section)}`, {
    method: "PUT",
    body: { values },
  });
}

export async function createBroadcastRequest(payload) {
  return apiRequest("/api/broadcasts", { method: "POST", body: payload });
}

export async function listBroadcastsRequest() {
  return apiRequest("/api/broadcasts");
}

export async function sendBroadcastRequest(broadcastId) {
  return apiRequest(`/api/broadcasts/${broadcastId}/send`, { method: "POST" });
}

export async function deleteBroadcastRequest(broadcastId) {
  return apiRequest(`/api/broadcasts/${broadcastId}`, { method: "DELETE" });
}

export async function getBroadcastReportRequest(broadcastId) {
  return apiRequest(`/api/broadcasts/${broadcastId}/report`);
}
