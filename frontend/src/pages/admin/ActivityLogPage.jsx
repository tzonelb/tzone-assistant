import { useCallback, useEffect, useState } from "react";
import { listActivityLogRequest } from "../../api/client";
import { EmptyState, ErrorState, LoadingState } from "../../components/common";
import "./ActivityLogPage.css";

function humanize(value) {
  return String(value || "").replaceAll("_", " ").replace(/\b\w/g, (letter) => letter.toUpperCase());
}

function formatDateTime(value) {
  if (!value) return "—";
  const normalized = /(?:Z|[+-]\d{2}:?\d{2})$/i.test(value) ? value : `${value}Z`;
  const date = new Date(normalized);
  return Number.isNaN(date.getTime()) ? "—" : date.toLocaleString();
}

// A real, company-scoped trail of the actions employees take outside a
// single conversation (tasks, customers, catalogue, broadcasts, roles) —
// each conversation's own Timeline already covers per-conversation events
// in full, so this deliberately doesn't duplicate those here.
export default function ActivityLogPage() {
  const [items, setItems] = useState([]);
  const [actions, setActions] = useState([]);
  const [employees, setEmployees] = useState([]);
  const [actorFilter, setActorFilter] = useState("");
  const [actionFilter, setActionFilter] = useState("");
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState("");

  const load = useCallback(async () => {
    setLoading(true);
    setError("");
    try {
      const result = await listActivityLogRequest({
        actorUserId: actorFilter || undefined,
        action: actionFilter || undefined,
        limit: 150,
      });
      setItems(Array.isArray(result?.items) ? result.items : []);
      setActions(Array.isArray(result?.actions) ? result.actions : []);
      setEmployees(Array.isArray(result?.employees) ? result.employees : []);
    } catch (requestError) {
      setError(requestError.message || "Could not load the activity log.");
    } finally {
      setLoading(false);
    }
  }, [actorFilter, actionFilter]);

  useEffect(() => { load(); }, [load]);

  return (
    <div className="tzv2-activity-log">
      <p className="tzv2-activity-log-hint">
        Every task, customer, catalogue, broadcast and role/permission change made by your team — newest first.
        Per-conversation events (takeover, transfer, status) stay on that conversation's own Timeline instead of duplicating here.
      </p>

      <div className="tzv2-activity-log-filters">
        <select className="input" value={actorFilter} onChange={(event) => setActorFilter(event.target.value)}>
          <option value="">All employees</option>
          {employees.map((employee) => <option value={employee.id} key={employee.id}>{employee.display_name}</option>)}
        </select>
        <select className="input" value={actionFilter} onChange={(event) => setActionFilter(event.target.value)}>
          <option value="">All action types</option>
          {actions.map((action) => <option value={action} key={action}>{humanize(action)}</option>)}
        </select>
      </div>

      {error ? <ErrorState title="Could not load activity" description={error} action={<button type="button" className="btn btn-primary" onClick={load}>Retry</button>} /> : null}

      {loading ? (
        <LoadingState label="Loading activity…" />
      ) : items.length === 0 ? (
        <EmptyState title="No activity yet" description="Actions your team takes on tasks, customers, catalogue, broadcasts and roles will show up here." />
      ) : (
        <div className="tzv2-activity-log-list">
          {items.map((item) => (
            <article className="tzv2-activity-log-row" key={item.id}>
              <div className="tzv2-activity-log-row-main">
                <strong>{item.description || humanize(item.action)}</strong>
                <span className="tag tag-neutral">{humanize(item.action)}</span>
              </div>
              <div className="tzv2-activity-log-row-meta">
                <span>{item.actor_name}</span>
                <time>{formatDateTime(item.created_at)}</time>
              </div>
            </article>
          ))}
        </div>
      )}
    </div>
  );
}
