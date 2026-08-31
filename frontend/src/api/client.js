
// Same origin in production: the app and the API are served from one domain
// behind the reverse proxy, so a request path must resolve on this site itself,
// not on the visitor's own machine. Only local `vite` dev (a separate :5173
// origin) needs the explicit backend URL. An explicit VITE_API_BASE_URL always
// wins (?? keeps an intentional empty string).
export const API_BASE_URL =
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
  const result = await apiRequest("/api/auth/me");

  /* The redesigned screens ask a company row what the signed-in person may do
   * there — `company.permission_codes` — while this API answers one flat
   * `permissions` list for the company the session is currently on. Attached
   * per company here rather than read differently on each screen.
   *
   * Only the active company gets the list, because only the active company's
   * codes were resolved: filling the others in from the same list would claim
   * a permission in a company the server never checked, and the screens that
   * read it use it to decide what to *offer*. An empty list on an inactive
   * company is the honest answer — the server re-checks on every call anyway.
   */
  const activeCompanyId = result?.user?.active_company_id;
  const permissions = Array.isArray(result?.permissions) ? result.permissions : [];

  return {
    ...result,
    companies: (Array.isArray(result?.companies) ? result.companies : []).map(
      (company) => ({
        ...company,
        permission_codes:
          company.id === activeCompanyId ? permissions : [],
      }),
    ),
  };
}

export async function getWorkspaceConfigRequest() {
  return apiRequest("/api/platform-ui/config");
}

/* ------------------------------------------------------------------ *
 * The company's activity log, in the shape the screen reads it.
 *
 * Two calls rather than one, because this platform splits them: `/api/activity`
 * answers the entries, `/api/activity/options` answers what the filters can
 * offer — built from what the log actually contains, so a dropdown never lists
 * an action nobody has performed. The screen wants both in one object, so they
 * are joined here instead of in the component.
 *
 * The field names are this API's own (`summary`, `actor_label`) mapped onto the
 * ones the screen reads (`description`, `actor_name`). One translation in one
 * place: the alternative was the screen reading fields that do not exist and
 * rendering a column of blanks.
 * ------------------------------------------------------------------ */
export async function listActivityLogRequest({
  actorUserId,
  action,
  limit = 100,
} = {}) {
  const query = createQueryString({
    actor_user_id: actorUserId,
    action,
    limit,
  });

  const [log, options] = await Promise.all([
    apiRequest(`/api/activity${query}`),
    /* The filters must survive a log that cannot be summarised — an empty
     * dropdown is a smaller failure than a screen that shows nothing. */
    apiRequest("/api/activity/options").catch(() => ({})),
  ]);

  const items = Array.isArray(log?.items) ? log.items : [];

  return {
    items: items.map((entry) => ({
      ...entry,
      description: entry.summary || "",
      actor_name: entry.actor_label || "System",
    })),
    total: log?.total ?? items.length,
    actions: Array.isArray(options?.actions) ? options.actions : [],
    employees: (Array.isArray(options?.actors) ? options.actors : []).map(
      (actor) => ({ id: actor.id, display_name: actor.label }),
    ),
  };
}

/* The same configuration under the name the design system's ThemeContext
 * imports. One endpoint, two callers: the shell reads modules and branding,
 * the theme reads `tokens` and `brand` from the same response. */
export async function getPlatformUiConfigRequest() {
  // `authenticated: false` here means "a 401 is an answer, not a session
  // expiry" -- it does not stop the session cookie being sent, which
  // `credentials: "include"` does unconditionally, and this is a GET so the
  // CSRF header it also suppresses was never added.
  //
  // ThemeProvider mounts once for the whole tree and fetches this immediately,
  // which on a clean browser is before anyone has signed in to anything. Under
  // the default flag that 401 ran handleUnauthorized() and
  // window.location.replace("/login") -- so an operator opening /superadmin was
  // thrown onto the *company* login screen before the console had painted, and
  // no amount of retrying got them in. The console has its own sign-in at
  // /superadmin/login and its own client, and this endpoint answers for a
  // company session it does not have.
  //
  // Nothing is lost on the company app: ThemeProvider already treats a failure
  // as "use platformDefaults", and a session that expires mid-session is caught
  // by the next real request the screen makes, all of which still redirect.
  return apiRequest("/api/platform-ui/config", { authenticated: false });
}

/* ------------------------------------------------------------------ *
 * Names the design system's shell imports.
 *
 * TopbarV2's global search asks for four lists under the names the design
 * project used. Three of them are this platform's existing endpoints under a
 * different name, so they delegate rather than duplicate. The fourth,
 * Broadcast, is a real module here now and lives with the other broadcast
 * calls further down.
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

/* ---------------------------------------------------------------- *
 * Saved replies -- the canned wording the composer offers.
 * Reading rides on conversations.view; writing takes settings.manage.
 * ---------------------------------------------------------------- */
export async function listSavedRepliesRequest({ department = "" } = {}) {
  const result = await apiRequest(
    `/api/saved-replies${createQueryString({ department })}`,
  );

  // The design's screens read `replies`; this API answers `items`. Without
  // this the Saved Replies page and the composer picker silently show an
  // empty library while the request itself succeeds.
  return { ...result, replies: result?.items || [] };
}

