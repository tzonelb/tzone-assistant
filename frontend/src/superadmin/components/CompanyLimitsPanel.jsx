import { useCallback, useEffect, useState } from "react";

import {
  clearCompanyLimitRequest,
  companyLimitsRequest,
  companyUsageRequest,
  setCompanyLimitRequest,
} from "../platformClient";
import { formatCount, humanize } from "../format";
import { ConsoleBanner, ConsoleButton, ConsolePanel } from "./ConsoleUI";


// 0 is this platform's "no ceiling" value (see plan_service.UNLIMITED) --
// shown as a word, not the number a company would otherwise read as "zero
// allowed".
function formatLimit(value) {
  return Number(value) === 0 ? "Unlimited" : formatCount(value);
}

function LimitRow({ limitKey, value, source, draft, onDraftChange, onSave, onClear, saving }) {
  return (
    <tr>
      <td>
        <strong>{humanize(limitKey)}</strong>
      </td>
      <td className="is-numeric">{formatLimit(value)}</td>
      <td>
        <span className={`sa-chip ${source === "override" ? "is-ok" : "is-muted"}`}>
          {source === "override" ? "Override" : "Plan default"}
        </span>
      </td>
      <td>
        <div className="sa-inline-form sa-limit-editor">
          <input
            type="number"
            min={0}
            placeholder="New value"
            value={draft.value}
            onChange={(event) => onDraftChange(limitKey, { ...draft, value: event.target.value })}
          />
          <input
            type="text"
            placeholder="Note (required)"
            maxLength={500}
            value={draft.note}
            onChange={(event) => onDraftChange(limitKey, { ...draft, note: event.target.value })}
          />
          <ConsoleButton
            loading={saving}
            disabled={draft.value === "" || !draft.note.trim()}
            onClick={() => onSave(limitKey)}
          >
            Set override
          </ConsoleButton>
          {source === "override" ? (
            <ConsoleButton variant="danger" loading={saving} onClick={() => onClear(limitKey)}>
              Clear
            </ConsoleButton>
          ) : null}
        </div>
      </td>
    </tr>
  );
}


export default function CompanyLimitsPanel({ companyId }) {
  const [limits, setLimits] = useState(null);
  const [usage, setUsage] = useState(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState("");

  const [drafts, setDrafts] = useState({});
  const [savingKey, setSavingKey] = useState(null);
  const [actionError, setActionError] = useState("");

  const load = useCallback(async () => {
    setLoading(true);
    setError("");

    try {
      const [limitsResult, usageResult] = await Promise.all([
        companyLimitsRequest(companyId),
        companyUsageRequest(companyId),
      ]);
      setLimits(limitsResult);
      setUsage(usageResult);
    } catch (requestError) {
      setError(requestError.message || "Limits and usage could not be loaded.");
    } finally {
      setLoading(false);
    }
  }, [companyId]);

  useEffect(() => {
    load();
  }, [load]);

  function draftFor(limitKey) {
    return drafts[limitKey] || { value: "", note: "" };
  }

  function setDraft(limitKey, next) {
    setDrafts((current) => ({ ...current, [limitKey]: next }));
  }

  async function saveOverride(limitKey) {
    const draft = draftFor(limitKey);
    setSavingKey(limitKey);
    setActionError("");

    try {
      await setCompanyLimitRequest(companyId, limitKey, Number(draft.value), draft.note.trim());
      setDraft(limitKey, { value: "", note: "" });
      await load();
    } catch (requestError) {
      setActionError(requestError.message || "The override could not be saved.");
    } finally {
      setSavingKey(null);
    }
  }

  async function clearOverride(limitKey) {
    setSavingKey(limitKey);
    setActionError("");

    try {
      await clearCompanyLimitRequest(companyId, limitKey);
      await load();
    } catch (requestError) {
      setActionError(requestError.message || "The override could not be cleared.");
    } finally {
      setSavingKey(null);
    }
  }

  const limitKeys = limits ? Object.keys(limits.limits || {}) : [];

  return (
    <>
      <ConsolePanel
        title="Plan limits"
        description={
          limits?.plan_code
            ? `Allowances from the ${limits.plan_code} plan, with any per-company overrides applied on top.`
            : "This company's allowances."
        }
      >
        <ConsoleBanner tone="error">{error}</ConsoleBanner>
        <ConsoleBanner tone="error">{actionError}</ConsoleBanner>

        {loading ? (
          <p className="sa-note">Loading…</p>
        ) : limitKeys.length ? (
          <div className="sa-table-scroll">
            <table className="sa-table">
              <thead>
                <tr>
                  <th>Allowance</th>
                  <th className="is-numeric">Effective</th>
                  <th>Source</th>
                  <th>Change</th>
                </tr>
              </thead>
              <tbody>
                {limitKeys.map((limitKey) => (
                  <LimitRow
                    key={limitKey}
                    limitKey={limitKey}
                    value={limits.limits[limitKey]}
                    source={limits.sources?.[limitKey]}
                    draft={draftFor(limitKey)}
                    onDraftChange={setDraft}
                    onSave={saveOverride}
                    onClear={clearOverride}
                    saving={savingKey === limitKey}
                  />
                ))}
              </tbody>
            </table>
          </div>
        ) : null}

        <p className="sa-note">
          An override replaces the plan's own number for this company alone,
          until cleared. A value of 0 means no ceiling at all.
        </p>
      </ConsolePanel>

      <ConsolePanel
        title="Usage this period"
        description={usage?.period ? `Metered activity for ${usage.period}.` : "Metered activity."}
      >
        {loading ? (
          <p className="sa-note">Loading…</p>
        ) : usage ? (
          <>
            <div className="sa-metric-grid">
              <div className="sa-metric">
                <span>Assistant replies</span>
                <strong>{formatCount(usage.ai_replies)}</strong>
              </div>
            </div>

            {usage.breakdown?.length ? (
              <div className="sa-table-scroll">
                <table className="sa-table">
                  <thead>
                    <tr>
                      <th>Metric</th>
                      <th>Channel</th>
                      <th className="is-numeric">Quantity</th>
                    </tr>
                  </thead>
                  <tbody>
                    {usage.breakdown.map((row, index) => (
                      <tr key={`${row.metric}-${row.channel}-${row.department_id}-${index}`}>
                        <td>{humanize(row.metric)}</td>
                        <td>{row.channel || "—"}</td>
                        <td className="is-numeric">{formatCount(row.quantity)}</td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              </div>
            ) : (
              <p className="sa-note">No metered activity recorded for this period yet.</p>
            )}
          </>
        ) : null}
      </ConsolePanel>
    </>
  );
}
