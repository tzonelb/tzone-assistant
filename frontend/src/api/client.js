
// Same origin in production: the app and the API are served from one domain
// behind the reverse proxy, so a request path must resolve on this site itself,
// not on the visitor's own machine. Only local `vite` dev (a separate :5173
// origin) needs the explicit backend URL. An explicit VITE_API_BASE_URL always
// wins (?? keeps an intentional empty string).
const API_BASE_URL =
  import.meta.env.VITE_API_BASE_URL ??
  (import.meta.env.PROD ? "" : "http://127.0.0.1:8000");

/*
 * The session token is no longer kept here.
 *
 * It lived in localStorage, which any script on the page can read: one XSS hole
 * anywhere — in a dependency, in a rendered customer name — handed an attacker
 * a working session that outlived the tab they stole it from. The server now
 * sets it as an httpOnly cookie, which script cannot read at all, so the token
 * never enters this file's reach.
 *
 * These three functions stay because a dozen screens import them. They are now
 * about the *presence* of a session rather than its value: `getAccessToken`
 * answers "does one exist" without being able to say what it is.
 */
const SESSION_FLAG_KEY = "tzone_signed_in";

/* The CSRF partner. Deliberately readable — it is not a credential. It proves
 * a request came from a page that could read this origin's cookies, which is
 * exactly what a cross-origin attacker cannot do. */
const CSRF_COOKIE = "tzone_csrf";

function readCookie(name) {
  if (typeof document === "undefined") {
    return null;
  }

  const match = document.cookie.match(
    new RegExp(`(?:^|; )${name}=([^;]*)`),
  );

  return match ? decodeURIComponent(match[1]) : null;
}

export function getAccessToken() {
  /* Truthy while a session exists, so every `if (getAccessToken())` in the app
   * keeps meaning what it meant. The value is not the token and must not be
   * sent anywhere. */
  return localStorage.getItem(SESSION_FLAG_KEY);
}

export function saveAccessToken(token) {
  if (token) {
    localStorage.setItem(SESSION_FLAG_KEY, "1");
  }
}

export function clearAccessToken() {
  localStorage.removeItem(SESSION_FLAG_KEY);
  /* The old key, in case this build is loaded in a browser that signed in
   * before the change. Leaving it would strand a real token in storage
   * indefinitely — exactly what this change exists to prevent. */
  localStorage.removeItem("tzone_access_token");
}

const LOGIN_PATH = "/login";

let redirectingToLogin = false;

