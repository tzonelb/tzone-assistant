import { useCallback, useEffect, useMemo, useState } from "react";
import { useNavigate } from "react-router-dom";
import { AddOutlined, RefreshOutlined } from "@mui/icons-material";

import { CONSOLE_BASE_PATH, listCompaniesRequest } from "../platformClient";
import { formatBytes, formatDate } from "../format";
import {
  ConsoleBanner,
  ConsoleButton,
  ConsoleEmpty,
  ConsoleLoading,
  ConsolePage,
  ConsolePanel,
  StatusChip,
} from "../components/ConsoleUI";


const STATUS_FILTERS = ["all", "active", "suspended"];


export default function CompaniesPage() {
  const navigate = useNavigate();

  const [companies, setCompanies] = useState([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState("");
  const [search, setSearch] = useState("");
  const [statusFilter, setStatusFilter] = useState("all");

  const loadCompanies = useCallback(async () => {
    setLoading(true);
    setError("");

    try {
      const result = await listCompaniesRequest();
      setCompanies(Array.isArray(result?.items) ? result.items : []);
    } catch (requestError) {
      setError(requestError.message || "Companies could not be loaded.");
      setCompanies([]);
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => {
    loadCompanies();
  }, [loadCompanies]);

  const visible = useMemo(() => {
    const needle = search.trim().toLowerCase();

    return companies.filter((company) => {
      if (statusFilter !== "all" && company.status !== statusFilter) {
        return false;
      }

      if (!needle) {
        return true;
      }

      return [
        company.name,
        company.slug,
        company.workspace_name,
        company.owner_email,
        company.owner_name,
        company.plan_code,
        company.plan_name,
      ]
        .filter(Boolean)
        .some((field) => String(field).toLowerCase().includes(needle));
    });
  }, [companies, search, statusFilter]);

  return (
    <ConsolePage
      eyebrow="CONTROL PLANE"
      title="Companies"
      description="Every company on the platform, including suspended ones."
      actions={
        <>
          <ConsoleButton onClick={loadCompanies}>
            <RefreshOutlined fontSize="small" />
            Refresh
          </ConsoleButton>

          <ConsoleButton
            variant="primary"
            onClick={() => navigate(`${CONSOLE_BASE_PATH}/companies/new`)}
          >
            <AddOutlined fontSize="small" />
            New company
          </ConsoleButton>
        </>
      }
    >
      <ConsolePanel>
        <div className="sa-toolbar">
          <input
            type="search"
            className="sa-search"
            value={search}
            placeholder="Search name, slug, workspace, owner or plan..."
            aria-label="Search companies"
            onChange={(event) => setSearch(event.target.value)}
          />

          <div className="sa-segmented" role="group" aria-label="Filter by status">
            {STATUS_FILTERS.map((value) => (
              <button
                key={value}
                type="button"
                className={statusFilter === value ? "is-active" : ""}
                onClick={() => setStatusFilter(value)}
              >
                {value === "all" ? "All" : value === "active" ? "Active" : "Suspended"}
              </button>
            ))}
          </div>

          <span className="sa-count">
            {visible.length} of {companies.length}
          </span>
        </div>

        <ConsoleBanner tone="error">{error}</ConsoleBanner>

        {loading ? <ConsoleLoading label="Loading companies..." /> : null}

        {!loading && !error && !visible.length ? (
          <ConsoleEmpty
            title="No company matches"
            description={
              companies.length
                ? "Adjust the search or the status filter."
                : "Create the first company to start using the platform."
            }
          />
        ) : null}

        {!loading && visible.length ? (
          <div className="sa-table-scroll">
            <table className="sa-table">
              <thead>
                <tr>
                  <th>Company</th>
                  <th>Status</th>
                  <th>Owner</th>
                  <th>Plan</th>
                  <th>Workspace</th>
                  <th className="is-numeric">Database</th>
                  <th>Created</th>
                </tr>
              </thead>

              <tbody>
                {visible.map((company) => (
                  <tr
                    key={company.id}
                    tabIndex={0}
                    role="link"
                    onClick={() =>
                      navigate(`${CONSOLE_BASE_PATH}/companies/${company.id}`)
                    }
                    onKeyDown={(event) => {
                      if (event.key === "Enter" || event.key === " ") {
                        event.preventDefault();
                        navigate(`${CONSOLE_BASE_PATH}/companies/${company.id}`);
                      }
                    }}
                  >
                    <td>
                      <strong>{company.name}</strong>
                      <span className="sa-subtle">{company.slug}</span>
                    </td>

                    <td>
                      <StatusChip status={company.status} />
                    </td>

                    <td>
                      <span>{company.owner_name || "—"}</span>
                      <span className="sa-subtle">{company.owner_email || "No owner"}</span>
                    </td>

                    <td>
                      <span>{company.plan_name || "No plan"}</span>
                      {company.plan_expires_at ? (
                        <span className="sa-subtle">
                          Until {formatDate(company.plan_expires_at)}
                        </span>
                      ) : null}
                    </td>

                    <td>
                      <span>{company.workspace_name || "—"}</span>
                      <span className="sa-subtle">{company.workspace_slug || ""}</span>
                    </td>

                    <td className="is-numeric">
                      <span>{formatBytes(company.database_bytes)}</span>
                      {company.database_exists ? null : (
                        <span className="sa-subtle is-danger">File missing</span>
                      )}
                    </td>

                    <td>{formatDate(company.created_at)}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        ) : null}
      </ConsolePanel>
    </ConsolePage>
  );
}
