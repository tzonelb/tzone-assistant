import { useCallback, useEffect, useMemo, useState } from "react";
import {
  AddOutlined,
  CloseOutlined,
  RefreshOutlined,
} from "@mui/icons-material";

import {
  createChannelAccountRequest,
  deleteChannelAccountRequest,
  getChannelAccountsRequest,
  updateChannelAccountRequest,
} from "../../api/channels";
import {
  AppButton,
  AppCard,
  AppTable,
  ConfirmDialog,
  ErrorState,
  PageHeader,
  StatusBadge,
} from "../../components/common";
import { formatPlatformDateTime } from "../../utils/dateTime";
import "./ChannelsPage.css";

// Only used as a fallback label; the server is the authority on which
// identifier a channel is routed by and sends it in `routing_fields`.
const CHANNEL_LABELS = {
  messenger: "Facebook Messenger",
  instagram: "Instagram",
  whatsapp: "WhatsApp",
};

const FIELD_HINTS = {
  page_id: "The numeric id of the Facebook Page this company answers from.",
  instagram_business_id:
    "The Instagram professional account id connected to the Page.",
  phone_number_id:
    "The WhatsApp Business phone number id from the Meta app dashboard.",
};

const FEATURE_FLAGS = [
  ["ai_enabled", "Assistant replies"],
  ["flow_enabled", "Automated flows"],
  ["voice_ai_enabled", "Voice messages"],
  ["image_ai_enabled", "Image understanding"],
];

function humanize(value) {
  return String(value ?? "")
    .replaceAll("_", " ")
    .replace(/\b\w/g, (letter) => letter.toUpperCase());
}

function channelLabel(channel) {
  return CHANNEL_LABELS[channel] || humanize(channel);
}

function fieldLabel(field) {
  return humanize(field).replace(/\bId\b/, "ID");
}

function emptyForm(channel = "messenger") {
  return {
    channel,
    name: "",
    branch_id: "",
    department_id: "",
    status: "active",
    page_id: "",
    instagram_business_id: "",
    phone_number_id: "",
    access_token: "",
    verify_token: "",
    ai_enabled: true,
    flow_enabled: true,
    voice_ai_enabled: false,
    image_ai_enabled: false,
  };
}

function formFromAccount(account) {
  return {
    channel: account.channel || "messenger",
    name: account.name || "",
    branch_id:
      account.branch_id === null || account.branch_id === undefined
        ? ""
        : String(account.branch_id),
    department_id:
      account.department_id === null || account.department_id === undefined
        ? ""
        : String(account.department_id),
    status: account.status || "active",
    page_id: account.page_id || "",
    instagram_business_id: account.instagram_business_id || "",
    phone_number_id: account.phone_number_id || "",
    // Never prefilled: the server does not return a token, and a placeholder
    // that looked like one would invite the team to save it back.
    access_token: "",
    verify_token: "",
    ai_enabled: Boolean(account.ai_enabled),
    flow_enabled: Boolean(account.flow_enabled),
    voice_ai_enabled: Boolean(account.voice_ai_enabled),
    image_ai_enabled: Boolean(account.image_ai_enabled),
  };
}

