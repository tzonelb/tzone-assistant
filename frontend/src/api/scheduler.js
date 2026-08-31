import { apiRequest } from "./client";

/*
 * The publishing calendar.
 *
 * `scheduled_for` crosses this boundary as a UTC ISO timestamp in both
 * directions. The screen converts to and from the display timezone; nothing
 * here guesses an offset.
 */

function createQueryString(parameters) {
  const searchParameters = new URLSearchParams();

  Object.entries(parameters).forEach(([key, value]) => {
    if (value === undefined || value === null || value === "") {
      return;
    }

    searchParameters.set(key, String(value));
  });

  const value = searchParameters.toString();
  return value ? `?${value}` : "";
}

export async function getScheduledPostsRequest({
  status = "",
  channel = "all",
  startsAfter = "",
  endsBefore = "",
  limit = 100,
  offset = 0,
} = {}) {
  const query = createQueryString({
    status,
    channel,
    starts_after: startsAfter,
    ends_before: endsBefore,
    limit,
    offset,
  });

  return apiRequest(`/api/scheduler${query}`);
}

export async function getScheduledPostRequest(postId) {
  return apiRequest(`/api/scheduler/${encodeURIComponent(postId)}`);
}

export async function createScheduledPostRequest(values) {
  return apiRequest("/api/scheduler", {
    method: "POST",
    body: values,
  });
}

export async function updateScheduledPostRequest(postId, values) {
  return apiRequest(`/api/scheduler/${encodeURIComponent(postId)}`, {
    method: "PATCH",
    body: values,
  });
}

export async function approveScheduledPostRequest(postId) {
  return apiRequest(`/api/scheduler/${encodeURIComponent(postId)}/approve`, {
    method: "POST",
  });
}

export async function cancelScheduledPostRequest(postId) {
  return apiRequest(`/api/scheduler/${encodeURIComponent(postId)}/cancel`, {
    method: "POST",
  });
}
