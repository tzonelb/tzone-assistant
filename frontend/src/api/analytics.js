import { apiRequest } from "./client";

/*
 * The reporting API.
 *
 * One endpoint returns the whole report. The company is resolved from the
 * bearer token on the server, so nothing here sends a company id.
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

export async function getAnalyticsSummaryRequest({ days = 30 } = {}) {
  return apiRequest(`/api/analytics/summary${createQueryString({ days })}`);
}
