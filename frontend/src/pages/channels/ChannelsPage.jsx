import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import {
  AddOutlined,
  CloseOutlined,
  RefreshOutlined,
} from "@mui/icons-material";

import {
  confirmChannelVerificationRequest,
  createChannelAccountRequest,
  deleteChannelAccountRequest,
  getChannelAccountsRequest,
  requestChannelVerificationRequest,
  updateChannelAccountRequest,
} from "../../api/channels";
import {
  facebookOAuthConfigRequest,
  startFacebookOAuthRequest,
} from "../../api/client";
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
import { CHANNEL_CATEGORIES } from "./channelCatalog";
import { resolveChannelIcon } from "./channelIcons";
import "./ChannelsPage.css";

// The message shown after returning from the Facebook connect flow, read once
// from the ?connect= status the callback redirects with. Kept out of the
// component so it can seed initial state without a set-state-in-effect.
const CONNECT_MESSAGES = {
  ok: "Connected. Your Facebook Page and any linked Instagram account are now receiving messages.",
  none: "Signed in, but no Page could be connected. Make sure you manage a Facebook Page.",
  cancelled: "Facebook sign-in was cancelled.",
  invalid: "That sign-in link expired. Please try connecting again.",
  failed: "Facebook sign-in failed. Please try again.",
};

// The elevated grant from confirming an emailed code, kept per-tab rather
// than persisted anywhere durable: it is a short-lived pass to connect or
// disconnect a channel, not a credential worth surviving a closed tab.
const ELEVATION_KEY = "tzone_channel_elevation";

function readStoredElevation() {
  try {
    const raw = sessionStorage.getItem(ELEVATION_KEY);
    if (!raw) return null;

    const parsed = JSON.parse(raw);

    if (!parsed?.token || !parsed?.expiresAt) return null;
    if (new Date(parsed.expiresAt).getTime() <= Date.now()) {
      sessionStorage.removeItem(ELEVATION_KEY);
      return null;
    }

    return parsed;
  } catch {
    return null;
  }
}

function writeStoredElevation(elevation) {
  try {
    if (elevation) {
      sessionStorage.setItem(ELEVATION_KEY, JSON.stringify(elevation));
    } else {
      sessionStorage.removeItem(ELEVATION_KEY);
    }
  } catch {
    // A private window or a browser blocking storage loses the "remember
    // across a reload" convenience, not the ability to verify again.
  }
}

function readConnectNotice() {
  try {
    const status = new URLSearchParams(window.location.search).get("connect");
    return status ? CONNECT_MESSAGES[status] || "" : "";
  } catch {
    return "";
  }
}

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