export default function ChannelsPage() {
  const [items, setItems] = useState([]);
  const [supportedChannels, setSupportedChannels] = useState([]);
  const [routingFields, setRoutingFields] = useState({});
  const [departments, setDepartments] = useState([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState("");

  const [editorOpen, setEditorOpen] = useState(false);
  const [selected, setSelected] = useState(null);
  const [form, setForm] = useState(emptyForm);
  const [clearAccessToken, setClearAccessToken] = useState(false);
  const [clearVerifyToken, setClearVerifyToken] = useState(false);
  const [saving, setSaving] = useState(false);
  const [formError, setFormError] = useState("");
  const [formConflict, setFormConflict] = useState("");
  const [saveStatus, setSaveStatus] = useState("");

  const [pendingDelete, setPendingDelete] = useState(null);
  const [deleting, setDeleting] = useState(false);

  const loadAccounts = useCallback(async () => {
    setLoading(true);
    setError("");

    try {
      const result = await getChannelAccountsRequest();

      setItems(Array.isArray(result?.items) ? result.items : []);
      setSupportedChannels(
        Array.isArray(result?.supported_channels)
          ? result.supported_channels
          : [],
      );
      setRoutingFields(result?.routing_fields || {});
      setDepartments(Array.isArray(result?.departments) ? result.departments : []);
    } catch (requestError) {
      setError(
        requestError.message || "Connected accounts could not be loaded.",
      );
      setItems([]);
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => {
    loadAccounts();
  }, [loadAccounts]);

  const routingField = routingFields[form.channel] || "";

  function resetFormState() {
    setClearAccessToken(false);
    setClearVerifyToken(false);
    setFormError("");
    setFormConflict("");
    setSaveStatus("");
  }

  function openCreate() {
    const firstChannel = supportedChannels[0] || "messenger";
    setSelected(null);
    setForm(emptyForm(firstChannel));
    resetFormState();
    setEditorOpen(true);
  }

  function openEdit(account) {
    setSelected(account);
    setForm(formFromAccount(account));
    resetFormState();
    setEditorOpen(true);
  }

  function closeEditor() {
    setEditorOpen(false);
    setSelected(null);
    resetFormState();
  }

  function updateField(key, value) {
    setSaveStatus("");
    setForm((current) => ({ ...current, [key]: value }));
  }

  async function handleSubmit(event) {
    event.preventDefault();

    setSaving(true);
    setFormError("");
    setFormConflict("");
    setSaveStatus("");

    try {
      if (selected) {
        const values = {
          name: form.name.trim(),
          branch_id: form.branch_id ? Number(form.branch_id) : null,
          department_id: form.department_id ? Number(form.department_id) : null,
          status: form.status,
          ai_enabled: form.ai_enabled,
          flow_enabled: form.flow_enabled,
          voice_ai_enabled: form.voice_ai_enabled,
          image_ai_enabled: form.image_ai_enabled,
        };

        if (routingField) {
          values[routingField] = form[routingField].trim();
        }

        /*
         * A blank token field means "keep what is stored". The key is left out
         * entirely so the server's `exclude_unset` never sees it. Clearing is a
         * deliberate, separate action that sends an empty string.
         */
        if (clearAccessToken) {
          values.access_token = "";
        } else if (form.access_token.trim()) {
          values.access_token = form.access_token.trim();
        }

        if (clearVerifyToken) {
          values.verify_token = "";
        } else if (form.verify_token.trim()) {
          values.verify_token = form.verify_token.trim();
        }

        await updateChannelAccountRequest(selected.id, values);
        setSaveStatus("Account updated.");
      } else {
        const values = {
          channel: form.channel,
          name: form.name.trim(),
          branch_id: form.branch_id ? Number(form.branch_id) : null,
          department_id: form.department_id ? Number(form.department_id) : null,
          ai_enabled: form.ai_enabled,
          flow_enabled: form.flow_enabled,
          voice_ai_enabled: form.voice_ai_enabled,
          image_ai_enabled: form.image_ai_enabled,
        };

        if (routingField) {
          values[routingField] = form[routingField].trim();
        }

        if (form.access_token.trim()) {
          values.access_token = form.access_token.trim();
        }

        if (form.verify_token.trim()) {
          values.verify_token = form.verify_token.trim();
        }

        await createChannelAccountRequest(values);
        setSaveStatus("Account connected.");
      }

      await loadAccounts();
      setClearAccessToken(false);
      setClearVerifyToken(false);
      setForm((current) => ({
        ...current,
        access_token: "",
        verify_token: "",
      }));

      if (!selected) {
        closeEditor();
      }
    } catch (requestError) {
      const message =
        requestError.message || "The account could not be saved.";

      // 409 is the one failure the team cannot fix from this form: the page or
      // number is claimed by another company on this platform.
      if (requestError.status === 409) {
        setFormConflict(message);
      } else {
        setFormError(message);
      }
    } finally {
      setSaving(false);
    }
  }

  async function handleDelete() {
    if (!pendingDelete) return;

    setDeleting(true);

    try {
      await deleteChannelAccountRequest(pendingDelete.id);

      if (selected?.id === pendingDelete.id) {
        closeEditor();
      }

      setPendingDelete(null);
      await loadAccounts();
    } catch (requestError) {
      setError(
        requestError.message || "The account could not be disconnected.",
      );
      setPendingDelete(null);
    } finally {
      setDeleting(false);
    }
  }

  const columns = useMemo(
    () => [
      {
        key: "name",
        label: "Account",
        render: (value, row) => (
          <button
            type="button"
            className="channel-name-button"
            onClick={() => openEdit(row)}
          >
            <strong>{value || `Account #${row.id}`}</strong>
            <span>{channelLabel(row.channel)}</span>
          </button>
        ),
      },
      {
        key: "routing",
        label: "Routing identifier",
        valueGetter: (row) =>
          row[routingFields[row.channel]] || row.external_account_id || "",
        render: (value, row) => (
          <div className="channel-routing-cell">
            <small>{fieldLabel(routingFields[row.channel] || "id")}</small>
            <code>{value || "—"}</code>
          </div>
        ),
      },
      {
        key: "tokens",
        label: "Credentials",
        render: (value, row) => (
          <div className="channel-token-cell">
            <span>
              Access token:{" "}
              <StatusBadge
                status={row.has_access_token ? "connected" : "inactive"}
                label={row.has_access_token ? "Configured" : "Not set"}
              />
            </span>
            <span>
              Verify token:{" "}
              <StatusBadge
                status={row.has_verify_token ? "connected" : "inactive"}
                label={row.has_verify_token ? "Configured" : "Not set"}
              />
            </span>
          </div>
        ),
      },
      {
        key: "status",
        label: "Status",
        render: (value) => (
          <StatusBadge
            status={value === "active" ? "active" : "inactive"}
            label={humanize(value)}
          />
        ),
      },
      {
        key: "branch_name",
        label: "Branch",
        render: (value) => value || "—",
      },
      {
        key: "updated_at",
        label: "Updated",
        render: (value) => formatPlatformDateTime(value),
      },
      {
        key: "actions",
        label: "",
        align: "right",
        render: (value, row) => (
          <div className="channel-row-actions">
            <AppButton variant="ghost" size="small" onClick={() => openEdit(row)}>
              Edit
            </AppButton>

            <AppButton
              variant="danger"
              size="small"
              onClick={() => setPendingDelete(row)}
            >
              Disconnect
            </AppButton>
          </div>
        ),
      },
    ],
    [routingFields],
  );

  const channelOptions = supportedChannels.length
    ? supportedChannels
    : Object.keys(routingFields);

  return (
    <div className="channels-page">
      <PageHeader
        eyebrow="CONNECTED ACCOUNTS"
        title="Channels"
        description="Connect a Facebook Page, an Instagram account or a WhatsApp number so this company receives and answers its own messages."
        actions={
          <>
            <AppButton
              variant="secondary"
              icon={<RefreshOutlined fontSize="small" />}
              onClick={loadAccounts}
            >
              Refresh
            </AppButton>

            <AppButton
              variant="primary"
              icon={<AddOutlined fontSize="small" />}
              onClick={openCreate}
            >
              Connect account
            </AppButton>
          </>
        }
      />

      <div className={`channels-layout ${editorOpen ? "has-editor" : ""}`}>
        <AppCard padding="medium" className="channels-list-card">
          {error ? (
            <ErrorState
              title="Connected accounts could not load"
              description={error}
              action={
                <AppButton variant="primary" onClick={loadAccounts}>
                  Try again
                </AppButton>
              }
            />
          ) : (
            <AppTable
              columns={columns}
              rows={items}
              loading={loading}
              emptyTitle="No account is connected yet"
              emptyDescription="Connect a Facebook Page, Instagram account or WhatsApp number so inbound messages are routed to this company."
              page={1}
              pageSize={Math.max(items.length, 1)}
              totalRows={items.length}
              renderMobileCard={(row) => (
                <button
                  type="button"
                  className="channel-mobile-card"
                  onClick={() => openEdit(row)}
                >
                  <strong>{row.name || `Account #${row.id}`}</strong>
                  <span>{channelLabel(row.channel)}</span>
                  <code>
                    {row[routingFields[row.channel]] ||
                      row.external_account_id ||
                      "No routing identifier"}
                  </code>
                  <small>
                    Access token:{" "}
                    {row.has_access_token ? "Configured" : "Not set"} ·{" "}
                    {humanize(row.status)}
                  </small>
                </button>
              )}
            />
          )}
        </AppCard>

        {editorOpen ? (
          <AppCard padding="medium" className="channels-editor-card">
            <header className="channels-editor-head">
              <div>
                <span>
                  {selected ? "EDIT CONNECTION" : "CONNECT A NEW ACCOUNT"}
                </span>
                <h3>
                  {selected
                    ? selected.name || `Account #${selected.id}`
                    : "New channel account"}
                </h3>
              </div>

              <button
                type="button"
                className="channels-editor-close"
                aria-label="Close editor"
                onClick={closeEditor}
              >
                <CloseOutlined fontSize="small" />
              </button>
            </header>

            {formConflict ? (
              <div className="channels-conflict" role="alert">
                <strong>This account is already connected elsewhere</strong>
                <p>{formConflict}</p>
              </div>
            ) : null}

            <form className="channels-form" onSubmit={handleSubmit}>
              <label htmlFor="channel-channel">
                <span>Channel</span>

                <select
                  id="channel-channel"
                  value={form.channel}
                  // The routing identifier is the primary key of the connection;
                  // switching an existing account to another channel would
                  // silently re-route live traffic.
                  disabled={Boolean(selected)}
                  onChange={(event) => updateField("channel", event.target.value)}
                >
                  {channelOptions.map((channel) => (
                    <option value={channel} key={channel}>
                      {channelLabel(channel)}
                    </option>
                  ))}
                </select>

                {selected ? (
                  <small>
                    The channel cannot be changed after the account is
                    connected.
                  </small>
                ) : null}
              </label>

              <label htmlFor="channel-name">
                <span>Display name</span>

                <input
                  id="channel-name"
                  type="text"
                  required
                  maxLength={120}
                  value={form.name}
                  placeholder="T-ZONE Main Page"
                  onChange={(event) => updateField("name", event.target.value)}
                />
              </label>

              {routingField ? (
                <label htmlFor="channel-routing">
                  <span>{fieldLabel(routingField)}</span>

                  <input
                    id="channel-routing"
                    type="text"
                    required
                    maxLength={120}
                    value={form[routingField]}
                    placeholder={fieldLabel(routingField)}
                    onChange={(event) =>
                      updateField(routingField, event.target.value)
                    }
                  />

                  <small>
                    {FIELD_HINTS[routingField] ||
                      "Inbound messages are routed to this company by this identifier."}
                  </small>
                </label>
              ) : null}

              {/* Which section of the business this account feeds. Optional on
                  purpose: a company may connect three accounts of the same type
                  and point each at a different department, or point none of
                  them anywhere and let the customer choose from the menu. */}
              <label htmlFor="channel-department">
                <span>Department (optional)</span>

                <select
                  id="channel-department"
                  value={form.department_id}
                  onChange={(event) =>
                    updateField("department_id", event.target.value)
                  }
                >
                  <option value="">
                    No default — the customer chooses
                  </option>
                  {departments.map((item) => (
                    <option value={String(item.id)} key={item.id}>
                      {item.label}
                    </option>
                  ))}
                </select>

                <small>
                  A message arriving on this account starts in this department.
                  The customer choosing another one from the menu still wins.
                </small>
              </label>

              <label htmlFor="channel-branch">
                <span>Branch id (optional)</span>

                <input
                  id="channel-branch"
                  type="number"
                  min="1"
                  value={form.branch_id}
                  placeholder="Leave empty for the whole company"
                  onChange={(event) =>
                    updateField("branch_id", event.target.value)
                  }
                />
              </label>

              {selected ? (
                <label htmlFor="channel-status">
                  <span>Status</span>

                  <select
                    id="channel-status"
                    value={form.status}
                    onChange={(event) =>
                      updateField("status", event.target.value)
                    }
                  >
                    <option value="active">Active</option>
                    <option value="disabled">Disabled</option>
                  </select>

                  <small>
                    A disabled account keeps its settings but is not used to
                    send.
                  </small>
                </label>
              ) : null}

              <fieldset className="channels-secrets">
                <legend>Credentials</legend>

                <p className="channels-secrets-note">
                  Tokens are stored sealed and are never sent back to this
                  screen. Leave a field blank to keep the token that is already
                  stored.
                </p>

                <div className="channels-field">
                  <label htmlFor="channel-access-token">
                    <span>Access token</span>

                    <StatusBadge
                      status={
                        selected?.has_access_token ? "connected" : "inactive"
                      }
                      label={
                        selected?.has_access_token ? "Configured" : "Not set"
                      }
                    />
                  </label>

                  <input
                    id="channel-access-token"
                    type="password"
                    autoComplete="new-password"
                    maxLength={1000}
                    value={form.access_token}
                    disabled={clearAccessToken}
                    placeholder={
                      selected?.has_access_token
                        ? "Leave blank to keep the stored token"
                        : "Paste the page access token"
                    }
                    onChange={(event) =>
                      updateField("access_token", event.target.value)
                    }
                  />

                  {selected?.has_access_token ? (
                    <label className="channels-clear-toggle">
                      <input
                        type="checkbox"
                        checked={clearAccessToken}
                        onChange={(event) => {
                          setSaveStatus("");
                          setClearAccessToken(event.target.checked);
                        }}
                      />
                      <span>Remove the stored access token when saving</span>
                    </label>
                  ) : null}
                </div>

                <div className="channels-field">
                  <label htmlFor="channel-verify-token">
                    <span>Verify token</span>

                    <StatusBadge
                      status={
                        selected?.has_verify_token ? "connected" : "inactive"
                      }
                      label={
                        selected?.has_verify_token ? "Configured" : "Not set"
                      }
                    />
                  </label>

                  <input
                    id="channel-verify-token"
                    type="password"
                    autoComplete="new-password"
                    maxLength={500}
                    value={form.verify_token}
                    disabled={clearVerifyToken}
                    placeholder={
                      selected?.has_verify_token
                        ? "Leave blank to keep the stored token"
                        : "The webhook verify token"
                    }
                    onChange={(event) =>
                      updateField("verify_token", event.target.value)
                    }
                  />

                  {selected?.has_verify_token ? (
                    <label className="channels-clear-toggle">
                      <input
                        type="checkbox"
                        checked={clearVerifyToken}
                        onChange={(event) => {
                          setSaveStatus("");
                          setClearVerifyToken(event.target.checked);
                        }}
                      />
                      <span>Remove the stored verify token when saving</span>
                    </label>
                  ) : null}
                </div>
              </fieldset>

              <fieldset className="channels-flags">
                <legend>What runs on this account</legend>

                {FEATURE_FLAGS.map(([key, label]) => (
                  <label className="channels-flag" key={key}>
                    <input
                      type="checkbox"
                      checked={form[key]}
                      onChange={(event) => updateField(key, event.target.checked)}
                    />
                    <span>{label}</span>
                  </label>
                ))}
              </fieldset>

              <footer className="channels-form-footer">
                <span className={formError ? "is-error" : "is-success"}>
                  {formError || saveStatus}
                </span>

                <div>
                  <AppButton
                    variant="secondary"
                    disabled={saving}
                    onClick={closeEditor}
                  >
                    Cancel
                  </AppButton>

                  <AppButton type="submit" variant="primary" loading={saving}>
                    {selected ? "Save changes" : "Connect account"}
                  </AppButton>
                </div>
              </footer>
            </form>
          </AppCard>
        ) : null}
      </div>

      <ConfirmDialog
        open={Boolean(pendingDelete)}
        title="Disconnect this account?"
        message={
          pendingDelete
            ? `Messages arriving on ${pendingDelete.name || channelLabel(pendingDelete.channel)} will no longer be routed to this company, and replies can no longer be sent from it.`
            : ""
        }
        confirmLabel="Disconnect"
        loading={deleting}
        onConfirm={handleDelete}
        onCancel={() => setPendingDelete(null)}
      />
    </div>
  );
}
