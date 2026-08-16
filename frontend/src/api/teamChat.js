import { apiRequest, getAccessToken, handleUnauthorized } from "./client";

// client.js keeps its base URL private, so the SSE reader below resolves the
// same value the same way rather than reaching into that module.
const API_BASE_URL =
  import.meta.env.VITE_API_BASE_URL || "http://127.0.0.1:8000";

const BASE = "/api/team-chat";

function queryString(parameters) {
  const search = new URLSearchParams();

  Object.entries(parameters).forEach(([key, value]) => {
    if (value === undefined || value === null || value === "") return;
    search.set(key, String(value));
  });

  const value = search.toString();
  return value ? `?${value}` : "";
}

export async function getTeamChatOverviewRequest() {
  return apiRequest(`${BASE}/overview`);
}

export async function getTeamChannelsRequest() {
  return apiRequest(`${BASE}/channels`);
}

export async function createTeamChannelRequest({
  name,
  topic = "",
  isPrivate = false,
  memberUserIds = [],
}) {
  return apiRequest(`${BASE}/channels`, {
    method: "POST",
    body: {
      name,
      topic: topic || null,
      is_private: Boolean(isPrivate),
      member_user_ids: memberUserIds,
    },
  });
}

export async function getTeamChannelRequest(channelId) {
  return apiRequest(`${BASE}/channels/${encodeURIComponent(channelId)}`);
}

export async function joinTeamChannelRequest(channelId) {
  return apiRequest(`${BASE}/channels/${encodeURIComponent(channelId)}/join`, {
    method: "POST",
  });
}

export async function leaveTeamChannelRequest(channelId) {
  return apiRequest(`${BASE}/channels/${encodeURIComponent(channelId)}/leave`, {
    method: "POST",
  });
}

export async function addTeamChannelMemberRequest(channelId, userId) {
  return apiRequest(`${BASE}/channels/${encodeURIComponent(channelId)}/members`, {
    method: "POST",
    body: { user_id: Number(userId) },
  });
}

export async function getTeamChannelMembersRequest(channelId) {
  return apiRequest(`${BASE}/channels/${encodeURIComponent(channelId)}/members`);
}

export async function getTeamMessagesRequest(
  channelId,
  { limit = 50, beforeId = null } = {},
) {
  const query = queryString({ limit, before_id: beforeId });
  return apiRequest(
    `${BASE}/channels/${encodeURIComponent(channelId)}/messages${query}`,
  );
}

export async function postTeamMessageRequest(
  channelId,
  body,
  { linkedConversationId = null } = {},
) {
  return apiRequest(
    `${BASE}/channels/${encodeURIComponent(channelId)}/messages`,
    {
      method: "POST",
      body: { body, linked_conversation_id: linkedConversationId },
    },
  );
}

export async function editTeamMessageRequest(messageId, body) {
  return apiRequest(`${BASE}/messages/${encodeURIComponent(messageId)}`, {
    method: "PATCH",
    body: { body },
  });
}

export async function markTeamChannelReadRequest(channelId) {
  return apiRequest(`${BASE}/channels/${encodeURIComponent(channelId)}/read`, {
    method: "POST",
  });
}

export async function getTeamChatUnreadRequest() {
  return apiRequest(`${BASE}/unread`);
}

export async function getTeamChatDirectoryRequest() {
  return apiRequest(`${BASE}/directory`);
}

/**
 * Read the team chat SSE stream until `signal` aborts.
 *
 * Mirrors the conversation live reader: the browser's EventSource cannot send
 * an Authorization header, so the stream is read from a fetch body instead.
 */
export async function subscribeTeamChatEvents({
  channelId = null,
  onEvent,
  onOpen,
  onError,
  signal,
} = {}) {
  const token = getAccessToken();
  const headers = { Accept: "text/event-stream" };

  if (token) {
    headers.Authorization = `Bearer ${token}`;
  }

  const query = queryString({ channel_id: channelId });

  try {
    const response = await fetch(`${API_BASE_URL}${BASE}/live/events${query}`, {
      headers,
      signal,
      cache: "no-store",
    });

    if (!response.ok || !response.body) {
      if (response.status === 401) {
        handleUnauthorized();
      }

      throw new Error(`Live connection failed with status ${response.status}.`);
    }

    onOpen?.();

    const reader = response.body.getReader();
    const decoder = new TextDecoder();
    let buffer = "";

    while (!signal?.aborted) {
      const { value, done } = await reader.read();

      if (done) break;

      buffer += decoder.decode(value, { stream: true });

      const blocks = buffer.split("\n\n");
      buffer = blocks.pop() || "";

      blocks.forEach((block) => {
        if (!block || block.startsWith(":")) return;

        const lines = block.split("\n");
        const eventName =
          lines.find((line) => line.startsWith("event:"))?.slice(6).trim() ||
          "message";
        const dataText = lines
          .filter((line) => line.startsWith("data:"))
          .map((line) => line.slice(5).trim())
          .join("\n");

        if (!dataText) return;

        try {
          onEvent?.({ event: eventName, data: JSON.parse(dataText) });
        } catch {
          // Ignore a malformed frame rather than dropping the connection.
        }
      });
    }
  } catch (error) {
    if (error?.name !== "AbortError") {
      onError?.(error);
    }
  }
}
