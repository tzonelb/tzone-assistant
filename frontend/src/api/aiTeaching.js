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
