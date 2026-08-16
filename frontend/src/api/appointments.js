import { apiRequest } from "./client";

const BASE = "/api/appointments";

function queryString(parameters) {
  const search = new URLSearchParams();

  Object.entries(parameters).forEach(([key, value]) => {
    if (value === undefined || value === null || value === "") {
      return;
    }

    search.set(key, String(value));
  });

  const value = search.toString();
  return value ? `?${value}` : "";
}

export async function getAppointmentOptionsRequest() {
  return apiRequest(`${BASE}/options`);
}

export async function getAppointmentsRequest({
  startDate = "",
  endDate = "",
  staffUserId = "",
  customerId = "",
  status = "",
  includeCancelled = true,
  limit = 500,
  offset = 0,
} = {}) {
  const query = queryString({
    start_date: startDate,
    end_date: endDate,
    staff_user_id: staffUserId,
    customer_id: customerId,
    status,
    include_cancelled: includeCancelled,
    limit,
    offset,
  });

  return apiRequest(`${BASE}${query}`);
}

export async function getAppointmentRequest(appointmentId) {
  return apiRequest(`${BASE}/${encodeURIComponent(appointmentId)}`);
}

export async function createAppointmentRequest(values) {
  return apiRequest(BASE, { method: "POST", body: values });
}

export async function rescheduleAppointmentRequest(appointmentId, values) {
  return apiRequest(
    `${BASE}/${encodeURIComponent(appointmentId)}/reschedule`,
    { method: "PATCH", body: values },
  );
}

export async function cancelAppointmentRequest(appointmentId, reason = null) {
  return apiRequest(
    `${BASE}/${encodeURIComponent(appointmentId)}/cancel`,
    { method: "POST", body: { reason } },
  );
}

export async function updateAppointmentStatusRequest(appointmentId, status) {
  return apiRequest(
    `${BASE}/${encodeURIComponent(appointmentId)}/status`,
    { method: "PATCH", body: { status } },
  );
}

export async function getAvailabilityRulesRequest({
  staffUserId = "",
  weekday = "",
  activeOnly = false,
} = {}) {
  const query = queryString({
    staff_user_id: staffUserId,
    weekday,
    active_only: activeOnly,
  });

  return apiRequest(`${BASE}/availability${query}`);
}

export async function createAvailabilityRuleRequest(values) {
  return apiRequest(`${BASE}/availability`, {
    method: "POST",
    body: values,
  });
}

export async function updateAvailabilityRuleRequest(ruleId, values) {
  return apiRequest(
    `${BASE}/availability/${encodeURIComponent(ruleId)}`,
    { method: "PUT", body: values },
  );
}

export async function deleteAvailabilityRuleRequest(ruleId) {
  return apiRequest(
    `${BASE}/availability/${encodeURIComponent(ruleId)}`,
    { method: "DELETE" },
  );
}

export async function getAvailableSlotsRequest({
  staffUserId,
  date,
  durationMinutes = "",
} = {}) {
  const query = queryString({
    staff_user_id: staffUserId,
    date,
    duration_minutes: durationMinutes,
  });

  return apiRequest(`${BASE}/slots${query}`);
}