// The catalogue shown before the connect form: every channel type this kind
// of platform is expected to offer, not only the ones already wired up.
// Clicking an available one opens the real form below; a "Coming soon" card
// has no click handler at all — the platform's own history is why: the
// previous version of this screen made those buttons look clickable and had
// nothing behind them, and got deleted for it.
function ChannelCatalogGrid({ connectedCounts, supported, onPick }) {
  return (
    <div className="channels-catalog">
      <p className="channels-catalog-intro">
        Pick a channel to connect. Anything marked "Coming soon" is planned
        but not wired up yet — nothing here pretends to work before it does.
      </p>

      {CHANNEL_CATEGORIES.map((category) => (
        <div className="channels-catalog-category" key={category.title}>
          <h4>{category.title}</h4>

          <div className="channels-catalog-grid">
            {category.channels.map((channel) => {
              const isSupported =
                supported.includes(channel.key) &&
                channel.availability === "available";
              const connected = connectedCounts[channel.key] || 0;

              const Icon = resolveChannelIcon(channel.icon);

              const badge = connected
                ? { cls: "is-connected", label: `${connected} connected` }
                : isSupported
                  ? { cls: "is-available", label: "Available" }
                  : { cls: "is-soon", label: "Coming soon" };

              return (
                <div className="channels-catalog-card" key={channel.key}>
                  <div
                    className="channels-catalog-card-icon"
                    style={{
                      background: `${channel.color}1a`,
                      color: channel.color,
                    }}
                  >
                    <Icon fontSize="small" />
                  </div>

                  <div className="channels-catalog-card-body">
                    <div className="channels-catalog-card-head">
                      <span className="channels-catalog-card-name">
                        {channel.name}
                      </span>
                      <span className={`channels-catalog-badge ${badge.cls}`}>
                        {badge.label}
                      </span>
                    </div>

                    {channel.note ? (
                      <span className="channels-catalog-card-note">
                        {channel.note}
                      </span>
                    ) : null}
                  </div>

                  <button
                    type="button"
                    className="channels-catalog-card-connect"
                    disabled={!isSupported}
                    onClick={() => onPick(channel.key)}
                  >
                    {isSupported ? "Connect" : "Coming soon"}
                  </button>
                </div>
              );
            })}
          </div>
        </div>
      ))}
    </div>
  );
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
  const [branches, setBranches] = useState([]);
  const [branchFilter, setBranchFilter] = useState("all");

  const [loading, setLoading] = useState(true);
  const [error, setError] = useState("");

  const [editorOpen, setEditorOpen] = useState(false);
  // "catalog" shows every channel type with a Connect/Coming-soon card;
  // "form" is the existing connect/edit form, reached by picking an
  // available card, or directly when editing an existing account.
  const [formStep, setFormStep] = useState("catalog");
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

  // "Log in with Facebook" is only offered when the server has a Meta app
  // configured; otherwise the button is not shown at all, so nothing on screen
  // suggests a connect method that cannot work yet.
  const [oauthConfigured, setOauthConfigured] = useState(false);
  const [connecting, setConnecting] = useState(false);
  const [connectNotice, setConnectNotice] = useState(readConnectNotice);

  // Connecting or disconnecting an account requires a live elevated grant
  // from confirming an emailed 6-digit code (see channel_verification_service
  // on the backend). One verification covers a whole sitting rather than
  // asking again for every click.
  const [elevation, setElevation] = useState(readStoredElevation);
  const [verifyOpen, setVerifyOpen] = useState(false);
  const [verifyStep, setVerifyStep] = useState("request");
  const [verifyCode, setVerifyCode] = useState("");
  const [verifyBusy, setVerifyBusy] = useState(false);
  const [verifyError, setVerifyError] = useState("");
  const [sessionChanges, setSessionChanges] = useState([]);
  const [changesSummaryOpen, setChangesSummaryOpen] = useState(false);
  const pendingElevatedActionRef = useRef(null);

  function currentElevation() {
    if (!elevation) return null;

    if (new Date(elevation.expiresAt).getTime() <= Date.now()) {
      setElevation(null);
      writeStoredElevation(null);
      return null;
    }

    return elevation;
  }

  // Runs `action(token)` if already verified this sitting; otherwise remembers
  // it and opens the code prompt, which resumes it the moment a code is
  // confirmed.
  function withElevation(action) {
    const live = currentElevation();

    if (live) {
      action(live.token);
      return;
    }

    pendingElevatedActionRef.current = action;
    setVerifyStep("request");
    setVerifyCode("");
    setVerifyError("");
    setVerifyOpen(true);
  }

  function recordSessionChange(summary) {
    setSessionChanges((current) => [...current, summary]);
  }

  async function sendVerificationCode() {
    setVerifyBusy(true);
    setVerifyError("");

    try {
      await requestChannelVerificationRequest();
      setVerifyStep("code");
    } catch (requestError) {
      setVerifyError(
        requestError.message || "The code could not be sent.",
      );
    } finally {
      setVerifyBusy(false);
    }
  }

  async function confirmVerificationCode() {
    setVerifyBusy(true);
    setVerifyError("");

    try {
      const result = await confirmChannelVerificationRequest(verifyCode.trim());
      const next = {
        token: result.elevated_token,
        expiresAt: result.expires_at,
      };

      setElevation(next);
      writeStoredElevation(next);
      setVerifyOpen(false);
      setSessionChanges([]);

      const action = pendingElevatedActionRef.current;
      pendingElevatedActionRef.current = null;
      action?.(next.token);
    } catch (requestError) {
      setVerifyError(requestError.message || "That code is wrong or expired.");
    } finally {
      setVerifyBusy(false);
    }
  }

  function endElevatedSession() {
    setElevation(null);
    writeStoredElevation(null);

    if (sessionChanges.length) {
      setChangesSummaryOpen(true);
    }
  }

  const elevationExpiryLabel = useMemo(() => {
    if (!elevation) return "";

    try {
      return new Date(elevation.expiresAt).toLocaleTimeString([], {
        hour: "2-digit",
        minute: "2-digit",
      });
    } catch {
      return "";
    }
  }, [elevation]);

  const visibleItems = useMemo(() => {
    if (branchFilter === "all") {
      return items;
    }

    if (branchFilter === "none") {
      return items.filter((row) => !row.branch_id);
    }

    return items.filter((row) => String(row.branch_id) === branchFilter);
  }, [items, branchFilter]);

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
      setBranches(Array.isArray(result?.branches) ? result.branches : []);
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

  useEffect(() => {
    let cancelled = false;
    facebookOAuthConfigRequest()
      .then((result) => {
        if (!cancelled) setOauthConfigured(Boolean(result?.configured));
      })
      .catch(() => {});
    return () => {
      cancelled = true;
    };
  }, []);

  // The Facebook callback lands the person back here with ?connect=... The
  // message was read into state above; here we just strip the params from the
  // URL so a refresh does not repeat the notice.
  useEffect(() => {
    const params = new URLSearchParams(window.location.search);
    if (!params.get("connect")) return;
    params.delete("connect");
    params.delete("reason");
    const next = params.toString();
    window.history.replaceState(
      {},
      "",
      window.location.pathname + (next ? `?${next}` : ""),
    );
  }, []);

  const connectWithFacebook = useCallback(async () => {
    setConnecting(true);
    setConnectNotice("");
    try {
      const result = await startFacebookOAuthRequest();
      if (result?.authorize_url) {
        window.location.href = result.authorize_url;
        return;
      }
      setConnectNotice("Could not start Facebook sign-in. Please try again.");
    } catch (requestError) {
      setConnectNotice(
        requestError.message || "Could not start Facebook sign-in.",
      );
    } finally {
      setConnecting(false);
    }
  }, []);

  const routingField = routingFields[form.channel] || "";

  function resetFormState() {
    setClearAccessToken(false);
    setClearVerifyToken(false);
    setFormError("");
    setFormConflict("");
    setSaveStatus("");
  }

  function openCreate() {
    setSelected(null);
    setFormStep("catalog");
    resetFormState();
    setEditorOpen(true);
  }

  function pickChannel(channel) {
    setForm(emptyForm(channel));
    setFormStep("form");
  }

  function openEdit(account) {
    setSelected(account);
    setForm(formFromAccount(account));
    setFormStep("form");
    resetFormState();
    setEditorOpen(true);
  }

  function closeEditor() {
    setEditorOpen(false);
    setSelected(null);
    setFormStep("catalog");
    resetFormState();
  }

  function updateField(key, value) {
    setSaveStatus("");
    setForm((current) => ({ ...current, [key]: value }));
  }

  function handleSubmit(event) {
    event.preventDefault();

    if (selected) {
      submitUpdate();
    } else {
      submitConnect();
    }
  }

  async function submitUpdate() {
    setSaving(true);
    setFormError("");
    setFormConflict("");
    setSaveStatus("");

    try {
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

      await loadAccounts();
      setClearAccessToken(false);
      setClearVerifyToken(false);
      setForm((current) => ({
        ...current,
        access_token: "",
        verify_token: "",
      }));
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

  // Connecting a new account requires an elevated grant, unlike editing one
  // (submitUpdate above): building the values happens up front so the code
  // prompt, if one is needed, does not lose what was typed into the form.
  function submitConnect() {
    setFormError("");
    setFormConflict("");
    setSaveStatus("");

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

    withElevation((token) => performConnect(values, token));
  }

  async function performConnect(values, token) {
    setSaving(true);

    try {
      await createChannelAccountRequest(values, token);
      setSaveStatus("Account connected.");
      recordSessionChange(`Connected ${channelLabel(values.channel)} — ${values.name}`);

      await loadAccounts();
      setClearAccessToken(false);
      setClearVerifyToken(false);
      setForm((current) => ({
        ...current,
        access_token: "",
        verify_token: "",
      }));
      closeEditor();
    } catch (requestError) {
      const message =
        requestError.message || "The account could not be saved.";

      if (requestError.status === 409) {
        setFormConflict(message);
      } else {
        setFormError(message);
      }
    } finally {
      setSaving(false);
    }
  }

  function handleDelete() {
    if (!pendingDelete) return;

    const target = pendingDelete;
    setPendingDelete(null);

    withElevation((token) => performDelete(target, token));
  }

  async function performDelete(target, token) {
    setDeleting(true);

    try {
      await deleteChannelAccountRequest(target.id, token);

      if (selected?.id === target.id) {
        closeEditor();
      }

      recordSessionChange(
        `Disconnected ${target.name || channelLabel(target.channel)}`,
      );
      await loadAccounts();
    } catch (requestError) {
      setError(
        requestError.message || "The account could not be disconnected.",
      );
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

  const connectedCounts = useMemo(
    () =>
      items.reduce((map, row) => {
        map[row.channel] = (map[row.channel] || 0) + 1;
        return map;
      }, {}),
    [items],
  );

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

            {oauthConfigured ? (
              <AppButton
                variant="secondary"
                onClick={connectWithFacebook}
                disabled={connecting}
              >
                {connecting ? "Connecting…" : "Log in with Facebook"}
              </AppButton>
            ) : null}

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

      {connectNotice ? (
        <AppCard padding="small" className="channels-connect-notice">
          <span>{connectNotice}</span>
          <button
            type="button"
            className="channels-connect-notice-dismiss"
            onClick={() => setConnectNotice("")}
            aria-label="Dismiss"
          >
            <CloseOutlined fontSize="small" />
          </button>
        </AppCard>
      ) : null}

      {elevation ? (
        <AppCard padding="small" className="channels-connect-notice">
          <span>
            Verified until {elevationExpiryLabel} — connect or disconnect
            channels without entering the code again until then.
          </span>
          <button
            type="button"
            className="channels-verify-done"
            onClick={endElevatedSession}
          >
            Done{sessionChanges.length ? " — show what changed" : ""}
          </button>
        </AppCard>
      ) : null}

      <div className={`channels-layout ${editorOpen ? "has-editor" : ""}`}>
        <AppCard padding="medium" className="channels-list-card">
          {/* Shown only to a company that has named a location. A filter with
              one option is a control that decides nothing. */}
          {branches.length ? (
            <label htmlFor="channels-branch-filter">
              <span>Branch</span>

              <select
                id="channels-branch-filter"
                value={branchFilter}
                onChange={(event) => setBranchFilter(event.target.value)}
              >
                <option value="all">All branches</option>
                <option value="none">No branch</option>
                {branches.map((branch) => (
                  <option value={String(branch.id)} key={branch.id}>
                    {branch.name}
                  </option>
                ))}
              </select>
            </label>
          ) : null}

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
              rows={visibleItems}
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
                  {selected
                    ? "EDIT CONNECTION"
                    : formStep === "catalog"
                      ? "CHOOSE A CHANNEL"
                      : "CONNECT A NEW ACCOUNT"}
                </span>
                <h3>
                  {selected
                    ? selected.name || `Account #${selected.id}`
                    : formStep === "catalog"
                      ? "What do you want to connect?"
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

            {!selected && formStep === "catalog" ? (
              <ChannelCatalogGrid
                connectedCounts={connectedCounts}
                supported={channelOptions}
                onPick={pickChannel}
              />
            ) : (
              <>
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

              {/* A list of names, not a number to type. This asked the owner
                  for a raw `Branch id` — a value nobody running a business
                  knows, and one that could name another company's branch until
                  the write started checking. The branch is a label for
                  filtering, so it is chosen the way the department above it
                  is. */}
              <label htmlFor="channel-branch">
                <span>Branch (optional)</span>

                <select
                  id="channel-branch"
                  value={form.branch_id}
                  onChange={(event) =>
                    updateField("branch_id", event.target.value)
                  }
                >
                  <option value="">The whole company</option>
                  {branches.map((branch) => (
                    <option value={String(branch.id)} key={branch.id}>
                      {branch.name}
                    </option>
                  ))}
                </select>
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
                  {!selected ? (
                    <AppButton
                      variant="secondary"
                      disabled={saving}
                      onClick={() => setFormStep("catalog")}
                    >
                      Back
                    </AppButton>
                  ) : null}

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
              </>
            )}
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

      <ConfirmDialog
        open={verifyOpen}
        title="Verify your email"
        confirmLabel={verifyStep === "request" ? "Send code" : "Verify"}
        confirmVariant="primary"
        cancelLabel="Cancel"
        loading={verifyBusy}
        onConfirm={
          verifyStep === "request" ? sendVerificationCode : confirmVerificationCode
        }
        onCancel={() => {
          setVerifyOpen(false);
          pendingElevatedActionRef.current = null;
        }}
        message={
          <div className="channels-verify-form">
            {verifyStep === "request" ? (
              <p>
                Connecting or disconnecting a channel first asks for a
                6-digit code sent to your email. Once verified, you can
                connect or disconnect other channels in the same sitting
                without entering it again.
              </p>
            ) : (
              <>
                <p>Enter the 6-digit code sent to your email.</p>
                <input
                  type="text"
                  inputMode="numeric"
                  autoComplete="one-time-code"
                  maxLength={6}
                  className="channels-verify-code-input"
                  value={verifyCode}
                  onChange={(event) =>
                    setVerifyCode(event.target.value.replace(/\D/g, "").slice(0, 6))
                  }
                  autoFocus
                />
              </>
            )}

            {verifyError ? (
              <p className="channels-verify-error">{verifyError}</p>
            ) : null}
          </div>
        }
      />

      <ConfirmDialog
        open={changesSummaryOpen}
        title="What changed"
        confirmLabel="Close"
        cancelLabel="Close"
        onConfirm={() => setChangesSummaryOpen(false)}
        onCancel={() => setChangesSummaryOpen(false)}
        message={
          <ul className="channels-verify-changes">
            {sessionChanges.map((line, index) => (
              <li key={index}>{line}</li>
            ))}
          </ul>
        }
      />
    </div>
  );
}
