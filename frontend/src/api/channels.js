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

export async function createChannelAccountRequest(values) {
  return apiRequest("/api/channels", {
    method: "POST",
    body: values,
  });
}

export async function updateChannelAccountRequest(accountId, values) {
  return apiRequest(`/api/channels/${encodeURIComponent(accountId)}`, {
    method: "PATCH",
    body: values,
  });
}

export async function deleteChannelAccountRequest(accountId) {
  return apiRequest(`/api/channels/${encodeURIComponent(accountId)}`, {
    method: "DELETE",
  });
}
