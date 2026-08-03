
// Desktop (Electron) builds let the user point the app at any server at
// runtime — the value is stored under this key by the Login page. Web and
// mobile builds never write it, so they keep the build-time behavior.
const SERVER_URL_KEY = "tzone_server_url";

function readStoredServerUrl() {
  try {
    const stored = localStorage.getItem(SERVER_URL_KEY);
    return stored ? stored.replace(/\/+$/, "") : null;
  } catch {
    return null;
  }
}

const API_BASE_URL =
  readStoredServerUrl() ||
  import.meta.env.VITE_API_BASE_URL ||
  "http://127.0.0.1:8000";

export function getServerUrl() {
  return API_BASE_URL;
}

export function saveServerUrl(url) {
  const cleaned = (url || "").trim().replace(/\/+$/, "");
  if (cleaned) {
    localStorage.setItem(SERVER_URL_KEY, cleaned);
  } else {
    localStorage.removeItem(SERVER_URL_KEY);
  }
}

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

    // A 401 on a request that was sent with a token (not a login/2FA
    // attempt, which pass authenticated:false and expect 401 on wrong
    // credentials) means the session itself died - expired token,
    // server restart invalidating it, etc. Previously only the initial
    // page-load fetch cleared the session on 401; every other call just
    // threw a local error the calling component may or may not surface,
    // leaving a dead token in localStorage and the user stuck on a page
    // that silently breaks feature by feature instead of being sent
    // back to /login.
    if (authenticated && response.status === 401) {
      window.dispatchEvent(new CustomEvent("tzone:session-expired"));
    }

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

export async function getNotificationPreferencesRequest() {
  return apiRequest("/api/notification-preferences");
}

