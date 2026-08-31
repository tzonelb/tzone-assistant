import { useCallback, useEffect, useState } from "react";
import { useNavigate, useParams } from "react-router-dom";
import { ArrowBackOutlined, RefreshOutlined } from "@mui/icons-material";

import {
  CONSOLE_BASE_PATH,
  assignPlanRequest,
  companyDetailRequest,
  listPlansRequest,
  rotateWorkspaceCodeRequest,
  setCompanyStatusRequest,
} from "../platformClient";
import { formatBytes, formatCount, formatTimestamp, humanize } from "../format";
import CompanyConfigEditor from "../components/CompanyConfigEditor";
import {
  ConfirmDialog,
  ConsoleBanner,
  ConsoleButton,
  ConsoleLoading,
  ConsolePage,
  ConsolePanel,
  StatusChip,
  WorkspaceCodeReveal,
} from "../components/ConsoleUI";


function IdentityRow({ label, value }) {
  return (
    <div className="sa-definition">
      <span>{label}</span>
      <strong>{value ?? "—"}</strong>
    </div>
  );
}


export default function CompanyDetailPage() {
  const { companyId } = useParams();
  const navigate = useNavigate();

  const [detail, setDetail] = useState(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState("");

  const [plans, setPlans] = useState([]);
  const [plansError, setPlansError] = useState("");

  const [statusReason, setStatusReason] = useState("");
  const [statusBusy, setStatusBusy] = useState(false);
  const [statusError, setStatusError] = useState("");
  const [suspendOpen, setSuspendOpen] = useState(false);

  const [planCode, setPlanCode] = useState("");
  const [planExpiry, setPlanExpiry] = useState("");
  const [planBusy, setPlanBusy] = useState(false);
  const [planError, setPlanError] = useState("");
  const [planStatus, setPlanStatus] = useState("");

  const [rotateOpen, setRotateOpen] = useState(false);
  const [rotateBusy, setRotateBusy] = useState(false);
  const [rotateError, setRotateError] = useState("");
  const [rotated, setRotated] = useState(null);

  const loadDetail = useCallback(async () => {
    setLoading(true);
    setError("");

    try {
      const result = await companyDetailRequest(companyId);
      setDetail(result);
      setPlanCode(result?.company?.plan_code || "");
      setPlanExpiry(result?.company?.plan_expires_at || "");
    } catch (requestError) {
      setDetail(null);
      setError(requestError.message || "This company could not be loaded.");
    } finally {
      setLoading(false);
    }
  }, [companyId]);

  const loadPlans = useCallback(async () => {
    setPlansError("");

    try {
      const result = await listPlansRequest();
      setPlans(Array.isArray(result?.items) ? result.items : []);
    } catch (requestError) {
      setPlans([]);
      setPlansError(requestError.message || "Plans could not be loaded.");
    }
  }, []);

  useEffect(() => {
    loadDetail();
    loadPlans();
  }, [loadDetail, loadPlans]);

  const company = detail?.company;
  const statistics = detail?.statistics;
  const suspended = company?.status !== "active";

  async function applyStatus(nextStatus) {
    setStatusBusy(true);
    setStatusError("");

    try {
      await setCompanyStatusRequest(
        companyId,
        nextStatus,
        statusReason.trim() || null,
      );
      setSuspendOpen(false);
      setStatusReason("");
      await loadDetail();
    } catch (requestError) {
      setStatusError(requestError.message || "The status could not be changed.");
    } finally {
      setStatusBusy(false);
    }
  }

  async function handleAssignPlan(event) {
    event.preventDefault();

    setPlanBusy(true);
    setPlanError("");
    setPlanStatus("");

    try {
      const result = await assignPlanRequest(
        companyId,
        planCode,
        planExpiry.trim() || null,
      );
      setPlanStatus(`Company moved to ${result?.plan_name || result?.plan_code}.`);
      await loadDetail();
    } catch (requestError) {
      setPlanError(requestError.message || "The plan could not be assigned.");
    } finally {
      setPlanBusy(false);
    }
  }

  async function handleRotate() {
    setRotateBusy(true);
    setRotateError("");

    try {
      const result = await rotateWorkspaceCodeRequest(companyId);
      setRotated(result);
      setRotateOpen(false);
      await loadDetail();
    } catch (requestError) {
      setRotateError(requestError.message || "The workspace code was not rotated.");
      setRotateOpen(false);
    } finally {
      setRotateBusy(false);
    }
  }

  if (loading) {
    return (
      <ConsolePage title="Company">
        <ConsoleLoading label="Loading company..." />
      </ConsolePage>
    );
  }

  if (error || !company) {
    return (
      <ConsolePage
        title="Company"
        actions={
          <ConsoleButton onClick={() => navigate(`${CONSOLE_BASE_PATH}/companies`)}>
            <ArrowBackOutlined fontSize="small" />
            Back to companies
          </ConsoleButton>
        }
      >
        <ConsoleBanner tone="error">{error || "Company not found."}</ConsoleBanner>
      </ConsolePage>
    );
  }

  return (
    <ConsolePage
      eyebrow={`COMPANY #${company.id}`}
      title={company.name}
      description={`${company.slug} · workspace ${company.workspace_name || "—"}`}
      actions={
        <>
          <ConsoleButton onClick={() => navigate(`${CONSOLE_BASE_PATH}/companies`)}>
            <ArrowBackOutlined fontSize="small" />
            Companies
          </ConsoleButton>

          <ConsoleButton onClick={loadDetail}>
            <RefreshOutlined fontSize="small" />
            Refresh
          </ConsoleButton>
        </>
      }
    >
      <ConsolePanel
        title="Identity and status"
        actions={<StatusChip status={company.status} />}
      >
        <div className="sa-definition-grid">
          <IdentityRow label="Owner" value={company.owner_name} />
          <IdentityRow label="Owner email" value={company.owner_email} />
          <IdentityRow label="Workspace" value={company.workspace_name} />
          <IdentityRow label="Workspace status" value={humanize(company.workspace_status)} />
          <IdentityRow label="Country" value={company.country} />
          <IdentityRow label="Currency" value={company.currency} />
          <IdentityRow label="Timezone" value={company.timezone} />
          <IdentityRow label="Default language" value={company.default_language} />
          <IdentityRow label="Created" value={formatTimestamp(company.created_at)} />
          <IdentityRow label="Provisioned" value={formatTimestamp(company.provisioned_at)} />
          <IdentityRow label="Schema version" value={company.schema_version} />
          <IdentityRow
            label="Code last rotated"
            value={
              company.code_rotated_at
                ? formatTimestamp(company.code_rotated_at)
                : "Never rotated"
            }
          />
          <IdentityRow label="Active employees" value={formatCount(company.employee_count)} />
          <IdentityRow
            label="Database file"
            value={company.database_exists ? "Present" : "Missing"}
          />
        </div>

        <ConsoleBanner tone="error">{statusError}</ConsoleBanner>

        <div className="sa-form-actions">
          {suspended ? (
            <ConsoleButton
              variant="primary"
              loading={statusBusy}
              onClick={() => applyStatus("active")}
            >
              Reactivate company
            </ConsoleButton>
          ) : (
            <ConsoleButton
              variant="danger"
              onClick={() => setSuspendOpen(true)}
            >
              Suspend company
            </ConsoleButton>
          )}
        </div>
      </ConsolePanel>

      <ConsolePanel
        title="Statistics"
        description="Row counts and file size, read from this company's database."
      >
        <p className="sa-note is-strong">
          These are counts. The console can measure how much this company stores
          but cannot read any of it — no conversation, message or customer of
          this company is reachable from the platform console.
        </p>

        {detail.statistics_error ? (
          <ConsoleBanner tone="warning">{detail.statistics_error}</ConsoleBanner>
        ) : null}

        {statistics ? (
          <div className="sa-metric-grid">
            {Object.entries(statistics)
              .filter(([key]) => key !== "database_bytes")
              .map(([key, value]) => (
                <div key={key} className="sa-metric">
                  <span>{humanize(key)}</span>
                  <strong>{formatCount(value)}</strong>
                </div>
              ))}

            <div className="sa-metric">
              <span>Database size</span>
              <strong>{formatBytes(statistics.database_bytes)}</strong>
            </div>
          </div>
        ) : null}
      </ConsolePanel>

      <ConsolePanel
        title="Plan"
        description={
          company.plan_name
            ? `Currently on ${company.plan_name} (${company.plan_code}).`
            : "This company is not on a plan."
        }
      >
        <ConsoleBanner tone="error">{plansError}</ConsoleBanner>

        <form className="sa-inline-form" onSubmit={handleAssignPlan}>
          <label className="sa-field" htmlFor="sa-plan-code">
            <span>Plan</span>

            <select
              id="sa-plan-code"
              value={planCode}
              onChange={(event) => setPlanCode(event.target.value)}
              required
            >
              <option value="" disabled>
                Choose a plan
              </option>

              {plans.map((plan) => (
                <option key={plan.code} value={plan.code}>
                  {plan.name} — {plan.code}
                </option>
              ))}
            </select>
          </label>

          <label className="sa-field" htmlFor="sa-plan-expiry">
            <span>Expires at</span>

            <input
              id="sa-plan-expiry"
              type="date"
              value={String(planExpiry).slice(0, 10)}
              onChange={(event) => setPlanExpiry(event.target.value)}
            />
          </label>

          <ConsoleButton
            type="submit"
            variant="primary"
            loading={planBusy}
            disabled={!planCode}
          >
            Assign plan
          </ConsoleButton>
        </form>

        <p className="sa-note">Leave the date empty for a plan that does not expire.</p>

        <ConsoleBanner tone="error">{planError}</ConsoleBanner>
        <ConsoleBanner tone="success">{planStatus}</ConsoleBanner>
      </ConsolePanel>

      <ConsolePanel
        title="Workspace code"
        description="Employees of this company prove possession of the code when they sign in."
      >
        <p className="sa-note">
          Rotating issues a new code and stops the old one working immediately.
          The company's data is not re-encrypted — only the copy of its key that
          the code unseals is replaced.
        </p>

        <ConsoleBanner tone="error">{rotateError}</ConsoleBanner>

        {rotated ? (
          <WorkspaceCodeReveal
            code={rotated.workspace_code}
            notice={rotated.workspace_code_notice}
          >
            <ConsoleButton onClick={() => setRotated(null)}>
              I have saved it
            </ConsoleButton>
          </WorkspaceCodeReveal>
        ) : (
          <div className="sa-form-actions">
            <ConsoleButton variant="danger" onClick={() => setRotateOpen(true)}>
              Rotate workspace code
            </ConsoleButton>
          </div>
        )}
      </ConsolePanel>

      {detail.platform_config ? (
        <CompanyConfigEditor
          companyId={companyId}
          config={detail.platform_config}
          onSaved={(saved) =>
            setDetail((current) =>
              current ? { ...current, platform_config: saved } : current,
            )
          }
        />
      ) : null}

      <ConfirmDialog
        open={suspendOpen}
        title={`Suspend ${company.name}?`}
        confirmLabel="Suspend company"
        loading={statusBusy}
        onCancel={() => setSuspendOpen(false)}
        onConfirm={() => applyStatus("suspended")}
        message={
          <>
            <p>
              Every employee of <strong>{company.name}</strong> ({company.slug})
              is signed out immediately and cannot sign in again until the
              company is reactivated.
            </p>

            <label className="sa-field" htmlFor="sa-suspend-reason">
              <span>Reason (recorded in the audit log)</span>

              <input
                id="sa-suspend-reason"
                type="text"
                value={statusReason}
                maxLength={500}
                onChange={(event) => setStatusReason(event.target.value)}
              />
            </label>
          </>
        }
      />

      <ConfirmDialog
        open={rotateOpen}
        title={`Rotate the workspace code for ${company.name}?`}
        confirmLabel="Rotate the code"
        loading={rotateBusy}
        onCancel={() => setRotateOpen(false)}
        onConfirm={handleRotate}
        message={
          <p>
            The current code for <strong>{company.name}</strong> stops working
            the moment this completes, and every employee has to be given the
            new one. The new code is displayed once and cannot be recovered
            afterwards.
          </p>
        }
      />
    </ConsolePage>
  );
}