export function handleUnauthorized() {
  clearAccessToken();

  if (typeof window === "undefined") {
    return;
  }

  if (
    redirectingToLogin ||
    window.location.pathname === LOGIN_PATH
  ) {
    return;
  }

  redirectingToLogin = true;
  window.location.replace(LOGIN_PATH);
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

  /* No Authorization header: the browser sends the httpOnly session cookie on
   * its own. A cookie travels automatically, which is also how a form on
   * another site could make the browser send it — so a write echoes the CSRF
   * token, which that site cannot read. */
  if (authenticated && method.toUpperCase() !== "GET") {
    const csrf = readCookie(CSRF_COOKIE);

    if (csrf) {
      headers["X-CSRF-Token"] = csrf;
    }
  }

  let response;

  try {
    response = await fetch(`${API_BASE_URL}${path}`, {
      method,
      headers,
      signal,
      /* Without this the browser sends no cookies to a cross-origin API, and
       * in development the app on :5173 and the API on :8000 are exactly
       * that. */
      credentials: "include",
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
      { cause: error },
    );
  }

  const data = await parseResponse(response);

  if (!response.ok) {
    if (authenticated && response.status === 401) {
      handleUnauthorized();
    }

    const message = resolveApiErrorMessage(data, response.status);

    const error = new Error(message);
    error.status = response.status;
    error.data = data;
    // Null far more often than not: Retry-After is not a CORS-safelisted
    // response header, so unless the API is same-origin or names it in
    // Access-Control-Expose-Headers the browser hides it. A screen that wants
    // a countdown has to work without one.
    const retryAfter = Number(response.headers.get("Retry-After"));
    error.retryAfter = retryAfter > 0 ? retryAfter : null;
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

export async function loginRequest(
  company,
  email,
  password,
  totpCode = "",
) {
  const body = { company, email, password };

  // Only sent once the account has a second factor and the form has asked for
  // the code; omitted on the first (password-only) attempt.
  if (totpCode) {
    body.totp_code = totpCode;
  }

  return apiRequest("/api/auth/login", {
    method: "POST",
    authenticated: false,
    body,
  });
}

export async function forgotPasswordRequest(email) {
  return apiRequest("/api/auth/password/forgot", {
    method: "POST",
    authenticated: false,
    body: { email },
  });
}

export async function changeOwnPasswordRequest(currentPassword, newPassword) {
  return apiRequest("/api/auth/password", {
    method: "POST",
    body: {
      current_password: currentPassword,
      new_password: newPassword,
    },
  });
}

export async function resetPasswordRequest(token, newPassword) {
  return apiRequest(
    `/api/auth/password/reset/${encodeURIComponent(token)}`,
    {
      method: "POST",
      authenticated: false,
      body: { new_password: newPassword },
    },
  );
}

export async function getAccessOverviewRequest() {
  return apiRequest("/api/admin/access/overview");
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

export async function forceUserPasswordResetRequest(userId) {
  return apiRequest(
    `/api/admin/access/users/${userId}/force-password-reset`,
    { method: "POST" },
  );
}

export async function unlockCompanyUserRequest(userId) {
  return apiRequest(`/api/admin/access/users/${userId}/unlock`, {
    method: "POST",
  });
}

export async function listBranchesRequest() {
  return apiRequest("/api/admin/access/branches");
}

export async function createBranchRequest(payload) {
  return apiRequest("/api/admin/access/branches", { method: "POST", body: payload });
}

export async function updateBranchRequest(branchId, payload) {
  return apiRequest(`/api/admin/access/branches/${branchId}`, {
    method: "PATCH",
    body: payload,
  });
}

export async function deleteBranchRequest(branchId) {
  return apiRequest(`/api/admin/access/branches/${branchId}`, {
    method: "DELETE",
  });
}

export async function logoutRequest() {
  return apiRequest("/api/auth/logout", {
    method: "POST",
  });
}

export async function getCurrentUserRequest() {
  return apiRequest("/api/auth/me");
}

export async function getWorkspaceConfigRequest() {
  return apiRequest("/api/platform-ui/config");
}

/* The same configuration under the name the design system's ThemeContext
 * imports. One endpoint, two callers: the shell reads modules and branding,
 * the theme reads `tokens` and `brand` from the same response. */
export async function getPlatformUiConfigRequest() {
  return apiRequest("/api/platform-ui/config");
}

/* ------------------------------------------------------------------ *
 * Names the design system's shell imports.
 *
 * TopbarV2's global search asks for four lists under the names the design
 * project used. Three of them are this platform's existing endpoints under a
 * different name, so they delegate rather than duplicate. Broadcast is a module
 * this branch does not carry yet: it answers empty so the search still works
 * and simply finds nothing there, which is what the caller already handles.
 * ------------------------------------------------------------------ */
export async function listCustomersRequest(options = {}) {
  return getCustomersRequest(options);
}

export async function listProductsRequest({ search = "", limit = 24 } = {}) {
  return apiRequest(
    `/api/catalogue/products${createQueryString({ search, limit })}`,
  );
}

export async function listTasksRequest({ search = "", limit = 20 } = {}) {
  return apiRequest(`/api/tasks${createQueryString({ search, limit })}`);
}

export async function listBroadcastsRequest() {
  return { items: [] };
}

/* ---------------------------------------------------------------- *
 * Saved replies -- the canned wording the composer offers.
 * Reading rides on conversations.view; writing takes settings.manage.
 * ---------------------------------------------------------------- */
export async function listSavedRepliesRequest({ department = "" } = {}) {
  return apiRequest(`/api/saved-replies${createQueryString({ department })}`);
}

export async function createSavedReplyRequest(title, body, department = "") {
  return apiRequest("/api/saved-replies", {
    method: "POST",
    body: { title, body, department },
  });
}

export async function updateSavedReplyRequest(id, title, body, department) {
  const payload = { title, body };

  if (department !== undefined) {
    payload.department = department;
  }

  return apiRequest(`/api/saved-replies/${encodeURIComponent(id)}`, {
    method: "PATCH",
    body: payload,
  });
}

export async function deleteSavedReplyRequest(id) {
  return apiRequest(`/api/saved-replies/${encodeURIComponent(id)}`, {
    method: "DELETE",
  });
}

/* ---------------------------------------------------------------- *
 * Conversation reminders -- come back to this at a time, optionally
 * sending a message when it arrives. One per conversation: setting a
 * second replaces the first.
 * ---------------------------------------------------------------- */
export async function setConversationReminderRequest(
  channel,
  userId,
  reminderAt,
  note,
  autoSend,
  messageText,
) {
  return apiRequest(`${conversationPath(channel, userId)}/reminder`, {
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
  return apiRequest(`${conversationPath(channel, userId)}/reminder`, {
    method: "DELETE",
  });
}

/* The redesigned Tasks and Appointments screens import these from here, while
 * this platform keeps them in api/tasks.js and api/appointments.js. Re-declared
 * against the same endpoints rather than re-exported: those modules import
 * `apiRequest` from this one, and importing them back would close a cycle. */
export async function taskOptionsRequest() {
  return apiRequest("/api/tasks/options");
}

export async function createTaskRequest(values) {
  return apiRequest("/api/tasks", { method: "POST", body: values });
}

export async function updateTaskRequest(taskId, values) {
  return apiRequest(`/api/tasks/${encodeURIComponent(taskId)}`, {
    method: "PATCH",
    body: values,
  });
}

export async function deleteTaskRequest(taskId) {
  return apiRequest(`/api/tasks/${encodeURIComponent(taskId)}`, {
    method: "DELETE",
  });
}

export async function appointmentOptionsRequest() {
  return apiRequest("/api/appointments/options");
}

export async function listAppointmentsRequest(options = {}) {
  return apiRequest(`/api/appointments${createQueryString(options)}`);
}

export async function createAppointmentRequest(values) {
  return apiRequest("/api/appointments", { method: "POST", body: values });
}

export async function updateAppointmentRequest(appointmentId, values) {
  return apiRequest(`/api/appointments/${encodeURIComponent(appointmentId)}`, {
    method: "PATCH",
    body: values,
  });
}

export async function deleteAppointmentRequest(appointmentId) {
  return apiRequest(`/api/appointments/${encodeURIComponent(appointmentId)}`, {
    method: "DELETE",
  });
}

/* Publish this company's design tokens (Theme Studio). */
export async function updatePlatformUiThemeRequest(tokens) {
  return apiRequest("/api/platform-ui/theme", {
    method: "PUT",
    body: tokens,
  });
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
  const response = await fetch(
    getConversationExportUrl(channel, userId, {
      scope,
      format,
    }),
    {
      // Auth is the httpOnly session cookie, not a bearer token.
      credentials: "include",
    },
  );

  if (!response.ok) {
    if (response.status === 401) {
      handleUnauthorized();
    }

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
  try {
    const response = await fetch(
      `${API_BASE_URL}/conversations/live/events`,
      {
        headers: { Accept: "text/event-stream" },
        // The session is an httpOnly cookie now, not a bearer token, so the
        // stream must send credentials like every other request. Without this
        // the server sees no session, answers 401, and the 401 handler below
        // bounces a freshly signed-in user straight back to the login screen.
        credentials: "include",
        signal,
        cache: "no-store",
      },
    );

    if (!response.ok || !response.body) {
      if (response.status === 401) {
        handleUnauthorized();
      }

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

export async function getCustomersRequest({
  search = "",
  limit = 20,
  offset = 0,
} = {}) {
  const query = createQueryString({
    search,
    limit,
    offset,
  });

  return apiRequest(`/api/customers${query}`);
}

export async function getCustomerRequest(customerId) {
  return apiRequest(
    `/api/customers/${encodeURIComponent(customerId)}`,
  );
}

export async function updateCustomerRequest(customerId, values) {
  return apiRequest(
    `/api/customers/${encodeURIComponent(customerId)}`,
    {
      method: "PUT",
      body: values,
    },
  );
}

export async function getKnowledgeItemsRequest({
  search = "",
  department = "",
  status = "",
  limit = 20,
  offset = 0,
} = {}) {
  const query = createQueryString({
    search,
    department,
    status,
    limit,
    offset,
  });

  return apiRequest(`/api/knowledge${query}`);
}

export async function getKnowledgeItemRequest(itemId) {
  return apiRequest(
    `/api/knowledge/${encodeURIComponent(itemId)}`,
  );
}

export async function createKnowledgeItemRequest(values) {
  return apiRequest("/api/knowledge", {
    method: "POST",
    body: values,
  });
}

export async function updateKnowledgeItemRequest(itemId, values) {
  return apiRequest(
    `/api/knowledge/${encodeURIComponent(itemId)}`,
    {
      method: "PUT",
      body: values,
    },
  );
}

export async function deleteKnowledgeItemRequest(itemId) {
  return apiRequest(
    `/api/knowledge/${encodeURIComponent(itemId)}`,
    { method: "DELETE" },
  );
}

export async function getKnowledgeOptionsRequest() {
  return apiRequest("/api/knowledge/options");
}

export async function createKnowledgeCategoryRequest(values) {
  return apiRequest("/api/knowledge/categories", {
    method: "POST",
    body: values,
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
