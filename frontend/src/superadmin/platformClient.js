/*
 * The console's own HTTP layer.
 *
 * It deliberately duplicates the shape of api/client.js instead of importing it.
 * A platform session and a company session are two different credentials, and
 * sharing a module means sharing a token slot: signing in here would end up
 * either overwriting an employee's session or being overwritten by it. Nothing
 * in this file touches `tzone_access_token`.
 */

// Same origin in production (see api/client.js): the console is served from the
// same domain as the API, so requests must be site-relative, not to the
// visitor's own :8000. Local vite dev keeps the explicit backend URL.
const API_BASE_URL =
  import.meta.env.VITE_API_BASE_URL ??
  (import.meta.env.PROD ? "" : "http://127.0.0.1:8000");

/*
 * The console session token is no longer kept here either, and for the same
 * reason with more at stake: this is the session that suspends companies and
 * rotates workspace codes. The server sets it as an httpOnly cookie; this file
 * only ever knows whether one exists.
 */
const SESSION_FLAG_KEY = "tzone_platform_signed_in";
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

/*
 * Where the console is mounted in App.jsx. Navigation inside the portal is
 * written against this rather than with relative "..", because a route path
 * such as "companies/:companyId" is one route with two segments and ".." from
 * it lands on the console root instead of the list.
 */
export const CONSOLE_BASE_PATH = "/superadmin";

const LOGIN_PATH = `${CONSOLE_BASE_PATH}/login`;

export function getPlatformToken() {
  /* Truthy while a console session exists. Not the token, and not sendable. */
  return localStorage.getItem(SESSION_FLAG_KEY);
}

export function savePlatformToken(token) {
  if (token) {
    localStorage.setItem(SESSION_FLAG_KEY, "1");
  }
}

export function clearPlatformToken() {
  localStorage.removeItem(SESSION_FLAG_KEY);
  /* The old key, for a browser that signed in before this change. Leaving it
   * would strand a real platform token in storage indefinitely. */
  localStorage.removeItem("tzone_platform_token");
}

let redirectingToLogin = false;

export function handlePlatformUnauthorized() {
  clearPlatformToken();

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

export async function platformRequest(
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

  /* No Authorization header: the browser sends the httpOnly session cookie
   * itself. A write echoes the CSRF token, which a page on another origin
   * cannot read. */
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
       * in development the console on :5173 and the API on :8000 are exactly
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
      handlePlatformUnauthorized();
    }

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

export async function platformLoginRequest(email, password, totpCode = "") {
  const body = { email, password };

  // Only sent once the account has a second factor and the sign-in form has
  // asked for the code; omitted entirely on the first (password-only) attempt.
  if (totpCode) {
    body.totp_code = totpCode;
  }

  return platformRequest("/api/platform/auth/login", {
    method: "POST",
    authenticated: false,
    body,
  });
}

export async function platformMeRequest() {
  return platformRequest("/api/platform/auth/me");
}

export async function platformLogoutRequest() {
  return platformRequest("/api/platform/auth/logout", {
    method: "POST",
  });
}

/*
 * Two-factor enrolment. These three routes are the only ones a super admin who
 * has not yet enrolled is allowed to reach; every other console call 403s until
 * `confirm` turns the second factor on.
 */
export async function platformTotpStatusRequest() {
  return platformRequest("/api/platform/auth/totp");
}

export async function platformTotpBeginRequest() {
  return platformRequest("/api/platform/auth/totp/begin", { method: "POST" });
}

export async function platformTotpConfirmRequest(code) {
  return platformRequest("/api/platform/auth/totp/confirm", {
    method: "POST",
    body: { code },
  });
}

export async function listCompaniesRequest() {
  return platformRequest("/api/platform/companies");
}

export async function createCompanyRequest(payload) {
  return platformRequest("/api/platform/companies", {
    method: "POST",
    body: payload,
  });
}

export async function companyDetailRequest(companyId) {
  return platformRequest(
    `/api/platform/companies/${encodeURIComponent(companyId)}`,
  );
}

export async function setCompanyStatusRequest(companyId, status, reason = null) {
  return platformRequest(
    `/api/platform/companies/${encodeURIComponent(companyId)}/status`,
    {
      method: "POST",
      body: { status, reason },
    },
  );
}

export async function rotateWorkspaceCodeRequest(companyId) {
  return platformRequest(
    `/api/platform/companies/${encodeURIComponent(companyId)}/workspace-code/rotate`,
    { method: "POST" },
  );
}

export async function assignPlanRequest(companyId, planCode, expiresAt = null) {
  return platformRequest(
    `/api/platform/companies/${encodeURIComponent(companyId)}/plan`,
    {
      method: "POST",
      body: { plan_code: planCode, expires_at: expiresAt },
    },
  );
}

export async function getCompanyConfigRequest(companyId) {
  return platformRequest(
    `/api/platform/companies/${encodeURIComponent(companyId)}/config`,
  );
}

export async function updateCompanyConfigRequest(companyId, payload) {
  return platformRequest(
    `/api/platform/companies/${encodeURIComponent(companyId)}/config`,
    {
      method: "PUT",
      body: payload,
    },
  );
}

export async function listPlansRequest() {
  return platformRequest("/api/platform/plans");
}

export async function createPlanRequest({ code, name, values = {} }) {
  return platformRequest("/api/platform/plans", {
    method: "POST",
    body: { code, name, values },
  });
}

/*
 * Only `values`. A plan's code is what every subscription row points at by
 * name, so the route accepts no new one and there is nothing here to send it
 * in: renaming a code would move every company on it onto a plan that no
 * longer exists.
 */
export async function updatePlanRequest(code, values) {
  return platformRequest(`/api/platform/plans/${encodeURIComponent(code)}`, {
    method: "PATCH",
    body: { values },
  });
}

export async function listPlatformAdminsRequest() {
  return platformRequest("/api/platform/admins");
}

export async function listPlatformUsersRequest({ search = "", limit = 20 } = {}) {
  const query = createQueryString({ search, limit });
  return platformRequest(`/api/platform/users${query}`);
}

export async function grantPlatformAdminRequest(userId) {
  return platformRequest(
    `/api/platform/admins/${encodeURIComponent(userId)}/grant`,
    { method: "POST" },
  );
}

export async function revokePlatformAdminRequest(userId) {
  return platformRequest(
    `/api/platform/admins/${encodeURIComponent(userId)}/revoke`,
    { method: "POST" },
  );
}

export async function platformHealthRequest() {
  return platformRequest("/api/platform/health");
}

export async function listAuditRequest({
  companyId = null,
  action = "",
  actorUserId = null,
  limit = 50,
  offset = 0,
} = {}) {
  const query = createQueryString({
    company_id: companyId,
    action,
    actor_user_id: actorUserId,
    limit,
    offset,
  });

  return platformRequest(`/api/platform/audit${query}`);
}
