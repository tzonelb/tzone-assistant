import { apiRequest } from "./client";

/*
 * Connected messaging accounts.
 *
 * Tokens are write-only: the server returns `has_access_token` /
 * `has_verify_token` booleans and never the value itself. On update, omitting a
 * token key keeps the stored one; sending an empty string clears it.
 */

export async function getChannelAccountsRequest() {
  return apiRequest("/api/channels");
}

export async function getChannelAccountRequest(accountId) {
  return apiRequest(`/api/channels/${encodeURIComponent(accountId)}`);
}

/*
 * Connecting or disconnecting a channel additionally requires an elevated
 * token from confirming a 6-digit code emailed to the account (see
 * requestChannelVerificationRequest / confirmChannelVerificationRequest
 * below). Editing an already-connected account does not.
 */

export async function createChannelAccountRequest(values, elevatedToken) {
  return apiRequest("/api/channels", {
    method: "POST",
    body: values,
    headers: { "X-Elevated-Token": elevatedToken },
  });
}

export async function updateChannelAccountRequest(accountId, values) {
  return apiRequest(`/api/channels/${encodeURIComponent(accountId)}`, {
    method: "PATCH",
    body: values,
  });
}

export async function deleteChannelAccountRequest(accountId, elevatedToken) {
  return apiRequest(`/api/channels/${encodeURIComponent(accountId)}`, {
    method: "DELETE",
    headers: { "X-Elevated-Token": elevatedToken },
  });
}

/*
 * Instagram (direct login) connects over two calls instead of one, because
 * Instagram itself can pause the login mid-way and ask for a 2FA code (see
 * backend/api/routes/instagram_direct.py). `start` returns either the
 * connected account or a `pending_id` naming that in-progress login;
 * `verify` is only called in the second case, with the code and that same
 * `pending_id`. Both need the same elevated grant `createChannelAccountRequest`
 * does, for the same reason: each one can establish a new credential.
 */

export async function startInstagramDirectConnectRequest(values, elevatedToken) {
  return apiRequest("/api/instagram-direct/connect/start", {
    method: "POST",
    body: values,
    headers: { "X-Elevated-Token": elevatedToken },
  });
}

export async function verifyInstagramDirectConnectRequest(values, elevatedToken) {
  return apiRequest("/api/instagram-direct/connect/verify", {
    method: "POST",
    body: values,
    headers: { "X-Elevated-Token": elevatedToken },
  });
}

/*
 * Facebook (cookie download) connects in one call, unlike Instagram (direct
 * login) above: the operator's cookies, exported from an already-signed-in
 * browser, are already fully authenticated, so there is no 2FA step for a
 * second call to resume (see backend/api/routes/facebook_direct.py).
 */

export async function connectFacebookDirectRequest(values, elevatedToken) {
  return apiRequest("/api/facebook-direct/connect", {
    method: "POST",
    body: values,
    headers: { "X-Elevated-Token": elevatedToken },
  });
}

export async function requestChannelVerificationRequest() {
  return apiRequest("/api/channels/verification/request", { method: "POST" });
}

export async function confirmChannelVerificationRequest(code) {
  return apiRequest("/api/channels/verification/confirm", {
    method: "POST",
    body: { code },
  });
}
