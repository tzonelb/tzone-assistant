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
 * The sections this company offers its customers: the menu the assistant shows,
 * the quick-reply buttons it renders, and the department list in its prompt.
 * They belong to this company alone — there is no shared default, so a company
 * that defines none is shown no menu rather than another company's.
 */
export async function listBusinessDepartmentsRequest() {
  return apiRequest(`${BASE}/departments`);
}

export async function createBusinessDepartmentRequest(values) {
  return apiRequest(`${BASE}/departments`, {
    method: "POST",
    body: values,
  });
}

export async function updateBusinessDepartmentRequest(departmentId, values) {
  return apiRequest(`${BASE}/departments/${encodeURIComponent(departmentId)}`, {
    method: "PUT",
    body: values,
  });
}

export async function deleteBusinessDepartmentRequest(departmentId) {
  return apiRequest(`${BASE}/departments/${encodeURIComponent(departmentId)}`, {
    method: "DELETE",
  });
}

export async function reorderBusinessDepartmentsRequest(departmentIds) {
  return apiRequest(`${BASE}/departments/reorder`, {
    method: "POST",
    body: { department_ids: departmentIds },
  });
}

/**
 * How this company answers: whether a welcome is sent and how often, whether
 * the assistant may reply with no knowledge match, how confident a match must
 * be, how many knowledge items reach the model, whether buttons are shown.
 *
 * One company default plus an optional override per channel. The platform's
 * shipped values are the floor underneath both, and a setting that is not
 * overridden inherits rather than holding a copy — which is why clearing is its
 * own call rather than "save the inherited value again".
 */
export async function getReplyPolicyRequest() {
  return apiRequest(`${BASE}/reply-policy`);
}

export async function updateReplyPolicyDefaultRequest({
  values = {},
  clear = [],
} = {}) {
  return apiRequest(`${BASE}/reply-policy`, {
    method: "PUT",
    body: { values, clear },
  });
}

export async function updateReplyPolicyChannelRequest(
  channel,
  { values = {}, clear = [] } = {},
) {
  return apiRequest(
    `${BASE}/reply-policy/channels/${encodeURIComponent(channel)}`,
    {
      method: "PUT",
      body: { values, clear },
    },
  );
}

/** Drops the channel's whole override, so it inherits the company default again. */
export async function clearReplyPolicyChannelRequest(channel) {
  return apiRequest(
    `${BASE}/reply-policy/channels/${encodeURIComponent(channel)}`,
    {
      method: "DELETE",
    },
  );
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