export async function updateNotificationPreferencesRequest(preferences) {
  return apiRequest("/api/notification-preferences", {
    method: "PUT",
    body: preferences,
  });
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

export async function verifyTwoFactorRequest(pendingToken, code) {
  return apiRequest("/api/auth/2fa/verify", {
    method: "POST",
    authenticated: false,
    body: { pending_token: pendingToken, code },
  });
}

export async function twoFactorStatusRequest() {
  return apiRequest("/api/auth/2fa/status");
}

export async function twoFactorEnrollStartRequest() {
  return apiRequest("/api/auth/2fa/enroll/start", { method: "POST" });
}

export async function twoFactorEnrollConfirmRequest(code) {
  return apiRequest("/api/auth/2fa/enroll/confirm", {
    method: "POST",
    body: { code },
  });
}

export async function twoFactorDisableRequest(password, code) {
  return apiRequest("/api/auth/2fa/disable", {
    method: "POST",
    body: { password, code },
  });
}

export async function signupPlansRequest() {
  return apiRequest("/api/signup/plans", { authenticated: false });
}

export async function sendSignupCodeRequest(email) {
  return apiRequest("/api/signup/send-code", {
    method: "POST",
    authenticated: false,
    body: { email },
  });
}

export async function signupRequest(payload) {
  return apiRequest("/api/signup", {
    method: "POST",
    authenticated: false,
    body: payload,
  });
}

export async function sendVerificationCodeRequest(purpose) {
  return apiRequest("/api/security/send-code", { method: "POST", body: { purpose } });
}

export async function verifyCodeRequest(purpose, code) {
  return apiRequest("/api/security/verify-code", { method: "POST", body: { purpose, code } });
}

export async function getSessionChangesRequest(purpose, token) {
  return apiRequest(`/api/security/changes?purpose=${encodeURIComponent(purpose)}`, {
    headers: { "X-Elevated-Token": token },
  });
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

export async function listSupportTicketsRequest() {
  return apiRequest("/api/support-tickets");
}

export async function createSupportTicketRequest(subject, description, priority) {
  return apiRequest("/api/support-tickets", { method: "POST", body: { subject, description, priority } });
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

export async function listAiTeachingChatRequest() {
  return apiRequest("/api/ai-teaching-chat");
}

export async function sendAiTeachingChatRequest(text) {
  return apiRequest("/api/ai-teaching-chat", { method: "POST", body: { text } });
}

export async function testAiReplyRequest({ message, channel, department }) {
  return apiRequest("/api/ai-teaching-chat/test", { method: "POST", body: { message, channel, department } });
}

export async function chatWithBotRequest({ message, channel, department }) {
  return apiRequest("/api/ai-teaching-chat/chat-with-bot", { method: "POST", body: { message, channel, department } });
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

export async function callOptionsRequest() {
  return apiRequest("/api/calls/options");
}

export async function listCallLogsRequest({ direction, status } = {}) {
  const query = createQueryString({ direction, status });
  return apiRequest(`/api/calls${query}`);
}

export async function createCallLogRequest(payload) {
  return apiRequest("/api/calls", { method: "POST", body: payload });
}

export async function deleteCallLogRequest(callId) {
  return apiRequest(`/api/calls/${callId}`, { method: "DELETE" });
}

export async function teamChatOptionsRequest() {
  return apiRequest("/api/team-chat/options");
}

export async function listTeamMessagesRequest({ beforeId, limit } = {}) {
  const query = createQueryString({ before_id: beforeId, limit });
  return apiRequest(`/api/team-chat${query}`);
}

export async function sendTeamMessageRequest(payload) {
  return apiRequest("/api/team-chat", { method: "POST", body: payload });
}

export async function deleteTeamMessageRequest(messageId) {
  return apiRequest(`/api/team-chat/${messageId}`, { method: "DELETE" });
}

export async function listTeamRoomsRequest() {
  return apiRequest("/api/team-chat/rooms");
}

export async function createTeamDmRequest(userId) {
  return apiRequest("/api/team-chat/rooms/dm", { method: "POST", body: { user_id: userId } });
}

export async function createTeamGroupRequest({ name, memberUserIds, department }) {
  return apiRequest("/api/team-chat/rooms/group", {
    method: "POST",
    body: { name, member_user_ids: memberUserIds || [], department: department || null },
  });
}

export async function listTeamRoomMessagesRequest(roomId, { limit } = {}) {
  const query = createQueryString({ limit });
  return apiRequest(`/api/team-chat/rooms/${roomId}/messages${query}`);
}

export async function sendTeamRoomMessageRequest(roomId, payload) {
  return apiRequest(`/api/team-chat/rooms/${roomId}/messages`, { method: "POST", body: payload });
}

export async function deleteTeamRoomMessageRequest(roomId, messageId) {
  return apiRequest(`/api/team-chat/rooms/${roomId}/messages/${messageId}`, { method: "DELETE" });
}

export async function listActivityLogRequest({ actorUserId, action, beforeId, limit } = {}) {
  const query = createQueryString({ actor_user_id: actorUserId, action, before_id: beforeId, limit });
  return apiRequest(`/api/activity-log${query}`);
}

async function uploadFileRequest(path, file) {
  const formData = new FormData();
  formData.append("file", file);

  const headers = { Accept: "application/json" };
  const token = getAccessToken();
  if (token) headers.Authorization = `Bearer ${token}`;

  let response;
  try {
    response = await fetch(`${API_BASE_URL}${path}`, {
      method: "POST",
      headers,
      body: formData,
    });
  } catch {
    throw new Error("Cannot connect to the T-ZONE server. Make sure FastAPI is running on port 8000.");
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

export async function uploadMediaRequest(file) {
  return uploadFileRequest("/api/media/upload", file);
}

export async function uploadVoiceNoteRequest(file) {
  return uploadFileRequest("/api/media/upload-voice-note", file);
}

export async function appointmentOptionsRequest() {
  return apiRequest("/api/appointments/options");
}

export async function listAppointmentsRequest({ status, employeeUserId, customerId, fromDate, toDate } = {}) {
  const query = createQueryString({
    status,
    employee_user_id: employeeUserId,
    customer_id: customerId,
    from_date: fromDate,
    to_date: toDate,
  });
  return apiRequest(`/api/appointments${query}`);
}

export async function createAppointmentRequest(payload) {
  return apiRequest("/api/appointments", { method: "POST", body: payload });
}

export async function updateAppointmentRequest(appointmentId, updates) {
  return apiRequest(`/api/appointments/${appointmentId}`, { method: "PUT", body: updates });
}

export async function deleteAppointmentRequest(appointmentId) {
  return apiRequest(`/api/appointments/${appointmentId}`, { method: "DELETE" });
}

export async function scheduledPostOptionsRequest() {
  return apiRequest("/api/scheduled-posts/options");
}

export async function listCommentPostsRequest({ channelAccountId } = {}) {
  const query = createQueryString({ channel_account_id: channelAccountId });
  return apiRequest(`/api/comments/posts${query}`);
}

export async function listPostCommentsRequest(postExternalId) {
  return apiRequest(`/api/comments/posts/${encodeURIComponent(postExternalId)}/comments`);
}

export async function replyToCommentRequest(commentId, text) {
  return apiRequest(`/api/comments/${commentId}/reply`, { method: "POST", body: { text } });
}

export async function listScheduledPostsRequest({ status } = {}) {
  const query = createQueryString({ status });
  return apiRequest(`/api/scheduled-posts${query}`);
}

export async function createScheduledPostRequest(payload) {
  return apiRequest("/api/scheduled-posts", { method: "POST", body: payload });
}

export async function updateScheduledPostRequest(postId, updates) {
  return apiRequest(`/api/scheduled-posts/${postId}`, { method: "PUT", body: updates });
}

export async function publishScheduledPostNowRequest(postId) {
  return apiRequest(`/api/scheduled-posts/${postId}/publish-now`, { method: "POST" });
}

export async function deleteScheduledPostRequest(postId) {
  return apiRequest(`/api/scheduled-posts/${postId}`, { method: "DELETE" });
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

export async function catalogueOptionsRequest() {
  return apiRequest("/api/catalogue/options");
}

export async function listProductsRequest({ search, category, status } = {}) {
  const query = createQueryString({ search, category, status });
  return apiRequest(`/api/catalogue${query}`);
}

export async function createProductRequest(payload) {
  return apiRequest("/api/catalogue", { method: "POST", body: payload });
}

export async function updateProductRequest(productId, updates) {
  return apiRequest(`/api/catalogue/${productId}`, { method: "PUT", body: updates });
}

export async function deleteProductRequest(productId) {
  return apiRequest(`/api/catalogue/${productId}`, { method: "DELETE" });
}

export async function importCatalogueCsvRequest(file) {
  const formData = new FormData();
  formData.append("file", file);

  const headers = { Accept: "application/json" };
  const token = getAccessToken();
  if (token) headers.Authorization = `Bearer ${token}`;

  let response;
  try {
    response = await fetch(`${API_BASE_URL}/api/catalogue/import/csv`, { method: "POST", headers, body: formData });
  } catch {
    throw new Error("Cannot connect to the T-ZONE server. Make sure FastAPI is running on port 8000.");
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

export async function importWhatsAppCatalogueRequest(catalogId) {
  return apiRequest("/api/catalogue/import/whatsapp", { method: "POST", body: { catalog_id: catalogId } });
}

export async function getAnalyticsSummaryRequest(days) {
  return apiRequest(`/api/analytics${createQueryString({ days })}`);
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

export async function listSavedRepliesRequest(options = {}) {
  const department = options?.department;
  const query = department ? `?department=${encodeURIComponent(department)}` : "";
  return apiRequest(`/api/saved-replies${query}`);
}

export async function createSavedReplyRequest(title, body, department = "") {
  return apiRequest("/api/saved-replies", { method: "POST", body: { title, body, department } });
}

export async function updateSavedReplyRequest(id, title, body, department) {
  const payload = { title, body };
  if (department !== undefined) payload.department = department;
  return apiRequest(`/api/saved-replies/${id}`, { method: "PATCH", body: payload });
}

export async function listReplyFlowsRequest() {
  return apiRequest("/api/reply-flows");
}

export async function getReplyFlowRequest(id) {
  return apiRequest(`/api/reply-flows/${id}`);
}

export async function createReplyFlowRequest(payload) {
  return apiRequest("/api/reply-flows", { method: "POST", body: payload });
}

export async function updateReplyFlowRequest(id, payload) {
  return apiRequest(`/api/reply-flows/${id}`, { method: "PATCH", body: payload });
}

export async function deleteReplyFlowRequest(id) {
  return apiRequest(`/api/reply-flows/${id}`, { method: "DELETE" });
}

export async function duplicateReplyFlowRequest(id) {
  return apiRequest(`/api/reply-flows/${id}/duplicate`, { method: "POST" });
}

export async function generateReplyFlowFromTextRequest(id, text) {
  return apiRequest(`/api/reply-flows/${id}/generate-from-text`, { method: "POST", body: { text } });
}

// Registry of available flow trigger types ({ key, label, category,
// description, config_fields }), served by the backend's TRIGGER_TYPES
// registry. Falls back gracefully (see ReplyFlowBuilderPage) while that
// endpoint is still landing.
export async function getReplyFlowTriggerTypesRequest() {
  return apiRequest("/api/reply-flows/trigger-types");
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

export async function getPlatformRevenueRequest() {
  return apiRequest("/api/platform/revenue");
}

export async function listPlatformAuditLogsRequest({ companyId, action, limit = 100, offset = 0 } = {}) {
  const query = createQueryString({ company_id: companyId, action, limit, offset });
  return apiRequest(`/api/platform/audit-logs${query}`);
}

export async function getCompanySubscriptionHistoryRequest(companyId) {
  return apiRequest(`/api/platform/companies/${companyId}/subscription-history`);
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

export async function getUserPermissionOverridesRequest(userId) {
  return apiRequest(`/api/admin/access/users/${userId}/overrides`);
}

export async function setUserPermissionOverridesRequest(userId, overrides) {
  return apiRequest(`/api/admin/access/users/${userId}/overrides`, { method: "PUT", body: { overrides } });
}

export async function resetCompanyUserPasswordRequest(userId) {
  return apiRequest(`/api/admin/access/users/${userId}/reset-password`, { method: "POST" });
}

export async function logoutCompanyUserRequest(userId) {
  return apiRequest(`/api/admin/access/users/${userId}/logout`, { method: "POST" });
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
  markRead = true,
) {
  const query = createQueryString({ limit, mark_read: markRead });
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
  mentionedUserIds = [],
) {
  return apiRequest(
    `${conversationPath(channel, userId)}/notes`,
    {
      method: "POST",
      body: { note, mentioned_user_ids: mentionedUserIds },
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

export async function sendConversationMediaReplyRequest(
  channel,
  userId,
  { mediaUrl, mediaType, caption, filename },
) {
  return apiRequest(
    `${conversationPath(channel, userId)}/reply-media`,
    {
      method: "POST",
      body: {
        media_url: mediaUrl,
        media_type: mediaType,
        caption: caption || undefined,
        filename: filename || undefined,
      },
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

export async function previewBroadcastRecipientCountRequest(broadcastId) {
  return apiRequest(`/api/broadcasts/${broadcastId}/recipient-count`);
}

export async function deleteBroadcastRequest(broadcastId) {
  return apiRequest(`/api/broadcasts/${broadcastId}`, { method: "DELETE" });
}

export async function getBroadcastReportRequest(broadcastId) {
  return apiRequest(`/api/broadcasts/${broadcastId}/report`);
}

// ---- Theme Studio / platform UI config (CLAUDE_CODE_THEME_SPEC.md) ----

export async function getPlatformUiConfigRequest() {
  return apiRequest("/api/platform-ui/config");
}

export async function listUiThemesRequest(scopeType, scopeId) {
  const params = new URLSearchParams({ scope_type: scopeType });
  if (scopeId) params.set("scope_id", scopeId);
  return apiRequest(`/api/platform-ui/themes?${params.toString()}`);
}

export async function createUiThemeDraftRequest({ scopeType, scopeId, tokens, modules }) {
  return apiRequest("/api/platform-ui/themes", {
    method: "POST",
    body: { scope_type: scopeType, scope_id: scopeId ?? null, tokens: tokens || {}, modules: modules || {} },
  });
}

export async function updateUiThemeDraftRequest(themeId, { tokens, modules }) {
  return apiRequest(`/api/platform-ui/themes/${themeId}`, {
    method: "PATCH",
    body: { tokens: tokens ?? null, modules: modules ?? null },
  });
}

export async function publishUiThemeRequest(themeId, reason) {
  return apiRequest(`/api/platform-ui/themes/${themeId}/publish`, {
    method: "POST",
    body: { reason },
  });
}

export async function restoreUiThemeRequest(themeId) {
  return apiRequest(`/api/platform-ui/themes/${themeId}/restore`, { method: "POST" });
}
