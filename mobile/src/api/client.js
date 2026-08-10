import AsyncStorage from "@react-native-async-storage/async-storage";
import * as SecureStore from "expo-secure-store";

const TOKEN_KEY = "tzone_access_token";
const SERVER_KEY = "tzone_server_url";

// Default to the dev machine's LAN IP so a phone on the same Wi-Fi can reach
// the backend. Editable from the login screen and persisted.
export const DEFAULT_SERVER_URL = "http://192.168.166.171:8000";

let cachedBaseUrl = null;
let cachedToken = null;
let onUnauthorized = null;

export function setUnauthorizedHandler(handler) {
  onUnauthorized = handler;
}

export async function getServerUrl() {
  if (cachedBaseUrl) return cachedBaseUrl;
  const stored = await AsyncStorage.getItem(SERVER_KEY);
  cachedBaseUrl = (stored || DEFAULT_SERVER_URL).replace(/\/+$/, "");
  return cachedBaseUrl;
}

// SECURITY: only http://localhost/127.0.0.1/private-LAN dev servers may use
// plaintext HTTP (the shipped default is exactly that — a dev machine's LAN
// IP). Any other host must be HTTPS, otherwise the access token and every
// request/response cross the network in the clear — trivially interceptable
// on a shared/public Wi-Fi or by a phishing "point the app at this URL" trick.
function isAllowedServerUrl(url) {
  let parsed;
  try {
    parsed = new URL(url);
  } catch {
    return false;
  }
  if (parsed.protocol === "https:") return true;
  if (parsed.protocol !== "http:") return false;
  const host = parsed.hostname;
  return (
    host === "localhost" ||
    host === "127.0.0.1" ||
    /^10\.\d+\.\d+\.\d+$/.test(host) ||
    /^192\.168\.\d+\.\d+$/.test(host) ||
    /^172\.(1[6-9]|2\d|3[0-1])\.\d+\.\d+$/.test(host)
  );
}

export async function setServerUrl(url) {
  const clean = url.trim().replace(/\/+$/, "");
  if (!isAllowedServerUrl(clean)) {
    throw new Error(
      "For your security, only https:// servers (or a local/LAN address for development) are allowed."
    );
  }
  cachedBaseUrl = clean;
  await AsyncStorage.setItem(SERVER_KEY, cachedBaseUrl);
}

// SECURITY: the access token used to be stored in AsyncStorage, which is
// unencrypted plain storage on both iOS and Android — readable by anything
// with filesystem access (a rooted/jailbroken device, malware with storage
// permission, or an unencrypted device backup). expo-secure-store uses the
// OS Keychain (iOS) / EncryptedSharedPreferences+Keystore (Android).
export async function getToken() {
  if (cachedToken) return cachedToken;
  cachedToken = await SecureStore.getItemAsync(TOKEN_KEY);
  if (!cachedToken) {
    // One-time migration from the old unencrypted location, if present.
    const legacy = await AsyncStorage.getItem(TOKEN_KEY);
    if (legacy) {
      cachedToken = legacy;
      await SecureStore.setItemAsync(TOKEN_KEY, legacy);
      await AsyncStorage.removeItem(TOKEN_KEY);
    }
  }
  return cachedToken;
}

export async function setToken(token) {
  cachedToken = token;
  if (token) await SecureStore.setItemAsync(TOKEN_KEY, token);
  else await SecureStore.deleteItemAsync(TOKEN_KEY);
}

function normalizeError(status, data) {
  let message = "Request failed.";
  const detail = data && data.detail !== undefined ? data.detail : data;
  if (typeof detail === "string") message = detail;
  else if (Array.isArray(detail)) message = detail.map((d) => d.msg || "").join("\n") || message;
  else if (detail && typeof detail === "object") message = detail.message || detail.detail || detail.error || message;
  const err = new Error(message);
  err.status = status;
  err.data = data;
  return err;
}

