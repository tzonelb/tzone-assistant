import { useCallback, useEffect, useState } from "react";
import { RefreshOutlined } from "@mui/icons-material";

import {
  grantPlatformAdminRequest,
  listPlatformAdminsRequest,
  listPlatformUsersRequest,
  revokePlatformAdminRequest,
} from "../platformClient";
import { formatTimestamp } from "../format";
import {
  ConfirmDialog,
  ConsoleBanner,
  ConsoleButton,
  ConsoleEmpty,
  ConsoleLoading,
  ConsolePage,
  ConsolePanel,
  StatusChip,
} from "../components/ConsoleUI";


export default function PlatformAdminsPage() {
  const [admins, setAdmins] = useState([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState("");

  const [searchInput, setSearchInput] = useState("");
  const [search, setSearch] = useState("");
  const [candidates, setCandidates] = useState([]);
  const [candidatesLoading, setCandidatesLoading] = useState(true);
  const [candidatesError, setCandidatesError] = useState("");

  const [grantTarget, setGrantTarget] = useState(null);
  const [grantBusy, setGrantBusy] = useState(false);
  const [grantError, setGrantError] = useState("");
  const [grantStatus, setGrantStatus] = useState("");

  const [revokeTarget, setRevokeTarget] = useState(null);
  const [revokeBusy, setRevokeBusy] = useState(false);
  const [revokeError, setRevokeError] = useState("");

  const loadAdmins = useCallback(async () => {
    setLoading(true);
    setError("");

    try {
      const result = await listPlatformAdminsRequest();
      setAdmins(Array.isArray(result?.items) ? result.items : []);
    } catch (requestError) {
      setError(requestError.message || "Platform administrators could not be loaded.");
      setAdmins([]);
    } finally {
      setLoading(false);
    }
  }, []);

  const loadCandidates = useCallback(async () => {
    setCandidatesLoading(true);
    setCandidatesError("");

    try {
      const result = await listPlatformUsersRequest({ search, limit: 20 });
      setCandidates(Array.isArray(result?.items) ? result.items : []);
    } catch (requestError) {
      setCandidatesError(requestError.message || "Accounts could not be searched.");
      setCandidates([]);
    } finally {
      setCandidatesLoading(false);
    }
  }, [search]);

  useEffect(() => {
    loadAdmins();
  }, [loadAdmins]);

  useEffect(() => {
    loadCandidates();
  }, [loadCandidates]);

  useEffect(() => {
    const timeout = window.setTimeout(() => setSearch(searchInput.trim()), 300);
    return () => window.clearTimeout(timeout);
  }, [searchInput]);

  async function handleGrant() {
    if (!grantTarget) {
      return;
    }

    setGrantBusy(true);
    setGrantError("");
    setGrantStatus("");

    try {
      await grantPlatformAdminRequest(grantTarget.id);
      setGrantStatus(`${grantTarget.email} now administers the platform.`);
      setGrantTarget(null);
      await Promise.all([loadAdmins(), loadCandidates()]);
    } catch (requestError) {
      setGrantError(requestError.message || "The rights could not be granted.");
      setGrantTarget(null);
    } finally {
      setGrantBusy(false);
    }
  }

  async function handleRevoke() {
    if (!revokeTarget) {
      return;
    }

    setRevokeBusy(true);
    setRevokeError("");

    try {
      await revokePlatformAdminRequest(revokeTarget.id);
      setRevokeTarget(null);
      await loadAdmins();
    } catch (requestError) {
      // The service refuses to remove the last administrator, and its wording
      // explains what to do about it, so it is shown exactly as it arrived.
      setRevokeError(requestError.message);
      setRevokeTarget(null);
    } finally {
      setRevokeBusy(false);
    }
  }

  return (
    <ConsolePage
      eyebrow="CONTROL PLANE"
      title="Platform administrators"
      description="Accounts that can sign in to this console."
      actions={
        <ConsoleButton onClick={loadAdmins}>
          <RefreshOutlined fontSize="small" />
          Refresh
        </ConsoleButton>
      }
    >
      <ConsolePanel title="Administrators">
        <ConsoleBanner tone="error">{error}</ConsoleBanner>
        <ConsoleBanner tone="error">{revokeError}</ConsoleBanner>

        {loading ? <ConsoleLoading label="Loading administrators..." /> : null}

        {!loading && !admins.length && !error ? (
          <ConsoleEmpty title="No platform administrator is registered." />
        ) : null}

        {!loading && admins.length ? (
          <div className="sa-table-scroll">
            <table className="sa-table">
              <thead>
                <tr>
                  <th>Administrator</th>
                  <th>Account status</th>
                  <th>Last sign in</th>
                  <th>Since</th>
                  <th className="is-numeric">Actions</th>
                </tr>
              </thead>

              <tbody>
                {admins.map((admin) => (
                  <tr key={admin.id}>
                    <td>
                      <strong>{admin.full_name || admin.email}</strong>
                      <span className="sa-subtle">
                        {admin.email} · user #{admin.id}
                      </span>
                    </td>

                    <td>
                      <StatusChip status={admin.status} />
                    </td>

                    <td>{formatTimestamp(admin.last_login_at)}</td>
                    <td>{formatTimestamp(admin.created_at)}</td>

                    <td className="is-numeric">
                      <ConsoleButton
                        variant="danger"
                        onClick={() => {
                          setRevokeError("");
                          setRevokeTarget(admin);
                        }}
                      >
                        Revoke
                      </ConsoleButton>
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        ) : null}
      </ConsolePanel>

      <ConsolePanel
        title="Grant platform rights"
        description="Search the accounts registered on the platform and promote one to administrator."
      >
        <div className="sa-toolbar">
          <input
            type="search"
            className="sa-search"
            value={searchInput}
            placeholder="Search by email or name..."
            aria-label="Search platform accounts"
            onChange={(event) => setSearchInput(event.target.value)}
          />

          <span className="sa-count">
            {search ? `${candidates.length} match` : "Most recent accounts"}
          </span>
        </div>

        <ConsoleBanner tone="error">{candidatesError}</ConsoleBanner>
        <ConsoleBanner tone="error">{grantError}</ConsoleBanner>
        <ConsoleBanner tone="success">{grantStatus}</ConsoleBanner>

        {candidatesLoading ? <ConsoleLoading label="Searching accounts..." /> : null}

        {!candidatesLoading && !candidates.length && !candidatesError ? (
          <ConsoleEmpty
            title="No account matches"
            description="The search covers the email address and the full name."
          />
        ) : null}

        {!candidatesLoading && candidates.length ? (
          <div className="sa-table-scroll">
            <table className="sa-table">
              <thead>
                <tr>
                  <th>Account</th>
                  <th>Account status</th>
                  <th>Registered</th>
                  <th className="is-numeric">Platform rights</th>
                </tr>
              </thead>

              <tbody>
                {candidates.map((account) => (
                  <tr key={account.id}>
                    <td>
                      <strong>{account.full_name || account.email}</strong>
                      <span className="sa-subtle">
                        {account.email} · user #{account.id}
                      </span>
                    </td>

                    <td>
                      <StatusChip status={account.status} />
                    </td>

                    <td>{formatTimestamp(account.created_at)}</td>

                    <td className="is-numeric">
                      {account.is_super_admin ? (
                        <span className="sa-subtle">Already an administrator</span>
                      ) : account.status !== "active" ? (
                        <span className="sa-subtle">
                          Unavailable: the platform refuses rights to an account
                          that is not active
                        </span>
                      ) : (
                        <ConsoleButton
                          variant="primary"
                          onClick={() => {
                            setGrantError("");
                            setGrantStatus("");
                            setGrantTarget(account);
                          }}
                        >
                          Grant
                        </ConsoleButton>
                      )}
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        ) : null}
      </ConsolePanel>

      <ConfirmDialog
        open={Boolean(grantTarget)}
        title={`Grant platform rights to ${grantTarget?.email || ""}?`}
        confirmLabel="Grant rights"
        loading={grantBusy}
        onCancel={() => setGrantTarget(null)}
        onConfirm={handleGrant}
        message={
          <p>
            <strong>{grantTarget?.email}</strong> (user #{grantTarget?.id}) will
            be able to sign in to this console and administer every company on
            the platform, including creating, suspending and reconfiguring them.
          </p>
        }
      />

      <ConfirmDialog
        open={Boolean(revokeTarget)}
        title={`Revoke platform rights from ${revokeTarget?.full_name || revokeTarget?.email || ""}?`}
        confirmLabel="Revoke rights"
        loading={revokeBusy}
        onCancel={() => setRevokeTarget(null)}
        onConfirm={handleRevoke}
        message={
          <p>
            <strong>{revokeTarget?.email}</strong> (user #{revokeTarget?.id})
            loses access to this console immediately and every platform session
            they hold is revoked. Their company access is untouched.
          </p>
        }
      />
    </ConsolePage>
  );
}
