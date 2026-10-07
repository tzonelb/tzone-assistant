import { useCallback, useEffect, useState } from "react";

import {
  clearSettingOverrideRequest,
  listSettingOverridesRequest,
  setSettingOverrideRequest,
} from "../platformClient";
import { formatTimestamp, humanize } from "../format";
import { ConsoleBanner, ConsoleButton, ConsolePanel } from "./ConsoleUI";


// Autocomplete hints only -- the server is the one source of truth for which
// sections and keys actually exist (`database.schema_tenant.DEFAULT_SETTINGS`),
// and refuses an override naming anything else with a message that lists the
// real ones. A stale hint here can only suggest a value that still fails.
const KNOWN_SECTIONS = [
  "company_profile",
  "ai_behavior",
  "working_hours",
  "notifications",
  "reply_flow",
  "reply_policy",
];

const EMPTY_FORM = { section: "", settingKey: "", value: "", lock: "unchanged", note: "" };

function parseValue(text) {
  const trimmed = text.trim();
  if (!trimmed) return null;
  try {
    return JSON.parse(trimmed);
  } catch {
    return trimmed;
  }
}


export default function CompanySettingOverridesPanel({ companyId }) {
  const [overrides, setOverrides] = useState([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState("");

  const [form, setForm] = useState(EMPTY_FORM);
  const [saving, setSaving] = useState(false);
  const [formError, setFormError] = useState("");
  const [formStatus, setFormStatus] = useState("");

  const [clearingKey, setClearingKey] = useState(null);

  const load = useCallback(async () => {
    setLoading(true);
    setError("");

    try {
      const result = await listSettingOverridesRequest(companyId);
      setOverrides(Array.isArray(result?.items) ? result.items : []);
    } catch (requestError) {
      setError(requestError.message || "Setting overrides could not be loaded.");
    } finally {
      setLoading(false);
    }
  }, [companyId]);

  useEffect(() => {
    load();
  }, [load]);

  async function submitOverride(event) {
    event.preventDefault();
    if (!form.section.trim() || !form.settingKey.trim()) return;

    setSaving(true);
    setFormError("");
    setFormStatus("");

    try {
      await setSettingOverrideRequest(companyId, {
        section: form.section.trim(),
        setting_key: form.settingKey.trim(),
        value: form.value.trim() ? parseValue(form.value) : undefined,
        set_value: Boolean(form.value.trim()),
        is_locked: form.lock === "unchanged" ? null : form.lock === "locked",
        note: form.note.trim() || undefined,
      });
      setFormStatus(`Pinned ${form.section.trim()}.${form.settingKey.trim()}.`);
      setForm(EMPTY_FORM);
      await load();
    } catch (requestError) {
      setFormError(requestError.message || "The override could not be saved.");
    } finally {
      setSaving(false);
    }
  }

  async function clearOverride(section, settingKey) {
    const key = `${section}.${settingKey}`;
    setClearingKey(key);
    setFormError("");

    try {
      await clearSettingOverrideRequest(companyId, section, settingKey);
      await load();
    } catch (requestError) {
      setFormError(requestError.message || "The override could not be cleared.");
    } finally {
      setClearingKey(null);
    }
  }

  return (
    <ConsolePanel
      title="Setting overrides"
      description="Pin a company's own setting to a value, lock it so the company cannot change it, or both -- independently."
    >
      <ConsoleBanner tone="error">{error}</ConsoleBanner>

      {loading ? (
        <p className="sa-note">Loading…</p>
      ) : overrides.length ? (
        <div className="sa-table-scroll">
          <table className="sa-table">
            <thead>
              <tr>
                <th>Section</th>
                <th>Setting</th>
                <th>Pinned value</th>
                <th>Locked</th>
                <th>Last changed</th>
                <th />
              </tr>
            </thead>
            <tbody>
              {overrides.map((override) => {
                const key = `${override.section}.${override.setting_key}`;
                return (
                  <tr key={key}>
                    <td>{humanize(override.section)}</td>
                    <td>{override.setting_key}</td>
                    <td>
                      <code>{JSON.stringify(override.value)}</code>
                    </td>
                    <td>
                      <span className={`sa-chip ${override.is_locked ? "is-danger" : "is-muted"}`}>
                        {override.is_locked ? "Locked" : "Not locked"}
                      </span>
                    </td>
                    <td>{formatTimestamp(override.updated_at)}</td>
                    <td>
                      <ConsoleButton
                        variant="danger"
                        loading={clearingKey === key}
                        onClick={() => clearOverride(override.section, override.setting_key)}
                      >
                        Clear
                      </ConsoleButton>
                    </td>
                  </tr>
                );
              })}
            </tbody>
          </table>
        </div>
      ) : (
        <p className="sa-note">No overrides are set on this company.</p>
      )}

      <form className="sa-inline-form" onSubmit={submitOverride}>
        <label className="sa-field" htmlFor="sa-override-section">
          <span>Section</span>
          <input
            id="sa-override-section"
            list="sa-override-sections"
            value={form.section}
            maxLength={60}
            onChange={(event) => setForm((current) => ({ ...current, section: event.target.value }))}
            required
          />
          <datalist id="sa-override-sections">
            {KNOWN_SECTIONS.map((section) => <option value={section} key={section} />)}
          </datalist>
        </label>

        <label className="sa-field" htmlFor="sa-override-key">
          <span>Setting key</span>
          <input
            id="sa-override-key"
            value={form.settingKey}
            maxLength={80}
            onChange={(event) => setForm((current) => ({ ...current, settingKey: event.target.value }))}
            required
          />
        </label>

        <label className="sa-field" htmlFor="sa-override-value">
          <span>Pin to value (optional, JSON or plain text)</span>
          <input
            id="sa-override-value"
            value={form.value}
            maxLength={2000}
            placeholder='true, 30, "ai_first"...'
            onChange={(event) => setForm((current) => ({ ...current, value: event.target.value }))}
          />
        </label>

        <label className="sa-field" htmlFor="sa-override-lock">
          <span>Lock</span>
          <select
            id="sa-override-lock"
            value={form.lock}
            onChange={(event) => setForm((current) => ({ ...current, lock: event.target.value }))}
          >
            <option value="unchanged">Leave as is</option>
            <option value="locked">Locked</option>
            <option value="unlocked">Unlocked</option>
          </select>
        </label>

        <label className="sa-field" htmlFor="sa-override-note">
          <span>Note (recorded in the audit log)</span>
          <input
            id="sa-override-note"
            value={form.note}
            maxLength={500}
            onChange={(event) => setForm((current) => ({ ...current, note: event.target.value }))}
          />
        </label>

        <ConsoleButton
          type="submit"
          variant="primary"
          loading={saving}
          disabled={!form.section.trim() || !form.settingKey.trim()}
        >
          Save override
        </ConsoleButton>
      </form>

      <ConsoleBanner tone="error">{formError}</ConsoleBanner>
      <ConsoleBanner tone="success">{formStatus}</ConsoleBanner>
    </ConsolePanel>
  );
}
