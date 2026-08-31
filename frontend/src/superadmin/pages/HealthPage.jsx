import { useCallback, useEffect, useState } from "react";
import { RefreshOutlined } from "@mui/icons-material";

import { platformHealthRequest } from "../platformClient";
import { formatBytes, formatCount, formatTimestamp } from "../format";
import {
  ConsoleBanner,
  ConsoleButton,
  ConsoleEmpty,
  ConsoleLoading,
  ConsolePage,
  ConsolePanel,
} from "../components/ConsoleUI";


export default function HealthPage() {
  const [health, setHealth] = useState(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState("");

  const loadHealth = useCallback(async () => {
    setLoading(true);
    setError("");

    try {
      setHealth(await platformHealthRequest());
    } catch (requestError) {
      setHealth(null);
      setError(requestError.message || "The health check could not be run.");
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => {
    loadHealth();
  }, [loadHealth]);

  return (
    <ConsolePage
      eyebrow="CONTROL PLANE"
      title="Platform health"
      description="Whether the platform can actually serve every company it lists."
      actions={
        <ConsoleButton onClick={loadHealth}>
          <RefreshOutlined fontSize="small" />
          Run again
        </ConsoleButton>
      }
    >
      <ConsoleBanner tone="error">{error}</ConsoleBanner>

      {loading ? <ConsoleLoading label="Checking the platform..." /> : null}

      {!loading && health ? (
        <>
          <ConsolePanel
            title={health.healthy ? "Healthy" : "Attention needed"}
            description={`Checked ${formatTimestamp(health.checked_at)}`}
            className={health.healthy ? "is-ok" : "is-danger"}
          >
            <div className="sa-metric-grid">
              <div className="sa-metric">
                <span>Companies</span>
                <strong>{formatCount(health.companies)}</strong>
              </div>

              <div className="sa-metric">
                <span>Active</span>
                <strong>{formatCount(health.active_companies)}</strong>
              </div>

              <div className="sa-metric">
                <span>Suspended</span>
                <strong>{formatCount(health.suspended_companies)}</strong>
              </div>

              <div className="sa-metric">
                <span>Provisioned databases</span>
                <strong>{formatCount(health.provisioned_databases)}</strong>
              </div>

              <div className="sa-metric">
                <span>Readable databases</span>
                <strong>{formatCount(health.readable_databases)}</strong>
              </div>

              <div className="sa-metric">
                <span>Storage in use</span>
                <strong>{formatBytes(health.total_database_bytes)}</strong>
              </div>

              <div className="sa-metric">
                <span>Platform admins</span>
                <strong>{formatCount(health.platform_admins)}</strong>
              </div>

              <div className="sa-metric">
                <span>Failed sign-ins</span>
                <strong>{formatCount(health.failed_logins_total)}</strong>
              </div>
            </div>
          </ConsolePanel>

          <ConsolePanel
            title="Email delivery"
            description="Whether password-reset links can actually be sent."
            className={health.email?.configured ? "is-ok" : "is-danger"}
          >
            <div className="sa-metric-grid">
              <div className="sa-metric">
                <span>Status</span>
                <strong>
                  {health.email?.configured ? "Configured" : "Not sending"}
                </strong>
              </div>

              <div className="sa-metric">
                <span>Backend</span>
                <strong>{health.email?.backend || "—"}</strong>
              </div>
            </div>

            <p className="sa-subtle">{health.email?.detail}</p>
          </ConsolePanel>

          <ConsolePanel
            title="Unreadable databases"
            description="A provisioned company whose database cannot be opened."
          >
            {health.unreadable_databases?.length ? (
              <ul className="sa-list">
                {health.unreadable_databases.map((entry) => (
                  <li key={entry.company_id}>
                    <strong>
                      {entry.name} (company #{entry.company_id})
                    </strong>
                    <span>{entry.error}</span>
                  </li>
                ))}
              </ul>
            ) : (
              <ConsoleEmpty title="Every provisioned database opened cleanly." />
            )}
          </ConsolePanel>

          <ConsolePanel
            title="Recent failed sign-ins"
            description="Across both the company door and this one."
          >
            {health.recent_failed_logins?.length ? (
              <div className="sa-table-scroll">
                <table className="sa-table">
                  <thead>
                    <tr>
                      <th>When</th>
                      <th>Email</th>
                      <th>Address</th>
                      <th>Reason</th>
                    </tr>
                  </thead>

                  <tbody>
                    {health.recent_failed_logins.map((attempt, index) => (
                      <tr key={`${attempt.created_at}-${index}`}>
                        <td>{formatTimestamp(attempt.created_at)}</td>
                        <td>{attempt.email || "—"}</td>
                        <td>{attempt.ip_address || "—"}</td>
                        <td>
                          <code className="sa-code">
                            {attempt.failure_reason || "unknown"}
                          </code>
                        </td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              </div>
            ) : (
              <ConsoleEmpty title="No failed sign-in has been recorded." />
            )}
          </ConsolePanel>
        </>
      ) : null}
    </ConsolePage>
  );
}
