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

export async function requestChannelVerificationRequest() {
  return apiRequest("/api/channels/verification/request", { method: "POST" });
}

export async function confirmChannelVerificationRequest(code) {
  return apiRequest("/api/channels/verification/confirm", {
    method: "POST",
    body: { code },
  });
}
