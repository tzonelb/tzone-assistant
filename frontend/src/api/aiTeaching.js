import { apiRequest } from "./client";

const BASE = "/api/ai-teaching";

export async function getAiTeachingProfileRequest() {
  return apiRequest(`${BASE}/profile`);
}

export async function updateAiTeachingProfileRequest(values) {
  return apiRequest(`${BASE}/profile`, {
    method: "PUT",
    body: values,
  });
}

export async function getAiTeachingPromptRequest(channel = "messenger") {
  return apiRequest(
    `${BASE}/profile/prompt?channel=${encodeURIComponent(channel)}`,
  );
}

export async function listAiTeachingProfilesRequest() {
  return apiRequest(`${BASE}/profiles`);
}

export async function updateAiTeachingProfileByIdRequest(profileId, values) {
  return apiRequest(`${BASE}/profiles/${encodeURIComponent(profileId)}`, {
    method: "PUT",
    body: values,
  });
}

/**
 * Runs the real assistant against a typed message and returns what it would
 * say. Nothing is delivered, stored or queued by this call.
 */
export async function runAiTeachingDryRunRequest({
  message,
  channel = "messenger",
  language = null,
}) {
  return apiRequest(`${BASE}/dry-run`, {
    method: "POST",
    body: { message, channel, language },
  });
}
