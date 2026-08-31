import { API_BASE_URL, apiRequest, handleUnauthorized } from "./client";

/*
 * The reporting API.
 *
 * One endpoint returns the whole report. The company is resolved from the
 * session on the server, so nothing here sends a company id.
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

/*
 * A report table as a CSV file, saved by the browser.
 *
 * Not `apiRequest`: that parses JSON, and this answer is a file. The filename
 * is whatever the server put in Content-Disposition rather than one invented
 * here, so an exported file is named by the thing that produced it — the same
 * way the conversation export works.
 */
export async function downloadAnalyticsReportRequest({
  report = "employees",
  days = 30,
} = {}) {
  const response = await fetch(
    `${API_BASE_URL}/api/analytics/export${createQueryString({ report, days })}`,
    // Auth is the httpOnly session cookie, not a bearer token.
    { credentials: "include" },
  );

  if (!response.ok) {
    if (response.status === 401) {
      handleUnauthorized();
    }

    throw new Error("The report could not be exported.");
  }

  const blob = await response.blob();
  const disposition = response.headers.get("content-disposition") || "";
  const match = disposition.match(/filename="([^"]+)"/);
  const url = URL.createObjectURL(blob);
  const anchor = document.createElement("a");

  anchor.href = url;
  anchor.download = match?.[1] || `analytics_${report}.csv`;
  document.body.appendChild(anchor);
  anchor.click();
  anchor.remove();
  URL.revokeObjectURL(url);
}
