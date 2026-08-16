import { apiRequest } from "./client";

/*
 * The post-comment queue.
 *
 * `replyToCommentRequest` can fail with 502 after the text has already been
 * stored: the reply is kept and the comment stays open because it is still
 * public and still unanswered. The caller must surface that instead of
 * reporting a successful send.
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

export async function getCommentsRequest({
  status = "",
  channel = "all",
  search = "",
  limit = 25,
  offset = 0,
} = {}) {
  const query = createQueryString({
    status,
    channel,
    search,
    limit,
    offset,
  });

  return apiRequest(`/api/comments${query}`);
}

export async function getCommentRequest(commentId) {
  return apiRequest(`/api/comments/${encodeURIComponent(commentId)}`);
}

export async function replyToCommentRequest(commentId, message) {
  return apiRequest(`/api/comments/${encodeURIComponent(commentId)}/reply`, {
    method: "POST",
    body: { message },
  });
}

export async function updateCommentStatusRequest(commentId, status) {
  return apiRequest(`/api/comments/${encodeURIComponent(commentId)}/status`, {
    method: "PATCH",
    body: { status },
  });
}
