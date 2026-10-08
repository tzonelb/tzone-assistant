import { useCallback, useEffect, useState } from "react";
import {
  cleanupDiagnosticEventsRequest,
  getDiagnosticsSummaryRequest,
  listDiagnosticEventsRequest,
} from "../../api/client";
import { EmptyState, ErrorState, LoadingState } from "../../components/common";
import "./DeveloperCenterPage.css";

function humanize(value) {
  return String(value || "").replaceAll("_", " ").replace(/\b\w/g, (letter) => letter.toUpperCase());
}

function formatDateTime(value) {
  if (!value) return "—";
  const normalized = /(?:Z|[+-]\d{2}:?\d{2})$/i.test(value) ? value : `${value}Z`;
  const date = new Date(normalized);
  return Number.isNaN(date.getTime()) ? "—" : date.toLocaleString();
}

const SEVERITIES = ["debug", "info", "warning", "error", "critical"];

// A technical event stream (webhook received, AI buffer started, send
// failed), kept apart from the customer-facing Timeline on purpose -- see
// backend/services/diagnostics_service.py's own docstring. Super admin only:
// the backend gate checks `is_super_admin` directly, not a company
// permission, so this screen is hidden from every other role regardless of
// what they are otherwise an owner or admin of.
export default function DeveloperCenterPage() {
  const [summary, setSummary] = useState(null);
  const [events, setEvents] = useState([]);
  const [severityFilter, setSeverityFilter] = useState("");
  const [eventTypeFilter, setEventTypeFilter] = useState("");
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState("");

  const [retentionDays, setRetentionDays] = useState(14);
  const [cleaning, setCleaning] = useState(false);
  const [cleanupStatus, setCleanupStatus] = useState("");

  const load = useCallback(async () => {
    setLoading(true);
    setError("");
    try {
      const [summaryResult, eventsResult] = await Promise.all([
        getDiagnosticsSummaryRequest(),
        listDiagnosticEventsRequest({
          severity: severityFilter || undefined,
          eventType: eventTypeFilter || undefined,
          limit: 150,
        }),
      ]);
      setSummary(summaryResult);
      setEvents(Array.isArray(eventsResult) ? eventsResult : []);
    } catch (requestError) {
      setError(requestError.message || "Could not load diagnostics.");
    } finally {
      setLoading(false);
    }
  }, [severityFilter, eventTypeFilter]);

  useEffect(() => { load(); }, [load]);

  async function runCleanup() {
    setCleaning(true);
    setCleanupStatus("");
    try {
      const result = await cleanupDiagnosticEventsRequest(retentionDays);
      setCleanupStatus(`Removed ${result?.deleted ?? 0} events older than ${result?.retention_days ?? retentionDays} days.`);
      await load();
    } catch (requestError) {
      setCleanupStatus(requestError.message || "Cleanup could not run.");
    } finally {
      setCleaning(false);
    }
  }

  const knownTypes = Array.from(new Set(events.map((event) => event.event_type))).sort();

  return (
    <div className="tzv2-devcenter">
      <p className="tzv2-devcenter-hint">
        The technical event stream behind every conversation — webhook received, AI buffer started, a send that failed —
        kept apart from the customer-facing Timeline. Super admin only.
      </p>

      {summary ? (
        <div className="tzv2-devcenter-summary">
          <div className="tzv2-devcenter-tile">
            <span>Events (24h)</span>
            <strong>{summary.total_events}</strong>
          </div>
          <div className="tzv2-devcenter-tile">
            <span>Incoming messages</span>
            <strong>{summary.incoming_messages}</strong>
          </div>
          <div className="tzv2-devcenter-tile">
            <span>Outgoing messages</span>
            <strong>{summary.outgoing_messages}</strong>
          </div>
          <div className="tzv2-devcenter-tile">
            <span>AI replies sent</span>
            <strong>{summary.ai_replies_sent}</strong>
          </div>
          <div className={`tzv2-devcenter-tile${summary.errors ? " is-warning" : ""}`}>
            <span>Errors</span>
            <strong>{summary.errors}</strong>
          </div>
        </div>
      ) : null}

      <div className="tzv2-devcenter-filters">
        <select className="input" value={severityFilter} onChange={(event) => setSeverityFilter(event.target.value)}>
          <option value="">All severities</option>
          {SEVERITIES.map((severity) => <option value={severity} key={severity}>{humanize(severity)}</option>)}
        </select>
        <select className="input" value={eventTypeFilter} onChange={(event) => setEventTypeFilter(event.target.value)}>
          <option value="">All event types</option>
          {knownTypes.map((type) => <option value={type} key={type}>{humanize(type)}</option>)}
        </select>

        <div className="tzv2-devcenter-cleanup">
          <label>
            Retention
            <input
              type="number"
              min={1}
              max={365}
              value={retentionDays}
              onChange={(event) => setRetentionDays(Number(event.target.value) || 14)}
            />
            days
          </label>
          <button type="button" className="btn btn-secondary" disabled={cleaning} onClick={runCleanup}>
            {cleaning ? "Cleaning up…" : "Clean up older events"}
          </button>
        </div>
      </div>

      {cleanupStatus ? <p className="tzv2-devcenter-cleanup-status">{cleanupStatus}</p> : null}

      {error ? (
        <ErrorState title="Could not load diagnostics" description={error} action={<button type="button" className="btn btn-primary" onClick={load}>Retry</button>} />
      ) : loading ? (
        <LoadingState label="Loading diagnostics…" />
      ) : events.length === 0 ? (
        <EmptyState title="No events" description="Nothing recorded yet for the current filters." />
      ) : (
        <div className="tzv2-devcenter-list">
          {events.map((event) => (
            <article className={`tzv2-devcenter-row is-${event.severity}`} key={event.id}>
              <div className="tzv2-devcenter-row-main">
                <strong>{humanize(event.event_type)}</strong>
                <span className={`tag ${event.severity === "error" || event.severity === "critical" ? "tag-accent-2" : "tag-neutral"}`}>
                  {humanize(event.severity)}
                </span>
                {event.status ? <span className="tzv2-devcenter-status">{event.status}</span> : null}
              </div>
              <div className="tzv2-devcenter-row-meta">
                <span>{event.channel || "—"}</span>
                <span>{event.external_user_id || "—"}</span>
                <time>{formatDateTime(event.created_at)}</time>
              </div>
            </article>
          ))}
        </div>
      )}
    </div>
  );
}
