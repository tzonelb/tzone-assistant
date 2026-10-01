import { useCallback, useEffect, useState } from "react";
import { RefreshOutlined } from "@mui/icons-material";

import {
  clearPlatformChannelCredentialsRequest,
  listPlatformChannelAccessRequest,
  listPlatformChannelsRequest,
  setPlatformChannelAccessRequest,
  setPlatformChannelCredentialsRequest,
} from "../platformClient";
import { formatTimestamp } from "../format";
import {
  ConsoleBanner,
  ConsoleButton,
  ConsoleEmpty,
  ConsoleLoading,
  ConsolePage,
  ConsolePanel,
  StatusChip,
} from "../components/ConsoleUI";


// Kept in step with backend/services/platform_channel_service.py's own
// PLATFORM_KEYED_CHANNELS -- these are the only two channels with a
// platform-level credential at all, because they are the only two backed
// by one Meta developer app rather than one company's own session. See
// that module's own docstring on why "instagram" has no row of its own.
const CHANNELS = [
  {
    key: "messenger",
    label: "Messenger / Instagram",
    description:
      "One Facebook app powers \"Log in with Facebook\" for both -- a company that connects approves once and gets its Page and any linked Instagram account together.",
  },
  {
    key: "whatsapp",
    label: "WhatsApp",
    description:
      "The WhatsApp Business app's own id and secret. Each company still connects its own phone number and access token on its own Channels screen -- this only reserves the app identity.",
  },
];

const CHANNEL_LABELS = Object.fromEntries(CHANNELS.map((c) => [c.key, c.label]));