export async function apiRequest(path, { method = "GET", body, authenticated = true, query } = {}) {
  const base = await getServerUrl();
  let url = `${base}${path}`;
  if (query) {
    const qs = Object.entries(query)
      .filter(([, v]) => v !== undefined && v !== null && v !== "")
      .map(([k, v]) => `${encodeURIComponent(k)}=${encodeURIComponent(v)}`)
      .join("&");
    if (qs) url += `?${qs}`;
  }
  const headers = { Accept: "application/json" };
  if (body !== undefined) headers["Content-Type"] = "application/json";
  if (authenticated) {
    const token = await getToken();
    if (token) headers.Authorization = `Bearer ${token}`;
  }

  let response;
  try {
    response = await fetch(url, {
      method,
      headers,
      body: body !== undefined ? JSON.stringify(body) : undefined,
    });
  } catch (networkErr) {
    const err = new Error("Cannot reach the server. Check the server address and your connection.");
    err.status = 0;
    throw err;
  }

  let data = null;
  try {
    data = await response.json();
  } catch (_) {
    data = null;
  }

  if (response.status === 401 && authenticated) {
    if (onUnauthorized) onUnauthorized();
    throw normalizeError(401, data || { detail: "Session expired. Please log in again." });
  }
  if (!response.ok) throw normalizeError(response.status, data);
  return data;
}

// ---- Auth ----
export function loginRequest({ company, email, password }) {
  return apiRequest("/api/auth/login", {
    method: "POST",
    body: { company, email, password },
    authenticated: false,
  });
}

export function verify2faRequest({ pendingToken, code }) {
  return apiRequest("/api/auth/2fa/verify", {
    method: "POST",
    body: { pending_token: pendingToken, code },
    authenticated: false,
  });
}

export function meRequest() {
  return apiRequest("/api/auth/me");
}

export function logoutRequest() {
  return apiRequest("/api/auth/logout", { method: "POST" });
}

// ---- Conversations ----
export function listConversationsRequest({ page = 1, pageSize = 100, folder = "inbox", channel = "all" } = {}) {
  return apiRequest("/conversations/", {
    query: { page, page_size: pageSize, folder, channel, status: "all", department: "all" },
  });
}

export function getMessagesRequest(channel, userId, { limit = 300, markRead = false } = {}) {
  return apiRequest(`/conversations/${encodeURIComponent(channel)}/${encodeURIComponent(userId)}`, {
    query: { limit, mark_read: markRead ? "true" : "false" },
  });
}

export function takeOverRequest(channel, userId) {
  return apiRequest(`/conversations/${encodeURIComponent(channel)}/${encodeURIComponent(userId)}/take-over`, {
    method: "POST",
  });
}

export function returnToAiRequest(channel, userId) {
  return apiRequest(`/conversations/${encodeURIComponent(channel)}/${encodeURIComponent(userId)}/return-to-ai`, {
    method: "POST",
  });
}

export function getControlRequest(channel, userId) {
  return apiRequest(`/conversations/${encodeURIComponent(channel)}/${encodeURIComponent(userId)}/control`);
}

export function updateControlRequest(channel, userId, patch) {
  return apiRequest(`/conversations/${encodeURIComponent(channel)}/${encodeURIComponent(userId)}/control`, {
    method: "PATCH",
    body: patch,
  });
}

export function getSavedRepliesRequest() {
  return apiRequest("/api/saved-replies");
}

export function getNotificationsSummaryRequest() {
  return apiRequest("/api/notifications/summary");
}

// ---- Customers ----
export function listCustomersRequest({ search = "", limit = 100, offset = 0 } = {}) {
  return apiRequest("/api/customers", { query: { search, limit, offset } });
}

export function getCustomerRequest(customerId) {
  return apiRequest(`/api/customers/${customerId}`);
}

export function getCustomerTimelineRequest(customerId) {
  return apiRequest(`/api/customers/${customerId}/timeline`);
}

// ---- Publish (scheduled posts + comment inbox) ----
export function listScheduledPostsRequest(status = "scheduled") {
  return apiRequest("/api/scheduled-posts", { query: { status } });
}

export function getScheduledPostOptionsRequest() {
  return apiRequest("/api/scheduled-posts/options");
}

export function listCommentPostsRequest() {
  return apiRequest("/api/comments/posts");
}

export function listPostCommentsRequest(postExternalId) {
  return apiRequest(`/api/comments/posts/${encodeURIComponent(postExternalId)}/comments`);
}

export function replyToCommentRequest(commentId, text) {
  return apiRequest(`/api/comments/${commentId}/reply`, { method: "POST", body: { text } });
}

export function sendReplyRequest(channel, userId, text) {
  return apiRequest(`/conversations/${encodeURIComponent(channel)}/${encodeURIComponent(userId)}/reply`, {
    method: "POST",
    body: { text },
  });
}
