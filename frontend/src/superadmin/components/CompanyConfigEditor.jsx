import { useMemo, useState } from "react";

import { updateCompanyConfigRequest } from "../platformClient";
import { humanize } from "../format";
import {
  ConsoleBanner,
  ConsoleButton,
  ConsolePanel,
} from "./ConsoleUI";


// The server rejects a colour it cannot parse, but <input type="color"> refuses
// to display anything other than six digits, so the swatch falls back to black
// while the text field keeps whatever is actually stored.
function toSwatchValue(value) {
  const text = String(value || "").trim();

  if (/^#[0-9a-fA-F]{6}$/.test(text)) {
    return text;
  }

  if (/^#[0-9a-fA-F]{3}$/.test(text)) {
    return `#${text.slice(1).split("").map((digit) => digit + digit).join("")}`;
  }

  if (/^#[0-9a-fA-F]{8}$/.test(text)) {
    return text.slice(0, 7);
  }

  return "#000000";
}

function brandingLabel(field) {
  return humanize(field).replace(/\bColor\b/, "colour");
}


export default function CompanyConfigEditor({ companyId, config, onSaved }) {
  const [modules, setModules] = useState(() => ({ ...(config?.modules || {}) }));
  const [branding, setBranding] = useState(() => ({ ...(config?.branding || {}) }));
  const [layout, setLayout] = useState(() => ({ ...(config?.layout || {}) }));

  const [saving, setSaving] = useState(false);
  const [error, setError] = useState("");
  const [status, setStatus] = useState("");

  const availableModules = useMemo(
    () => config?.available_modules || [],
    [config],
  );
  const availableBranding = useMemo(
    () => config?.available_branding_fields || [],
    [config],
  );
  const availableLayout = useMemo(
    () => config?.available_layout_flags || [],
    [config],
  );

  const colorFields = useMemo(
    () => new Set(availableBranding.filter((field) => field.endsWith("_color"))),
    [availableBranding],
  );

  async function handleSave(event) {
    event.preventDefault();

    setSaving(true);
    setError("");
    setStatus("");

    try {
      const saved = await updateCompanyConfigRequest(companyId, {
        modules,
        branding,
        layout,
      });

      setModules({ ...(saved?.modules || {}) });
      setBranding({ ...(saved?.branding || {}) });
      setLayout({ ...(saved?.layout || {}) });
      setStatus("Configuration saved.");
      onSaved?.(saved);
    } catch (saveError) {
      setError(saveError.message || "Configuration could not be saved.");
    } finally {
      setSaving(false);
    }
  }

  return (
    <form onSubmit={handleSave}>
      <ConsolePanel
        title="Modules"
        description="Which parts of the workspace this company sees. A module absent from the stored configuration is on."
      >
        <div className="sa-switch-grid">
          {availableModules.map((key) => (
            <label key={key} className="sa-switch">
              <input
                type="checkbox"
                checked={modules[key] !== false}
                onChange={(event) =>
                  setModules((current) => ({
                    ...current,
                    [key]: event.target.checked,
                  }))
                }
              />
              <span className="sa-switch-track" aria-hidden="true" />
              <span>{humanize(key)}</span>
            </label>
          ))}
        </div>
      </ConsolePanel>

      <ConsolePanel
        title="Branding"
        description="Brand name and theme tokens applied inside this company's workspace."
      >
        <div className="sa-field-grid">
          {availableBranding.map((field) => (
            <label key={field} className="sa-field" htmlFor={`sa-branding-${field}`}>
              <span>{brandingLabel(field)}</span>

              {colorFields.has(field) ? (
                <span className="sa-color-field">
                  <input
                    type="color"
                    aria-label={`${brandingLabel(field)} swatch`}
                    value={toSwatchValue(branding[field])}
                    onChange={(event) =>
                      setBranding((current) => ({
                        ...current,
                        [field]: event.target.value,
                      }))
                    }
                  />

                  <input
                    id={`sa-branding-${field}`}
                    type="text"
                    value={branding[field] || ""}
                    placeholder="#1B2A4A"
                    spellCheck={false}
                    onChange={(event) =>
                      setBranding((current) => ({
                        ...current,
                        [field]: event.target.value,
                      }))
                    }
                  />
                </span>
              ) : (
                <input
                  id={`sa-branding-${field}`}
                  type="text"
                  value={branding[field] || ""}
                  maxLength={200}
                  onChange={(event) =>
                    setBranding((current) => ({
                      ...current,
                      [field]: event.target.value,
                    }))
                  }
                />
              )}
            </label>
          ))}
        </div>

        <p className="sa-note">
          Clearing a field does not remove it. The API ignores blank values, so
          the previous value stays in place — to change one, type the new value
          over it.
        </p>
      </ConsolePanel>

      <ConsolePanel
        title="Layout flags"
        description="Interface defaults the customer workspace reads when an employee signs in."
      >
        <div className="sa-switch-grid">
          {availableLayout.map((flag) => (
            <label key={flag} className="sa-switch">
              <input
                type="checkbox"
                checked={Boolean(layout[flag])}
                onChange={(event) =>
                  setLayout((current) => ({
                    ...current,
                    [flag]: event.target.checked,
                  }))
                }
              />
              <span className="sa-switch-track" aria-hidden="true" />
              <span>{humanize(flag)}</span>
            </label>
          ))}
        </div>

        <ConsoleBanner tone="error">{error}</ConsoleBanner>
        <ConsoleBanner tone="success">{status}</ConsoleBanner>

        <div className="sa-form-actions">
          <ConsoleButton type="submit" variant="primary" loading={saving}>
            Save configuration
          </ConsoleButton>
        </div>
      </ConsolePanel>
    </form>
  );
}