export default function ChannelsPage() {
  const [items, setItems] = useState([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState("");

  const [editingChannel, setEditingChannel] = useState(null);
  const [form, setForm] = useState({ app_id: "", app_secret: "" });
  const [saving, setSaving] = useState(false);
  const [formError, setFormError] = useState("");

  const [clearingChannel, setClearingChannel] = useState(null);

  const [accessChannel, setAccessChannel] = useState("messenger");
  const [accessRows, setAccessRows] = useState([]);
  const [accessLoading, setAccessLoading] = useState(false);
  const [accessError, setAccessError] = useState("");
  const [togglingCompanyId, setTogglingCompanyId] = useState(null);

  const loadChannels = useCallback(async () => {
    setLoading(true);
    setError("");

    try {
      const result = await listPlatformChannelsRequest();
      setItems(Array.isArray(result?.items) ? result.items : []);
    } catch (requestError) {
      setError(requestError.message || "The channel list could not be loaded.");
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => {
    loadChannels();
  }, [loadChannels]);

  const loadAccess = useCallback(async (channel) => {
    setAccessLoading(true);
    setAccessError("");

    try {
      const result = await listPlatformChannelAccessRequest(channel);
      setAccessRows(Array.isArray(result?.items) ? result.items : []);
    } catch (requestError) {
      setAccessError(requestError.message || "The access list could not be loaded.");
      setAccessRows([]);
    } finally {
      setAccessLoading(false);
    }
  }, []);

  useEffect(() => {
    loadAccess(accessChannel);
  }, [accessChannel, loadAccess]);

  function openEdit(channel) {
    setEditingChannel(channel);
    setForm({ app_id: "", app_secret: "" });
    setFormError("");
  }

  async function saveCredentials(event) {
    event.preventDefault();
    setSaving(true);
    setFormError("");

    try {
      await setPlatformChannelCredentialsRequest(editingChannel, {
        app_id: form.app_id.trim(),
        app_secret: form.app_secret.trim(),
      });
      setEditingChannel(null);
      await loadChannels();
    } catch (requestError) {
      setFormError(requestError.message || "That credential could not be saved.");
    } finally {
      setSaving(false);
    }
  }

  async function clearCredentials(channel) {
    if (
      !window.confirm(
        `Remove the ${CHANNEL_LABELS[channel]} credential? Every company relying on it loses "Log in with Facebook" immediately, even if their own Page stays connected.`,
      )
    ) {
      return;
    }

    setClearingChannel(channel);

    try {
      await clearPlatformChannelCredentialsRequest(channel);
      await loadChannels();
    } catch (requestError) {
      setError(requestError.message || "That credential could not be removed.");
    } finally {
      setClearingChannel(null);
    }
  }

  async function toggleAccess(companyId, enabled) {
    setTogglingCompanyId(companyId);
    setAccessError("");

    try {
      await setPlatformChannelAccessRequest(accessChannel, companyId, enabled);
      setAccessRows((rows) =>
        rows.map((row) => (row.company_id === companyId ? { ...row, enabled } : row)),
      );
    } catch (requestError) {
      setAccessError(requestError.message || "That change could not be saved.");
    } finally {
      setTogglingCompanyId(null);
    }
  }

  const statusByChannel = Object.fromEntries(items.map((item) => [item.channel, item]));

  return (
    <ConsolePage
      eyebrow="CHANNELS"
      title="Channels"
      description="A Meta developer app's own keys, held once for the whole platform. Configuring one here turns on the self-service connect flow for it; granting a company access below is the separate decision that lets that one company actually use it."
      actions={
        <ConsoleButton onClick={loadChannels} disabled={loading}>
          <RefreshOutlined fontSize="small" />
          Refresh
        </ConsoleButton>
      }
    >
      <ConsoleBanner>{error}</ConsoleBanner>

      {loading ? (
        <ConsoleLoading label="Loading channels..." />
      ) : (
        <ConsolePanel
          title="Developer app credentials"
          description="Never shown again once saved -- replace the whole credential to change it."
        >
          <div className="sa-channels-grid">
            {CHANNELS.map((channel) => {
              const status = statusByChannel[channel.key] || { configured: false, config: {} };

              return (
                <div className="sa-channel-card" key={channel.key}>
                  <header>
                    <strong>{channel.label}</strong>
                    <StatusChip status={status.configured ? "active" : "unconfigured"} />
                  </header>

                  <p>{channel.description}</p>

                  {status.configured ? (
                    <dl className="sa-channel-card-meta">
                      <div>
                        <dt>App ID</dt>
                        <dd>
                          <code>{status.config?.app_id || "—"}</code>
                        </dd>
                      </div>
                      <div>
                        <dt>Updated</dt>
                        <dd>{formatTimestamp(status.updated_at)}</dd>
                      </div>
                    </dl>
                  ) : (
                    <p className="sa-channel-card-empty">Not configured yet.</p>
                  )}

                  <footer>
                    <ConsoleButton onClick={() => openEdit(channel.key)}>
                      {status.configured ? "Replace" : "Configure"}
                    </ConsoleButton>

                    {status.configured ? (
                      <ConsoleButton
                        variant="danger"
                        loading={clearingChannel === channel.key}
                        onClick={() => clearCredentials(channel.key)}
                      >
                        Remove
                      </ConsoleButton>
                    ) : null}
                  </footer>
                </div>
              );
            })}
          </div>
        </ConsolePanel>
      )}

      {editingChannel ? (
        <ConsolePanel
          title={`Configure ${CHANNEL_LABELS[editingChannel]}`}
          description="Both fields are required -- a partial credential is refused, so a company can never end up with an access token paired against the wrong app secret."
        >
          <form className="sa-form" onSubmit={saveCredentials}>
            <div className="sa-field-grid">
              <label className="sa-field" htmlFor="sa-channel-app-id">
                <span>App ID</span>
                <input
                  id="sa-channel-app-id"
                  type="text"
                  value={form.app_id}
                  maxLength={120}
                  required
                  onChange={(event) => setForm((f) => ({ ...f, app_id: event.target.value }))}
                />
              </label>

              <label className="sa-field" htmlFor="sa-channel-app-secret">
                <span>App secret</span>
                <input
                  id="sa-channel-app-secret"
                  type="password"
                  autoComplete="new-password"
                  value={form.app_secret}
                  maxLength={500}
                  required
                  onChange={(event) => setForm((f) => ({ ...f, app_secret: event.target.value }))}
                />
              </label>
            </div>

            <ConsoleBanner>{formError}</ConsoleBanner>

            <div className="sa-form-actions">
              <ConsoleButton onClick={() => setEditingChannel(null)} disabled={saving}>
                Cancel
              </ConsoleButton>
              <ConsoleButton type="submit" variant="primary" loading={saving}>
                Save
              </ConsoleButton>
            </div>
          </form>
        </ConsolePanel>
      ) : null}

      <ConsolePanel
        title="Company access"
        description="Who may reach the channel above. A company not listed as granted sees no connect button for it at all, even once the app is configured."
        actions={
          <select
            value={accessChannel}
            onChange={(event) => setAccessChannel(event.target.value)}
            className="sa-select"
          >
            {CHANNELS.map((channel) => (
              <option value={channel.key} key={channel.key}>
                {channel.label}
              </option>
            ))}
          </select>
        }
      >
        <ConsoleBanner>{accessError}</ConsoleBanner>

        {accessLoading ? (
          <ConsoleLoading label="Loading companies..." />
        ) : accessRows.length === 0 ? (
          <ConsoleEmpty title="No companies yet" description="Create a company first." />
        ) : (
          <div className="sa-table-scroll">
            <table className="sa-table">
              <thead>
                <tr>
                  <th>Company</th>
                  <th>Status</th>
                  <th>Access</th>
                </tr>
              </thead>
              <tbody>
                {accessRows.map((row) => (
                  <tr key={row.company_id}>
                    <td>{row.company_name}</td>
                    <td>
                      <StatusChip status={row.company_status} />
                    </td>
                    <td>
                      <label className="sa-toggle">
                        <input
                          type="checkbox"
                          checked={row.enabled}
                          disabled={togglingCompanyId === row.company_id}
                          onChange={(event) =>
                            toggleAccess(row.company_id, event.target.checked)
                          }
                        />
                        <span>{row.enabled ? "Granted" : "Not granted"}</span>
                      </label>
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        )}
      </ConsolePanel>
    </ConsolePage>
  );
}
