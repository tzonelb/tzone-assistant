/*
 * The console's own HTTP layer.
 *
 * It deliberately duplicates the shape of api/client.js instead of importing it.
 * A platform session and a company session are two different credentials, and
 * sharing a module means sharing a token slot: signing in here would end up
 * either overwriting an employee's session or being overwritten by it. Nothing
 * in this file touches `tzone_access_token`.
 */

const API_BASE_URL =
  import.meta.env.VITE_API_BASE_URL ||
  "http://127.0.0.1:8000";

const TOKEN_KEY = "tzone_platform_token";

/*
 * Where the console is mounted in App.jsx. Navigation inside the portal is
 * written against this rather than with relative "..", because a route path
 * such as "companies/:companyId" is one route with two segments and ".." from
 * it lands on the console root instead of the list.
 */
export const CONSOLE_BASE_PATH = "/superadmin";

const LOGIN_PATH = `${CONSOLE_BASE_PATH}/login`;

export function getPlatformToken() {
  return localStorage.getItem(TOKEN_KEY);
}

export function savePlatformToken(token) {
  if (token) {
    localStorage.setItem(TOKEN_KEY, token);
  }
}

export function clearPlatformToken() {
  localStorage.removeItem(TOKEN_KEY);
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

  if (authenticated) {
    const token = getPlatformToken();

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

export async function platformLoginRequest(email, password) {
  return platformRequest("/api/platform/auth/login", {
    method: "POST",
    authenticated: false,
    body: { email, password },
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
