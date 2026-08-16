import { useCallback, useEffect, useState } from "react";
import { RefreshOutlined } from "@mui/icons-material";

import { listAuditRequest } from "../platformClient";
import { formatTimestamp } from "../format";
import {
  ConsoleBanner,
  ConsoleButton,
  ConsoleEmpty,
  ConsoleLoading,
  ConsolePage,
  ConsolePanel,
} from "../components/ConsoleUI";


const PAGE_SIZE = 50;

function emptyFilters() {
  return { companyId: "", action: "", actorUserId: "" };
}


export default function AuditLogPage() {
  const [filters, setFilters] = useState(emptyFilters);
  const [applied, setApplied] = useState(emptyFilters);
  const [page, setPage] = useState(1);

  const [entries, setEntries] = useState([]);
  const [total, setTotal] = useState(0);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState("");

  const loadAudit = useCallback(async () => {
    setLoading(true);
    setError("");

    try {
      const result = await listAuditRequest({
        companyId: applied.companyId || null,
        action: applied.action,
        actorUserId: applied.actorUserId || null,
        limit: PAGE_SIZE,
        offset: (page - 1) * PAGE_SIZE,
      });

      setEntries(Array.isArray(result?.items) ? result.items : []);
      setTotal(Number(result?.total || 0));
    } catch (requestError) {
      setError(requestError.message || "The audit log could not be loaded.");
      setEntries([]);
      setTotal(0);
    } finally {
      setLoading(false);
    }
  }, [applied, page]);

  useEffect(() => {
    loadAudit();
  }, [loadAudit]);

  const lastPage = Math.max(1, Math.ceil(total / PAGE_SIZE));

  function handleApply(event) {
    event.preventDefault();
    setPage(1);
    setApplied({ ...filters });
  }

  return (
    <ConsolePage
      eyebrow="CONTROL PLANE"
      title="Audit log"
      description="Every control-plane action, newest first."
      actions={
        <ConsoleButton onClick={loadAudit}>
          <RefreshOutlined fontSize="small" />
          Refresh
        </ConsoleButton>
      }
    >
      <ConsolePanel>
        <form className="sa-inline-form" onSubmit={handleApply}>
          <label className="sa-field" htmlFor="sa-audit-action">
            <span>Action</span>

            <input
              id="sa-audit-action"
              type="text"
              value={filters.action}
              maxLength={80}
              placeholder="company.suspended"
              spellCheck={false}
              onChange={(event) =>
                setFilters((current) => ({ ...current, action: event.target.value }))
              }
            />
          </label>

          <label className="sa-field" htmlFor="sa-audit-company">
            <span>Company id</span>

            <input
              id="sa-audit-company"
              type="number"
              min="1"
              step="1"
              value={filters.companyId}
              onChange={(event) =>
                setFilters((current) => ({ ...current, companyId: event.target.value }))
              }
            />
          </label>

          <label className="sa-field" htmlFor="sa-audit-actor">
            <span>Actor user id</span>

            <input
              id="sa-audit-actor"
              type="number"
              min="1"
              step="1"
              value={filters.actorUserId}
              onChange={(event) =>
                setFilters((current) => ({
                  ...current,
                  actorUserId: event.target.value,
                }))
              }
            />
          </label>

          <ConsoleButton type="submit" variant="primary">
            Apply filters
          </ConsoleButton>

          <ConsoleButton
            onClick={() => {
              setFilters(emptyFilters());
              setApplied(emptyFilters());
              setPage(1);
            }}
          >
            Clear
          </ConsoleButton>
        </form>

        <p className="sa-note">
          The action filter matches the whole action name, not part of it.
        </p>

        <ConsoleBanner tone="error">{error}</ConsoleBanner>

        {loading ? <ConsoleLoading label="Loading audit entries..." /> : null}

        {!loading && !entries.length && !error ? (
          <ConsoleEmpty
            title="No audit entry matches"
            description="Clear the filters to see the full log."
          />
        ) : null}

        {!loading && entries.length ? (
          <>
            <div className="sa-table-scroll">
              <table className="sa-table">
                <thead>
                  <tr>
                    <th>When</th>
                    <th>Action</th>
                    <th>Actor</th>
                    <th>Company</th>
                    <th>Target</th>
                    <th>Details</th>
                  </tr>
                </thead>

                <tbody>
                  {entries.map((entry) => (
                    <tr key={entry.id}>
                      <td>
                        <span>{formatTimestamp(entry.created_at)}</span>
                        <span className="sa-subtle">{entry.ip_address || "—"}</span>
                      </td>

                      <td>
                        <code className="sa-code">{entry.action}</code>
                      </td>

                      <td>
                        <span>{entry.actor_email || "System"}</span>
                        {entry.actor_user_id ? (
                          <span className="sa-subtle">user #{entry.actor_user_id}</span>
                        ) : null}
                      </td>

                      <td>
                        <span>{entry.company_name || "—"}</span>
                        {entry.company_id ? (
                          <span className="sa-subtle">company #{entry.company_id}</span>
                        ) : null}
                      </td>

                      <td>
                        {entry.target_type
                          ? `${entry.target_type} #${entry.target_id ?? "—"}`
                          : "—"}
                      </td>

                      <td className="sa-audit-data">
                        {entry.data && Object.keys(entry.data).length ? (
                          <pre>{JSON.stringify(entry.data, null, 2)}</pre>
                        ) : (
                          "—"
                        )}
                      </td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>

            <div className="sa-pagination">
              <ConsoleButton
                disabled={page <= 1}
                onClick={() => setPage((current) => Math.max(1, current - 1))}
              >
                Previous
              </ConsoleButton>

              <span>
                Page {page} of {lastPage} · {total} entries
              </span>

              <ConsoleButton
                disabled={page >= lastPage}
                onClick={() => setPage((current) => current + 1)}
              >
                Next
              </ConsoleButton>
            </div>
          </>
        ) : null}
      </ConsolePanel>
    </ConsolePage>
  );
}