// The design read `{ departments: [names] }` from its own /api/departments
// route. This platform's one department vocabulary is
// `business_departments.code` (see backend/api/routes/conversations.py), and
// the inbox options endpoint serves it under the same conversations.view gate
// the saved-replies read rides on. "Unassigned" is the inbox's
// no-section-yet sentinel, not a section a reply can be written for.
export async function listDepartmentsRequest() {
  const result = await apiRequest("/conversations/options");

  return {
    ...result,
    departments: (result?.departments || []).filter(
      (code) => code && code !== "Unassigned",
    ),
  };
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

/* ---------------------------------------------------------------- *
 * Attachments. A multipart POST, so it does not go through apiRequest
 * (which sets a JSON content type); the browser has to set its own
 * boundary. Auth still travels as the session cookie, and the CSRF
 * token is echoed the same way a JSON write echoes it.
 * ---------------------------------------------------------------- */
async function uploadFileRequest(path, file) {
  const form = new FormData();
  form.append("file", file);

  const headers = { Accept: "application/json" };
  const csrf = readCookie(CSRF_COOKIE);

  if (csrf) {
    headers["X-CSRF-Token"] = csrf;
  }

  let response;

  try {
    response = await fetch(`${API_BASE_URL}${path}`, {
      method: "POST",
      headers,
      credentials: "include",
      body: form,
    });
  } catch (error) {
    throw new Error("The file could not be uploaded.", { cause: error });
  }

  const data = await parseResponse(response);

  if (!response.ok) {
    if (response.status === 401) {
      handleUnauthorized();
    }

    const error = new Error(resolveApiErrorMessage(data, response.status));
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

/* Send a file already uploaded above. The server checks the URL names a file
 * this workspace stored, so this cannot be pointed at an arbitrary address. */
export async function sendConversationMediaReplyRequest(
  channel,
  userId,
  { mediaUrl, mediaType, caption, filename },
) {
  return apiRequest(`${conversationPath(channel, userId)}/reply-media`, {
    method: "POST",
    body: {
      media_url: mediaUrl,
      media_type: mediaType,
      caption: caption || undefined,
      filename: filename || undefined,
    },
  });
}

/* The redesigned Tasks and Appointments screens import these from here, while
 * this platform keeps them in api/tasks.js and api/appointments.js. Re-declared
 * against the same endpoints rather than re-exported: those modules import
 * `apiRequest` from this one, and importing them back would close a cycle. */
/* The redesigned screens were written against a different backend, so these are
 * adapters, not thin wrappers. Each one translates the design's vocabulary into
 * this platform's API. The translation lives here on purpose: the design files
 * themselves are kept byte-identical to the branch they came from, so they can
 * be re-synced without re-applying edits.
 *
 * Everything below was verified against the actual schemas in
 * backend/api/schemas/{tasks,appointments}.py and the routes in
 * backend/api/routes/. Getting one of these wrong is invisible: the request is
 * accepted, unknown keys are dropped, and the user watches a form report
 * success while saving nothing.
 */

export async function taskOptionsRequest() {
  return apiRequest("/api/tasks/options");
}

// The design's task form is worded differently from this platform's model:
// its "description" is `problem` here, its `due_at` is `due_date`, and it
// carries a customer while a task here links to a conversation. Names that
// have no home are dropped explicitly rather than silently by Pydantic.
function toTaskPayload(values = {}) {
  const {
    description,
    due_at: dueAt,
    customer_id: customerId,
    status,
    ...rest
  } = values;

  const payload = { ...rest };

  if (description !== undefined) payload.problem = description;
  if (dueAt !== undefined) payload.due_date = dueAt;
  // "done" is the design's word for finished; this platform's vocabulary is
  // open / in_progress / resolved / closed, and anything else is a 422.
  if (status !== undefined) payload.status = status === "done" ? "resolved" : status;
  // customerId is deliberately unused: a task here has no customer column, and
  // sending it would be dropped without telling anyone.
  void customerId;

  return payload;
}

export async function createTaskRequest(values) {
  return apiRequest("/api/tasks", {
    method: "POST",
    body: toTaskPayload(values),
  });
}

export async function updateTaskRequest(taskId, values) {
  const payload = toTaskPayload(values);

  // A status change has its own route here, and it is the only one that also
  // records who moved the task.
  if (Object.keys(payload).length === 1 && payload.status !== undefined) {
    return apiRequest(`/api/tasks/${encodeURIComponent(taskId)}/status`, {
      method: "PATCH",
      body: { status: payload.status },
    });
  }

  // PUT, not PATCH: /api/tasks/{id} serves PUT only, so a PATCH here was a 405
  // on every edit.
  return apiRequest(`/api/tasks/${encodeURIComponent(taskId)}`, {
    method: "PUT",
    body: payload,
  });
}

export async function deleteTaskRequest(taskId) {
  return apiRequest(`/api/tasks/${encodeURIComponent(taskId)}`, {
    method: "DELETE",
  });
}

export async function appointmentOptionsRequest() {
  const result = await apiRequest("/api/appointments/options");

  // The design's dialog reads `employees`; this API answers `staff`. Without
  // this the staff dropdown is silently always empty, and an appointment
  // cannot be created at all because staff is required.
  return { ...result, employees: result?.staff ?? result?.employees ?? [] };
}

export async function listAppointmentsRequest({
  employeeUserId,
  ...rest
} = {}) {
  const result = await apiRequest(
    `/api/appointments${createQueryString({
      ...rest,
      // The list route filters on staff_user_id.
      staff_user_id: employeeUserId,
    })}`,
  );

  return { ...result, items: (result?.items || []).map(toAppointmentRow) };
}

/* The list answers `starts_at`/`ends_at`/`staff_name`; the screen reads
 * `scheduled_at`, `duration_minutes` and `employee_name`. Only the request half
 * was adapted before, so every row rendered its time as an em dash and its
 * length as "undefined min" -- a screen that looked loaded and showed no
 * booking time at all. The original keys are kept alongside, so anything
 * reading the API's own names keeps working. */
function toAppointmentRow(row = {}) {
  const startsAt = row.starts_at ?? null;
  const endsAt = row.ends_at ?? null;

  let durationMinutes = null;

  if (startsAt && endsAt) {
    const minutes = Math.round(
      (new Date(endsAt).getTime() - new Date(startsAt).getTime()) / 60000,
    );

    // An unparseable date gives NaN, which would render as "NaN min".
    durationMinutes = Number.isFinite(minutes) ? minutes : null;
  }

  return {
    ...row,
    scheduled_at: startsAt,
    duration_minutes: durationMinutes,
    employee_name: row.staff_name ?? null,
    employee_user_id: row.staff_user_id ?? null,
  };
}

export async function createAppointmentRequest(values = {}) {
  const {
    scheduled_at: scheduledAt,
    duration_minutes: durationMinutes,
    employee_user_id: employeeUserId,
    ...rest
  } = values;

  // This API takes an explicit end, not a duration, and names the person
  // staff_user_id. Sending the design's shape unchanged was a 422 every time,
  // so the dialog could never create anything.
  const starts = scheduledAt ? new Date(scheduledAt) : null;
  const minutes = Number(durationMinutes) > 0 ? Number(durationMinutes) : 30;
  const ends =
    starts && !Number.isNaN(starts.valueOf())
      ? new Date(starts.getTime() + minutes * 60000)
      : null;

  return apiRequest("/api/appointments", {
    method: "POST",
    body: {
      ...rest,
      staff_user_id: employeeUserId,
      starts_at: starts ? starts.toISOString() : scheduledAt,
      ends_at: ends ? ends.toISOString() : undefined,
    },
  });
}

export async function updateAppointmentRequest(appointmentId, values = {}) {
  // The only in-place change the design's row offers is the status, and this
  // API keeps that on its own route.
  //
  // Except for "cancelled". The row's dropdown is filled from the service's
  // ALLOWED_STATUS, which includes it, but the PATCH body is a Literal of the
  // other four -- cancelling has its own route, because it records a reason and
  // is the one status change the customer was told about. Sending it to the
  // PATCH was a raw 422 every time, so the same action failed from the dropdown
  // and succeeded from the cancel button. Routing it here keeps the designed
  // control exactly as it is and makes every option in it work.
  if (values.status === "cancelled") {
    return deleteAppointmentRequest(appointmentId);
  }

  return apiRequest(
    `/api/appointments/${encodeURIComponent(appointmentId)}/status`,
    { method: "PATCH", body: { status: values.status } },
  );
}

export async function deleteAppointmentRequest(appointmentId) {
  // An appointment is cancelled, not deleted: the record is what the customer
  // was told, and the history is worth keeping.
  return apiRequest(
    `/api/appointments/${encodeURIComponent(appointmentId)}/cancel`,
    { method: "POST", body: { reason: null } },
  );
}

/* ------------------------------------------------------------------ *
 * Calls — the history of every phone call, and the live line that
 * places them.
 *
 * Two screens over two routers. The calls router is the record:
 * reading it rides on conversations.view and writing on
 * conversations.reply, because logging a call is answering a customer
 * by another route. The dialer router is the line itself, and
 * everything on it that makes a phone ring takes dialer.use.
 * ------------------------------------------------------------------ */
export async function callOptionsRequest() {
  return apiRequest("/api/calls/options");
}

export async function listCallLogsRequest({ customerId, direction, status } = {}) {
  const query = createQueryString({
    customer_id: customerId,
    direction,
    status,
  });

  return apiRequest(`/api/calls${query}`);
}

export async function createCallLogRequest(payload) {
  return apiRequest("/api/calls", { method: "POST", body: payload });
}

export async function deleteCallLogRequest(callId) {
  return apiRequest(`/api/calls/${encodeURIComponent(callId)}`, {
    method: "DELETE",
  });
}

/* Whether this deployment has a phone line, and what is missing when it
 * does not. The Dialer draws its setup notice from the `missing` list. */
export async function dialerStatusRequest() {
  return apiRequest("/api/dialer/status");
}

export async function listDialerCallsRequest({
  activeOnly = false,
  limit = 50,
  offset = 0,
} = {}) {
  const query = createQueryString({ active_only: activeOnly, limit, offset });

  return apiRequest(`/api/dialer/calls${query}`);
}

export async function placeDialerCallRequest(payload) {
  return apiRequest("/api/dialer/calls", { method: "POST", body: payload });
}

export async function transferDialerCallRequest(callId, payload) {
  return apiRequest(
    `/api/dialer/calls/${encodeURIComponent(callId)}/transfer`,
    { method: "POST", body: payload },
  );
}

export async function hangupDialerCallRequest(callId) {
  return apiRequest(`/api/dialer/calls/${encodeURIComponent(callId)}/hangup`, {
    method: "POST",
  });
}

/* ---------------------------------------------------------------- *
 * Broadcast -- one message, sent once, to many contacts.
 *
 * The paths and the shapes are the API's own (backend/api/routes/
 * broadcasts.py), and they are already the shapes the two Broadcast screens
 * read -- `{ items }` for the list, the campaign row itself for a create,
 * `{ recipient_count }` for the live recount, and `{ broadcast, totals,
 * recipients, channel_tracking_supported }` for the report -- so unlike
 * tasks and appointments there is nothing to adapt between the two names.
 * ---------------------------------------------------------------- */
export async function listBroadcastsRequest() {
  return apiRequest("/api/broadcasts");
}

export async function createBroadcastRequest(payload) {
  return apiRequest("/api/broadcasts", { method: "POST", body: payload });
}

export async function getBroadcastReportRequest(broadcastId) {
  return apiRequest(
    `/api/broadcasts/${encodeURIComponent(broadcastId)}/report`,
  );
}

/* The stored `recipient_count` is a snapshot from when the draft was created.
 * This recomputes it the way the send will resolve it, so the confirm dialog
 * cannot promise a number the send does not deliver to. */
export async function previewBroadcastRecipientCountRequest(broadcastId) {
  return apiRequest(
    `/api/broadcasts/${encodeURIComponent(broadcastId)}/recipient-count`,
  );
}

export async function sendBroadcastRequest(broadcastId) {
  return apiRequest(`/api/broadcasts/${encodeURIComponent(broadcastId)}/send`, {
    method: "POST",
  });
}

export async function deleteBroadcastRequest(broadcastId) {
  return apiRequest(`/api/broadcasts/${encodeURIComponent(broadcastId)}`, {
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

/* ------------------------------------------------------------------ *
 * Theme Studio's draft lifecycle.
 *
 * The call above publishes in one step. These five are the step in between:
 * a draft nobody else sees, saved as each control moves, then published as
 * the scope's next numbered version — or an archived version reopened.
 * `scopeType` is "platform" (every workspace), "plan" or "company"; the
 * server decides who may write which.
 * ------------------------------------------------------------------ */

export async function listUiThemesRequest(scopeType, scopeId) {
  const query = createQueryString({
    scope_type: scopeType,
    scope_id: scopeId,
  });

  return apiRequest(`/api/platform-ui/themes${query}`);
}

export async function createUiThemeDraftRequest({
  scopeType,
  scopeId = null,
  tokens = {},
  modules = {},
} = {}) {
  return apiRequest("/api/platform-ui/themes", {
    method: "POST",
    body: {
      scope_type: scopeType,
      scope_id: scopeId,
      tokens,
      modules,
    },
  });
}

export async function updateUiThemeDraftRequest(themeId, { tokens, modules } = {}) {
  /* Only what this call actually changes is sent. An omitted half stays
   * omitted rather than becoming `{}`, because the server reads "absent" as
   * "leave this alone" — the studio saves one control at a time and must not
   * rewrite the module list every time somebody drags the radius slider. */
  const body = {};

  if (tokens !== undefined) body.tokens = tokens;
  if (modules !== undefined) body.modules = modules;

  return apiRequest(
    `/api/platform-ui/themes/${encodeURIComponent(themeId)}`,
    { method: "PATCH", body },
  );
}

export async function publishUiThemeRequest(themeId, reason) {
  return apiRequest(
    `/api/platform-ui/themes/${encodeURIComponent(themeId)}/publish`,
    { method: "POST", body: { reason } },
  );
}

export async function restoreUiThemeRequest(themeId) {
  return apiRequest(
    `/api/platform-ui/themes/${encodeURIComponent(themeId)}/restore`,
    { method: "POST", body: {} },
  );
}

/* ================================================================== *
 * The Platform Admin screen's calls.
 *
 * READ THIS BEFORE ADDING ONE.
 *
 * The console these functions talk to is the operator's one, and on this
 * platform it is a *separate credential*: every route there depends on
 * `get_platform_admin`, which requires a token minted in the platform scope,
 * belonging to a super admin, with a second factor enrolled. A company session
 * is refused by design — that separation is what stops an operator's console
 * from also being a way into a customer's workspace. Both sessions share one
 * cookie slot, so a browser holds one or the other and never both.
 *
 * The consequence is worth stating plainly: the Platform Admin screen renders
 * inside the *customer* shell, so from there these calls answer 403 "Sign in
 * to the platform console to perform this action." The console that works is
 * the one at `/superadmin`, which has its own sign-in and its own HTTP layer
 * (`superadmin/platformClient.js`).
 *
 * These functions therefore do the one useful thing left: they name the real
 * endpoint, in the real shape, so nothing here is a path that does not exist.
 * Where this platform has no equivalent at all, the function says so instead of
 * inventing one.
 * ================================================================== */

/* Our console keys a plan by its `code`; the screen was written against one
 * that keys it by `id`. Both are on every plan row the list returns, so the
 * mapping is remembered as the list goes past rather than guessed at later. */
const platformPlanCodesById = new Map();

function planCodeFor(planId) {
  const code = platformPlanCodesById.get(String(planId));

  if (!code) {
    throw new Error(
      "That plan is not loaded. Open the Plans tab first — this console " +
      "identifies a plan by its code, not by a row id.",
    );
  }

  return code;
}

function notOnThisPlatform(what) {
  return new Error(
    `${what} is not part of this platform's console. The operator's console ` +
    "is at /superadmin.",
  );
}

export async function listPlatformCompaniesRequest() {
  const result = await apiRequest("/api/platform/companies");
  return { companies: result?.items || [] };
}

export async function createPlatformCompanyRequest() {
  /* Not a missing endpoint — a different contract. Provisioning a company here
   * seals a database with its own key and hands back a workspace code exactly
   * once, so `POST /api/platform/companies` requires the owner's name, address
   * and first password and a workspace name. This form collects none of them,
   * and inventing a password for somebody's owner account is not a default
   * anything may pick. The console at /superadmin has the form that asks. */
  throw notOnThisPlatform("Creating a company from this screen");
}

export async function setPlatformCompanyStatusRequest(companyId, status) {
  return apiRequest(
    `/api/platform/companies/${encodeURIComponent(companyId)}/status`,
    { method: "POST", body: { status } },
  );
}

export async function listPlatformPlansRequest() {
  const result = await apiRequest("/api/platform/plans");
  const plans = result?.items || [];

  plans.forEach((plan) => {
    if (plan?.id !== undefined && plan?.code) {
      platformPlanCodesById.set(String(plan.id), plan.code);
    }
  });

  return { plans };
}

export async function createPlatformPlanRequest(form = {}) {
  const { code, name, ...values } = form;

  return apiRequest("/api/platform/plans", {
    method: "POST",
    body: { code, name, values },
  });
}

export async function updatePlatformPlanRequest(planId, payload = {}) {
  /* `code` identifies the plan and is never editable — every subscription
   * points at it, so renaming one would move every company on that plan onto a
   * plan that no longer exists. `id` is not a value either. */
  const values = { ...payload };
  delete values.code;
  delete values.id;

  return apiRequest(
    `/api/platform/plans/${encodeURIComponent(planCodeFor(planId))}`,
    { method: "PATCH", body: { values } },
  );
}

export async function changePlatformCompanyPlanRequest(companyId, planId) {
  return apiRequest(
    `/api/platform/companies/${encodeURIComponent(companyId)}/plan`,
    { method: "POST", body: { plan_code: planCodeFor(planId) } },
  );
}

export async function updatePlatformCompanyModulesRequest(companyId, modules) {
  return apiRequest(
    `/api/platform/companies/${encodeURIComponent(companyId)}/config`,
    { method: "PUT", body: { modules } },
  );
}

export async function listPlatformAuditLogsRequest({
  companyId,
  action,
  limit = 100,
  offset = 0,
} = {}) {
  const query = createQueryString({
    company_id: companyId,
    action,
    limit,
    offset,
  });

  return apiRequest(`/api/platform/audit${query}`);
}

/* The four below have no counterpart on this platform. Self-service plan
 * requests, a revenue roll-up and a platform-wide usage total were never built
 * here — allowances are per company (`/companies/{id}/usage`) and a plan is
 * assigned by the operator directly. They answer empty rather than calling a
 * path that would 404, so the screen loads and simply shows nothing there,
 * which is the truth. */

export async function listSubscriptionRequestsRequest() {
  return { requests: [] };
}

export async function reviewSubscriptionRequestRequest() {
  /* A read that finds nothing is honest; a write that silently does nothing is
   * not. There are no requests to review, so reaching this is a bug worth
   * hearing about. */
  throw notOnThisPlatform("Reviewing subscription requests");
}

export async function getPlatformUsageRequest() {
  return {};
}

export async function getPlatformRevenueRequest() {
  return {};
}

export async function getCompanySubscriptionHistoryRequest() {
  return { history: [] };
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
  // The screen's background refresh passes false. Without it every poll counts
  // as opening the conversation, which marks it read again a few seconds after
  // somebody deliberately marked it unread.
  markRead = true,
) {
  const query = createQueryString({ limit, mark_read: markRead ? "" : "false" });
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

/* The colleagues the note is for. The composer's picker resolves each `@name`
 * to an id as it is chosen, and those ids travel with the note: a name two
 * people answer to would otherwise be guessed at on the server, and the wrong
 * colleague would be handed a note about a customer. The server checks every
 * id against this company's own directory before it stores or notifies. */
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
      body: {
        note,
        mentioned_user_ids: Array.isArray(mentionedUserIds)
          ? mentionedUserIds
          : [],
      },
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
  lifecycleStage = "",
  tag = "",
  assignedUserId = "",
  segmentId = "",
  limit = 20,
  offset = 0,
} = {}) {
  // The Contacts screen names its filters in camelCase; the API reads the same
  // five as query parameters in snake_case. `createQueryString` drops the empty
  // ones, so an unfiltered list is still a bare `/api/customers`.
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

export async function customerOptionsRequest() {
  return apiRequest("/api/customers/options");
}

export async function getCustomerRequest(customerId) {
  return apiRequest(
    `/api/customers/${encodeURIComponent(customerId)}`,
  );
}

export async function getCustomerTimelineRequest(customerId) {
  return apiRequest(
    `/api/customers/${encodeURIComponent(customerId)}/timeline`,
  );
}

export async function createCustomerRequest(values) {
  return apiRequest("/api/customers", {
    method: "POST",
    body: values,
  });
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

export async function bulkUpdateCustomersRequest(payload) {
  return apiRequest("/api/customers/bulk-update", {
    method: "POST",
    body: payload,
  });
}

export async function listCustomerSegmentsRequest() {
  return apiRequest("/api/customer-segments");
}

export async function createCustomerSegmentRequest(name, filters) {
  return apiRequest("/api/customer-segments", {
    method: "POST",
    body: { name, filters: filters || {} },
  });
}

export async function deleteCustomerSegmentRequest(segmentId) {
  return apiRequest(
    `/api/customer-segments/${encodeURIComponent(segmentId)}`,
    { method: "DELETE" },
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

/* ------------------------------------------------- company settings (v2)
 *
 * The design's Company Settings page keeps its business hours inside the
 * `company_profile` section, as `business_hours`, shaped
 * `{ monday: { open: true, from: "09:00", to: "18:00" }, ... }`.
 *
 * This platform already owns that decision, in its own `working_hours`
 * section: `{ enabled, timezone, days: { monday: { open, close, closed } } }`.
 * It is not a second copy of the same thing — it is *the* copy, and it is the
 * one the assistant actually consults (`core/working_hours.py`, read from
 * `core/engine.py` on the escalation path).
 *
 * So the translation happens here rather than on the page. Storing
 * `business_hours` into `company_profile` would have worked — that section is
 * an open bag and would have accepted it — and would have been the worst
 * outcome available: an owner setting their hours on the screen, seeing them
 * saved, and the assistant going on answering at three in the morning from the
 * other store. The `working_hours` block in `database/schema_tenant.py` names
 * that exact failure.
 *
 * Two shape differences, both real:
 *   - `open` means "we are open this day" in the design, and "the time we open"
 *     here. Reading one as the other is a boolean where a clock belongs.
 *   - the design has no `closed`; a day is open or it is not.
 */
const WEEKDAYS = [
  "sunday", "monday", "tuesday", "wednesday", "thursday", "friday", "saturday",
];

function toBusinessHours(workingHours) {
  const days = workingHours?.days || {};

  return Object.fromEntries(
    WEEKDAYS.map((day) => {
      const stored = days[day] || {};

      return [day, {
        // `enabled` off means the company has not set hours at all, which the
        // engine treats as always open. Drawing every day ticked would tell an
        // owner they had set something they had not.
        open: Boolean(workingHours?.enabled) && !stored.closed,
        from: stored.open || "09:00",
        to: stored.close || "18:00",
      }];
    }),
  );
}

function toWorkingHours(businessHours, timezone) {
  const days = Object.fromEntries(
    WEEKDAYS.map((day) => {
      const drawn = businessHours?.[day] || {};

      return [day, {
        open: drawn.from || "09:00",
        close: drawn.to || "18:00",
        closed: !drawn.open,
      }];
    }),
  );

  return {
    // Saving the screen is the act that turns hours on. Leaving `enabled` false
    // would store the whole week and have the engine ignore all of it.
    enabled: true,
    timezone: timezone || "Asia/Beirut",
    days,
  };
}

export async function getCompanySettingSectionRequest(section) {
  const result = await apiRequest(
    `/api/company-settings/${encodeURIComponent(section)}`,
  );

  if (section !== "company_profile") {
    return result;
  }

  // A second request, not a wider one: the two sections are stored separately
  // and are read by different screens. The profile screen is the only place
  // that draws them together.
  let workingHours = null;

  try {
    workingHours = await apiRequest("/api/company-settings/working_hours");
  } catch {
    // Hours are one row of the profile form. Failing to read them should not
    // blank the company's name and timezone with an error.
  }

  return {
    ...result,
    values: {
      ...result?.values,
      business_hours: toBusinessHours(workingHours?.values),
    },
    locked_keys: [
      ...(result?.locked_keys || []),
      // A Super Admin lock on the hours is a lock on the control that edits
      // them, wherever that control is drawn.
      ...((workingHours?.locked_keys || []).length ? ["business_hours"] : []),
    ],
  };
}

export async function updateCompanySettingSectionRequest(section, values) {
  if (section !== "company_profile") {
    return apiRequest(`/api/company-settings/${encodeURIComponent(section)}`, {
      method: "PUT",
      body: { values },
    });
  }

  const { business_hours: businessHours, ...profile } = values || {};

  const result = await apiRequest("/api/company-settings/company_profile", {
    method: "PUT",
    body: { values: profile },
  });

  let workingHours = null;

  if (businessHours) {
    workingHours = await apiRequest("/api/company-settings/working_hours", {
      method: "PUT",
      body: { values: toWorkingHours(businessHours, profile.timezone) },
    });
  }

  return {
    ...result,
    values: {
      ...result?.values,
      business_hours: workingHours
        ? toBusinessHours(workingHours.values)
        : businessHours,
    },
  };
}

/* ------------------------------------------------------------- billing (v2)
 *
 * The design calls these /api/platform/my-* . On this platform the
 * /api/platform prefix
 * is the operator's console and runs on a platform-scope session no company
 * login can obtain, so the company's own view of its plan lives at
 * /api/billing instead (backend/api/routes/billing.py). Same information,
 * a prefix that does not claim an authority the caller does not have.
 */

export async function getMySubscriptionRequest() {
  const result = await apiRequest("/api/billing/subscription");

  return {
    ...result,
    // The plan's feature flags are `voice_ai_enabled` and friends here; the
    // design reads `features.voice_ai`. Both names are returned, so anything
    // reading the API's own spelling keeps working. Getting this wrong is
    // invisible: the voice-reply toggle simply stays disabled and tells the
    // owner to upgrade a plan they are already on.
    features: {
      ...result?.features,
      voice_ai: Boolean(result?.features?.voice_ai_enabled),
      image_ai: Boolean(result?.features?.image_ai_enabled),
      accounting: Boolean(result?.features?.accounting_connector_enabled),
      products: Boolean(result?.features?.product_connector_enabled),
    },
  };
}

export async function getMyModulesRequest() {
  // The design indexes the result directly (`modules[key]`); this API wraps it.
  const result = await apiRequest("/api/billing/modules");

  return result?.modules || {};
}

export async function getPlansCatalogRequest() {
  return apiRequest("/api/billing/plans");
}

export async function requestPlanChangeRequest(planId, note) {
  return apiRequest("/api/billing/requests", {
    method: "POST",
    body: { plan_id: planId, note: note || "" },
  });
}

export async function getMySubscriptionRequestsRequest() {
  return apiRequest("/api/billing/requests");
}

/* ------------------------------------------- secure channels panel (v2)
 *
 * `SecureChannelsPanel` is the design's Channels section, and it is drawn
 * against a subsystem this platform does not have: a six-digit email code that
 * buys a 20-minute *elevated* session (/api/security/send-code ,
 * `/verify-code`, `/changes` and an `X-Elevated-Token` header on every write),
 * plus per-provider connect flows — a WhatsApp QR pairing bridge, and direct
 * Instagram/Facebook credential logins.
 *
 * None of it exists here. What this platform has is one generic channel
 * account API: `GET /api/channels`, `POST /api/channels`, and
 * `DELETE /api/channels/{id}`, all behind `channels.view`/`channels.manage`.
 *
 * So the two calls that DO have a home here are adapted below and are real,
 * which is what lets the section draw the company's connected accounts and its
 * plan usage rather than an empty shell. The rest reject with the reason. They
 * are deliberately NOT pointed at a plausible-looking endpoint: inventing a
 * destination for a credential-handling flow is how a connect form comes to
 * report success and store nothing, and an elevated-session check comes to be
 * skipped rather than implemented. The panel's own error handling shows the
 * message.
 */

/* One message, one reason, for every design control whose backend this platform
 * does not have. It rejects rather than resolving empty on purpose: a screen
 * that quietly renders "no items" for a feature that was never built is
 * indistinguishable from one whose data failed to load, and both look like a
 * feature that exists and is broken. An error the section shows says which it
 * is. No request is made — there is no endpoint to make it to, and pointing one
 * at a plausible-looking path is how a form comes to report success and store
 * nothing. */
function notBuiltHere(what, instead = "") {
  return Promise.reject(
    new Error(
      `${what} is not available on this platform yet.` +
      (instead ? ` ${instead}` : ""),
    ),
  );
}

export async function listMyChannelsRequest() {
  // Real. `/api/channels` answers `items`; the panel reads `channels`.
  const result = await apiRequest("/api/channels");

  return { ...result, channels: result?.items || [] };
}

export async function disconnectChannelRequest(accountId, elevatedToken) {
  // Real, minus the elevated token: there is no elevated session to prove, and
  // sending a header the server does not read would look like one existed.
  // `channels.manage` is what actually guards this.
  void elevatedToken;

  return apiRequest(`/api/channels/${encodeURIComponent(accountId)}`, {
    method: "DELETE",
  });
}

export function sendVerificationCodeRequest() {
  return notBuiltHere("Email verification for channel access",
    "Connect and disconnect accounts from the Channels screen instead.");
}

export function verifyCodeRequest() {
  return notBuiltHere("Email verification for channel access",
    "Connect and disconnect accounts from the Channels screen instead.");
}

export function getSessionChangesRequest() {
  return notBuiltHere("The verified-session change log",
    "Connect and disconnect accounts from the Channels screen instead.");
}

export function connectTelegramRequest() {
  return notBuiltHere("Connecting Telegram from this screen",
    "Connect and disconnect accounts from the Channels screen instead.");
}

export function connectWhatsAppRequest() {
  return notBuiltHere("Connecting WhatsApp Cloud from this screen",
    "Connect and disconnect accounts from the Channels screen instead.");
}

export function connectInstagramDirectRequest() {
  return notBuiltHere("Connecting Instagram with a username and password",
    "Connect and disconnect accounts from the Channels screen instead.");
}

export function connectFacebookDirectRequest() {
  return notBuiltHere("Connecting Facebook with session cookies",
    "Connect and disconnect accounts from the Channels screen instead.");
}

export function startFacebookOAuthRequest() {
  return notBuiltHere("Connecting Facebook over OAuth",
    "Connect and disconnect accounts from the Channels screen instead.");
}

export function startWhatsAppQrRequest() {
  return notBuiltHere("WhatsApp QR pairing",
    "Connect and disconnect accounts from the Channels screen instead.");
}

export function whatsAppQrStatusRequest() {
  return notBuiltHere("WhatsApp QR pairing",
    "Connect and disconnect accounts from the Channels screen instead.");
}

/* ----------------------------------------------- support tickets (v2)
 *
 * To the T-ZONE team about the platform, not to the company about a customer —
 * `/api/tickets` is that other thing and is a different table entirely.
 */

export async function listSupportTicketsRequest() {
  return apiRequest("/api/support-tickets");
}

export async function createSupportTicketRequest(subject, description, priority) {
  return apiRequest("/api/support-tickets", {
    method: "POST",
    body: { subject, description, priority },
  });
}

/* ------------------------------------------------- AI Knowledge (v2)
 *
 * The design's Knowledge section keeps an entry as
 * `{ title, content, department, tags }`. This platform's knowledge base is
 * `knowledge_items`: one title, Arabic and English content side by side, a
 * department and a `keywords` string.
 *
 * They are the same feature, so this adapts rather than stubs. Two real
 * differences:
 *   - one `content` box, two content columns. What the employee typed goes to
 *     `content_en`, and `content` is read back as whichever column has text —
 *     an entry written in Arabic through the older screen still shows here
 *     instead of appearing blank.
 *   - `tags` is a list; `keywords` is one comma-separated string.
 */

function toKnowledgeEntry(item = {}) {
  return {
    id: item.id,
    title: item.title,
    content: item.content_en || item.content_ar || "",
    department: item.department || "Unassigned",
    tags: String(item.keywords || "")
      .split(",")
      .map((tag) => tag.trim())
      .filter(Boolean),
  };
}

function toKnowledgeItem(title, content, department, tags) {
  return {
    title,
    // Never both, and never neither: the API refuses an item with no content at
    // all, which is the one thing this screen can produce by accident.
    content_en: content || "",
    department: department && department !== "Unassigned" ? department : null,
    keywords: (tags || []).join(", "),
  };
}

export async function listKnowledgeEntriesRequest() {
  const result = await apiRequest("/api/knowledge");

  return { ...result, entries: (result?.items || []).map(toKnowledgeEntry) };
}

export async function createKnowledgeEntryRequest(title, content, department, tags) {
  return apiRequest("/api/knowledge", {
    method: "POST",
    body: toKnowledgeItem(title, content, department, tags),
  });
}

export async function updateKnowledgeEntryRequest(entryId, title, content, department, tags) {
  return apiRequest(`/api/knowledge/${encodeURIComponent(entryId)}`, {
    method: "PUT",
    body: toKnowledgeItem(title, content, department, tags),
  });
}

export async function deleteKnowledgeEntryRequest(entryId) {
  return apiRequest(`/api/knowledge/${encodeURIComponent(entryId)}`, {
    method: "DELETE",
  });
}

/* --------------------------------- AI Instructions and Reply Flows (v2)
 *
 * Two more sections of the design's Company Settings drawn against backends
 * this platform does not have.
 *
 * `InstructionsPage` wants /api/instructions — an ordered list of behaviour
 * rules, each scoped to a department or channel, with a reorder endpoint that
 * decides which rule wins a conflict. `ReplyFlowsListPage` wants
 * /api/reply-flows — the step-by-step conversation builder.
 *
 * Neither exists here, and neither is a rename of something that does: the
 * nearest things this platform owns are the AI profile
 * (`/api/ai-teaching/profile`) and the per-channel reply policy
 * (`/api/ai-teaching/reply-policy`), which are different models answering
 * different questions. Mapping one onto the other would produce a screen that
 * accepted rules and silently changed nothing about how the assistant replies.
 *
 * So they reject with the reason, and the sections stay in the navigation
 * drawn exactly as the design draws them.
 */

export function listInstructionsRequest() {
  return notBuiltHere("AI Instructions");
}

export function createInstructionRequest() {
  return notBuiltHere("AI Instructions");
}

export function updateInstructionRequest() {
  return notBuiltHere("AI Instructions");
}

export function deleteInstructionRequest() {
  return notBuiltHere("AI Instructions");
}

export function reorderInstructionsRequest() {
  return notBuiltHere("AI Instructions");
}

export function listReplyFlowsRequest() {
  return notBuiltHere("Reply Flows");
}

export function createReplyFlowRequest() {
  return notBuiltHere("Reply Flows");
}

export function deleteReplyFlowRequest() {
  return notBuiltHere("Reply Flows");
}

export function duplicateReplyFlowRequest() {
  return notBuiltHere("Reply Flows");
}

/* ------------------------------------------- notification preferences (v2) */

export async function getNotificationPreferencesRequest() {
  return apiRequest("/api/notification-preferences");
}

export async function updateNotificationPreferencesRequest(preferences) {
  return apiRequest("/api/notification-preferences", {
    method: "PUT",
    body: preferences,
  });
}

/* --------------------------------------------------- two-factor auth (v2)
 *
 * The design calls /api/auth/2fa/* ; this platform serves the same second
 * factor at `/api/auth/totp`, with `DELETE` where the design sends
 * `POST /disable`. Adapted here rather than on the page, and verified against
 * backend/api/routes/auth.py.
 */

export async function twoFactorStatusRequest() {
  return apiRequest("/api/auth/totp");
}

export async function twoFactorEnrollStartRequest() {
  const result = await apiRequest("/api/auth/totp/begin", { method: "POST" });

  // The enrolment screen reads `otpauth_uri`; this API answers `uri`. Without
  // this the "add to your authenticator" field renders empty and the only way
  // through the flow is the manual base32 key beside it.
  return { ...result, otpauth_uri: result?.uri ?? "" };
}

export async function twoFactorEnrollConfirmRequest(code) {
  return apiRequest("/api/auth/totp/confirm", {
    method: "POST",
    body: { code },
  });
}

export async function twoFactorDisableRequest(password, code) {
  // The password is deliberately not sent. This platform proves the caller
  // still holds the second factor with a current code and asks for nothing
  // else, so forwarding a password would be posting a credential to an
  // endpoint that does not want it. The design's form still asks for it, and
  // the request is refused without a valid code either way.
  void password;

  return apiRequest("/api/auth/totp", {
    method: "DELETE",
    body: { code },
  });
}

/* ---------------------------------------------------------- team chat (v2)
 *
 * The redesigned Team Chat screen speaks in one company-wide stream plus the
 * direct messages and groups a person belongs to. All of it is the same
 * `team_channels` storage the older screen reads through
 * `frontend/src/api/teamChat.js`; these endpoints serve it in the shape that
 * screen was drawn for, rather than adding a second chat system beside it.
 */

export async function teamChatOptionsRequest() {
  return apiRequest("/api/team-chat/options");
}

export async function listTeamMessagesRequest({ limit = 100 } = {}) {
  return apiRequest(`/api/team-chat/stream?limit=${encodeURIComponent(limit)}`);
}

export async function sendTeamMessageRequest(payload) {
  return apiRequest("/api/team-chat/stream", {
    method: "POST",
    body: payload,
  });
}

export async function deleteTeamMessageRequest(messageId) {
  return apiRequest(
    `/api/team-chat/messages/${encodeURIComponent(messageId)}`,
    { method: "DELETE" },
  );
}

export async function listTeamRoomsRequest() {
  return apiRequest("/api/team-chat/rooms");
}

export async function createTeamDmRequest(userId) {
  return apiRequest("/api/team-chat/rooms/dm", {
    method: "POST",
    body: { user_id: userId },
  });
}

export async function createTeamGroupRequest({
  name,
  memberUserIds = [],
  department = null,
}) {
  return apiRequest("/api/team-chat/rooms/group", {
    method: "POST",
    body: {
      name,
      member_user_ids: memberUserIds,
      department,
    },
  });
}

export async function listTeamRoomMessagesRequest(
  roomId,
  { limit = 100 } = {},
) {
  const room = encodeURIComponent(roomId);

  return apiRequest(
    `/api/team-chat/rooms/${room}/messages?limit=${encodeURIComponent(limit)}`,
  );
}

export async function sendTeamRoomMessageRequest(roomId, payload) {
  const room = encodeURIComponent(roomId);

  return apiRequest(`/api/team-chat/rooms/${room}/messages`, {
    method: "POST",
    body: payload,
  });
}

export async function deleteTeamRoomMessageRequest(roomId, messageId) {
  const room = encodeURIComponent(roomId);
  const message = encodeURIComponent(messageId);

  return apiRequest(`/api/team-chat/rooms/${room}/messages/${message}`, {
    method: "DELETE",
  });
}


/* ------------------------------------------------------------------ *
 * Names the redesigned "Test & Train AI" screen imports.
 *
 * Adapters, like the task/appointment block above. The design branch this
 * screen came from served it from an /api/ai-teaching-chat router, whose
 * `/test` and `/chat-with-bot` both ran one shared reply pipeline.
 *
 * (Here and in the Publish block below, a path the *design* served but this
 * platform does not is written without backticks on purpose.
 * `tests/test_screens_call_real_routes.py` reads a quoted /api/... string in
 * this file as a path a screen requests, and reports it when the API has no
 * such route — which is exactly the check that should fail if one of these
 * ever became a real call.)
 *
 * On this platform that pipeline is `/api/ai-teaching/dry-run` — the real
 * assistant, run against a throwaway session, delivering nothing and storing
 * nothing (see `bot_profile_service.preview_reply`). Both design endpoints map
 * onto the one this platform already has, rather than a second copy of the
 * assistant being stood up beside it.
 * ------------------------------------------------------------------ */

export async function listAiTeachingChatRequest() {
  const result = await apiRequest("/api/ai-teaching/teaching-chat");

  // This API answers `items` like every other list route here; the screen
  // reads `messages`.
  return { ...result, messages: result?.items || [] };
}

/* The screen appends both halves of the turn straight into its message list and
 * renders each one's `id`. A response missing either — an intermediary that
 * rewrote the body, a stub that acknowledges every write — would put `undefined`
 * in that list and take the whole screen down on the next render. Refused here
 * so the page shows its own inline error instead of a blank panel. */
export async function sendAiTeachingChatRequest(text) {
  const result = await apiRequest("/api/ai-teaching/teaching-chat", {
    method: "POST",
    body: { text },
  });

  if (!result?.manager_message || !result?.assistant_message) {
    throw new Error("The assistant did not answer — nothing was saved.");
  }

  return result;
}

/* The design sent a `department` alongside the message so its pipeline could
 * scope knowledge to one section. This platform's preview does not take one:
 * the assistant decides the department itself from the message, which is what a
 * real customer message does too. Dropped explicitly rather than silently. The
 * field this API does take alongside the message is `language`, and the screen
 * offers no control for it, so it is left unset and the assistant answers in
 * the language the message was written in.
 *
 * The reply is the real one. `department_detected` and `knowledge_used` are
 * NOT: `preview_reply` returns the assistant's answer, its buttons and whether
 * the model path was used, and nothing below it reports which knowledge entry
 * was matched. They are left absent rather than filled with a guess — the
 * screen then shows "unknown", which is true, instead of a section this API
 * never named. */
function toDryRunPayload({ message, channel, department } = {}) {
  void department;

  return { message, channel: channel || "messenger" };
}

export async function testAiReplyRequest(values) {
  return apiRequest("/api/ai-teaching/dry-run", {
    method: "POST",
    body: toDryRunPayload(values),
  });
}

/* The design's plain employee-facing chat. Same pipeline, same endpoint — the
 * difference is only the permission, and on this platform that difference is
 * not the client's to make: the preview runs the real model and spends the
 * company's budget, so it stays behind `settings.manage` like every other way
 * of reaching it. An employee without it gets this API's own 403 message. */
export async function chatWithBotRequest(values) {
  const result = await apiRequest("/api/ai-teaching/dry-run", {
    method: "POST",
    body: toDryRunPayload(values),
  });

  return { reply: result?.reply || "" };
}

/* ------------------------------------------------------------------ *
 * Names the redesigned "Publish" screen imports.
 *
 * The design branch served these from an /api/scheduled-posts router, where one
 * row carried many channel accounts and published itself on demand. This
 * platform's publishing calendar is `/api/scheduler`: one row per post per
 * page, moving draft -> approved -> published, with a background publisher
 * (`channels/post_publisher.py`) that sends approved posts when they come due.
 *
 * The two vocabularies line up as follows, and every one of these translations
 * is applied here rather than in the screen:
 *
 *   design "scheduled" == this platform's "approved"  (ready, not yet sent)
 *   design "sent"      == this platform's "published"
 *   design's one post over N accounts == N posts, one per account
 *   design's per-account text override == that post's own body
 *   design's DELETE    == this platform's cancel (the row is kept, the audit
 *                        trail with it; it leaves every tab the screen shows)
 * ------------------------------------------------------------------ */

// The channels a post can be published to. Messaging channels (WhatsApp,
// Telegram) are not postable and the API refuses them, so they are filtered out
// before the screen can offer one.
const POSTABLE_CHANNELS = ["messenger", "instagram"];

const PUBLISH_STATUS_TO_API = {
  draft: "draft",
  scheduled: "approved",
  sent: "published",
  failed: "failed",
};

const API_STATUS_TO_PUBLISH = {
  draft: "draft",
  approved: "scheduled",
  published: "sent",
  failed: "failed",
};

const VIDEO_EXTENSIONS = ["mp4", "mov", "m4v", "webm", "avi", "mkv"];

/* The screen renders a <video> for "video" and an <img> for anything else.
 * This platform stores a media URL and no media type, so the type is read off
 * the URL rather than invented: a .mp4 in an <img> is a broken image on a card
 * that otherwise worked. */
function mediaTypeOf(url) {
  if (!url) return null;

  const extension = String(url).split("?")[0].split(".").pop().toLowerCase();

  return VIDEO_EXTENSIONS.includes(extension) ? "video" : "image";
}

async function postableChannelAccounts() {
  const result = await apiRequest("/api/channels");

  return (result?.items || []).filter((account) =>
    POSTABLE_CHANNELS.includes(account.channel),
  );
}

export async function scheduledPostOptionsRequest() {
  const accounts = await postableChannelAccounts();

  return {
    statuses: Object.keys(PUBLISH_STATUS_TO_API),
    channel_accounts: accounts.map((account) => ({
      id: account.id,
      channel: account.channel,
      name: account.name,
      status: account.status,
    })),
    // The design offered Post / Reel / Story per network. This platform's
    // publisher posts to the page feed and has no other form, so the list is
    // empty rather than naming types nothing would honour.
    post_types: [],
  };
}

/* One row here is one post on one page. `channel_account_ids` is always an
 * array because the screen maps over it unconditionally — a post whose page was
 * never chosen gets an empty one, not `undefined`. */
function toPublishPostRow(row = {}) {
  const accountId = row.channel_account_id;

  return {
    ...row,
    text: row.body ?? null,
    media_urls: row.media_url ? [row.media_url] : [],
    media_type: mediaTypeOf(row.media_url),
    channel_account_ids: accountId ? [accountId] : [],
    scheduled_at: row.scheduled_for ?? null,
    status: API_STATUS_TO_PUBLISH[row.status] ?? row.status,
    // The screen prints `results[*].error` under a failed post. This platform
    // records one attempt per row, so there is exactly one entry, keyed by the
    // page it was for.
    results: row.last_error
      ? { [String(accountId ?? row.id)]: { ok: false, error: row.last_error } }
      : {},
  };
}

export async function listScheduledPostsRequest({ status } = {}) {
  const result = await apiRequest(
    `/api/scheduler${createQueryString({
      status: status ? PUBLISH_STATUS_TO_API[status] || status : undefined,
    })}`,
  );

  return { ...result, items: (result?.items || []).map(toPublishPostRow) };
}

async function approveScheduledPost(postId) {
  try {
    await apiRequest(`/api/scheduler/${encodeURIComponent(postId)}/approve`, {
      method: "POST",
    });
  } catch (error) {
    // 409 is "already approved", which is the state the caller wanted.
    if (error.status !== 409) throw error;
  }
}

/* The design's dialog saves one post across every page the user ticked. This
 * API holds one post per page, so this creates that many — the per-page text
 * the dialog collected becomes each post's own body, which is exactly what the
 * override meant.
 *
 * Two of the design's fields have no home here and are dropped rather than
 * sent: the per-page post type (feed/reel/story — the publisher only posts to
 * the feed) and the media type (read back off the URL instead). A draft is
 * given a time because this API has no unscheduled draft; the screen never
 * shows a draft's time, and approving it is what makes the time mean
 * anything. */
export async function createScheduledPostRequest(values = {}) {
  const accountIds = values.channel_account_ids || [];
  const accounts = await postableChannelAccounts();
  const scheduledFor = values.scheduled_at || new Date().toISOString();
  const overrides = values.content_overrides || {};
  const created = [];

  for (const accountId of accountIds) {
    const account = accounts.find((item) => item.id === accountId);

    if (!account) {
      const error = new Error(
        "That page is no longer connected to this company.",
      );
      error.status = 404;
      throw error;
    }

    const body = String(
      overrides[accountId] ?? overrides[String(accountId)] ?? values.text ?? "",
    ).trim();

    const result = await apiRequest("/api/scheduler", {
      method: "POST",
      body: {
        channel: account.channel,
        channel_account_id: accountId,
        body,
        scheduled_for: scheduledFor,
        media_url: values.media_urls?.[0] || null,
      },
    });

    // "draft" is where a post starts here, so only the design's "scheduled"
    // needs the extra step — and it is this platform's approval, not a
    // shortcut past it: the caller already holds `scheduler.manage`, which is
    // what approving requires.
    if (values.status !== "draft") {
      await approveScheduledPost(result?.post?.id);
    }

    created.push(result?.post);
  }

  return { items: created };
}

/* The form the create route normalises every timestamp into. The queue's due
 * check is a plain string comparison, so a `...Z` written straight through the
 * edit route — which has no such normalisation — would sort against `+00:00`
 * rows on their punctuation rather than on their time. */
function utcOffsetIso(date) {
  return date.toISOString().replace(/\.\d{3}Z$/, "+00:00");
}

/* "Post now" on a queue-based publisher is "due now, and approved" — the
 * sweep in `backend/workers.py` picks it up on its next pass. There is no
 * endpoint that publishes inline, and pretending otherwise would report a post
 * as sent before anything had been sent. */
export async function publishScheduledPostNowRequest(postId) {
  await apiRequest(`/api/scheduler/${encodeURIComponent(postId)}`, {
    method: "PATCH",
    body: { scheduled_for: utcOffsetIso(new Date()) },
  });

  await approveScheduledPost(postId);

  return { status: "queued" };
}

/* The design deleted the row. This platform cancels it: a post that went out,
 * or was about to, is part of what the company did, and the calendar keeps its
 * record. A cancelled post is in none of the four tabs the screen shows, so it
 * disappears from the screen either way. */
export async function deleteScheduledPostRequest(postId) {
  return apiRequest(`/api/scheduler/${encodeURIComponent(postId)}/cancel`, {
    method: "POST",
  });
}
