import { useEffect, useMemo, useState } from "react";
import {
  ArrowBackOutlined,
  CheckCircleOutlined,
  CloseOutlined,
  ErrorOutlined,
  ExpandLessOutlined,
  ExpandMoreOutlined,
  ForumOutlined,
  LockOutlined,
  SearchOutlined,
} from "@mui/icons-material";
import { useLocation, useNavigate, useSearchParams } from "react-router-dom";
import {
  facebookConnectRequest,
  getCompanySettingSectionRequest,
  updateCompanySettingSectionRequest,
} from "../../api/client";
import { useAuth } from "../../contexts/AuthContext";

const SECTIONS = [
  ["profile", "Company Profile", "Identity, contact information, branches, timezone and business details.", ["Company name", "Workspace code", "Timezone", "Default language"]],
  ["branding", "Branding", "Company logo, colours and customer-facing identity.", ["Logo", "Primary colour", "Secondary colour", "Export branding"]],
  ["ai", "AI Behavior", "AI response timing and human takeover workflow.", []],
  ["knowledge", "AI Teaching & Knowledge", "Instructions, files, FAQs and answer safety.", ["System instructions", "Knowledge sources", "Fallback behavior", "Answer confidence"]],
  ["flow", "Reply Flow", "Order of welcome, language, intent, knowledge and escalation.", ["Welcome step", "Language detection", "Intent detection", "Escalation"]],
  ["replies", "Saved Replies", "Reusable replies, menus and channel templates.", ["Opening message", "Away message", "Quick replies", "Buttons and menus"]],
  ["departments", "Departments", "Departments, routing and escalation paths.", ["Department list", "Default queue", "Escalation owner", "Assignment rules"]],
  ["hours", "Business Hours", "Opening hours and outside-hours behavior.", ["Weekly schedule", "Holidays", "Outside-hours reply", "Timezone"]],
  ["channels", "Channels", "Messenger, WhatsApp, Instagram and Telegram.", ["Connected accounts", "Connection status", "Permissions", "Branch mapping"]],
  ["api", "API & Webhooks", "Callbacks, access keys and integration health.", ["Webhook URL", "Verify token", "API access", "Delivery logs"]],
  ["security", "Security", "Tenant isolation, sessions and audit controls.", ["Encryption status", "Session policy", "Audit retention", "IP restrictions"]],
  ["modules", "Modules", "Enable or hide optional platform modules.", ["Appointments", "Scheduler", "Catalogue", "Team Chat"]],
  ["backup", "Backup", "Backup policy, retention and restoration.", ["Automatic backup", "Retention", "Last backup", "Restore point"]],
  ["subscription", "Subscription", "Plan, limits, usage and billing.", ["Current plan", "Users limit", "AI usage", "Renewal date"]],
];

// Reads the section id from a "/company-settings/{section}" path so links
// like the Dashboard's "Add channel" button land on the right tab.
function sectionFromPath(pathname) {
  const segment = pathname.split("/company-settings/")[1]?.split("/")[0];
  return SECTIONS.some(([id]) => id === segment) ? segment : null;
}

// Sections that expose billing, security or credential-adjacent data.
// Viewing these (not just editing them) requires "settings.manage", not just
// "settings.view". Must stay in sync with backend/api/routes/company_settings.py.
const SENSITIVE_SECTIONS = new Set(["ai", "subscription", "security", "api", "backup"]);

// Frontend tab id -> backend company_settings section name. Any id not
// listed here is passed through unchanged.
const BACKEND_SECTION_ID = {
  profile: "company_profile",
  flow: "reply_flow",
  ai: "ai_behavior",
};

function resolveBackendSectionId(frontendId) {
  return BACKEND_SECTION_ID[frontendId] || frontendId;
}

function slugifyFieldLabel(label) {
  return label
    .trim()
    .toLowerCase()
    .replace(/[^a-z0-9]+/g, "_")
    .replace(/^_+|_+$/g, "");
}

function humanizeReason(reason) {
  return String(reason).trim().replace(/_/g, " ");
}

// The six reply-flow stage ids honored by core/engine.py, in their default
// order. "escalation" is the one conditional step in the engine -- when
// present, the AI's internal human-handoff decision is actually surfaced to
// the customer; when absent, escalation never shows even if the AI decides
// a handoff is warranted.
const REPLY_FLOW_STAGES = [
  {
    id: "welcome",
    label: "Welcome message",
    description: "Greets the customer with an opening message at the start of the conversation.",
  },
  {
    id: "language_detection",
    label: "Language detection",
    description: "Detects which language the customer is writing in before the AI replies.",
  },
  {
    id: "intent_detection",
    label: "Intent / department detection",
    description: "Classifies what the customer needs and routes the conversation to the right department.",
  },
  {
    id: "knowledge_lookup",
    label: "Knowledge base lookup",
    description: "Searches the company's knowledge base for a relevant, grounded answer.",
  },
  {
    id: "answer",
    label: "AI answer generation",
    description: "Generates the AI's reply and sends it to the customer.",
  },
  {
    id: "escalation",
    label: "Escalation to human",
    description: "Conditional step -- see below.",
    conditional: true,
  },
];

const REPLY_FLOW_STAGE_IDS = REPLY_FLOW_STAGES.map((stage) => stage.id);

const WORKFLOW_DEFAULT_VALUES = { reply_access_mode: "take_required", return_to_ai_timeout_minutes: 5, auto_release_to_ai: true, auto_read_mode: "assigned_owner_only" };

function WorkflowSettings() {
  const [values, setValues] = useState(WORKFLOW_DEFAULT_VALUES);
  const [savedValues, setSavedValues] = useState(WORKFLOW_DEFAULT_VALUES);
  const [locked, setLocked] = useState([]);
  const [status, setStatus] = useState("");
  const [saving, setSaving] = useState(false);

  useEffect(() => {
    getCompanySettingSectionRequest("ai_behavior")
      .then((result) => {
        const loadedValues = { ...WORKFLOW_DEFAULT_VALUES, ...(result?.values || {}) };
        setValues(loadedValues);
        setSavedValues(loadedValues);
        setLocked(result?.locked_keys || []);
      })
      .catch((error) => setStatus(error.message || "Settings could not load."));
  }, []);

  async function save() {
    setSaving(true); setStatus("");
    try {
      // Only submit keys the user actually changed -- same pattern as
      // GenericSectionEditor.save(). The backend rejects the entire update
      // if any submitted key is locked, so re-sending an untouched locked
      // key's value would needlessly 409 the save of the other, unlocked
      // AI Behavior fields.
      const nextPayload = {
        reply_access_mode: values.reply_access_mode,
        return_to_ai_timeout_minutes: Math.max(1, Number(values.return_to_ai_timeout_minutes) || 5),
        auto_release_to_ai: Boolean(values.auto_release_to_ai),
        auto_read_mode: "assigned_owner_only",
      };
      const changedValues = {};
      Object.keys(nextPayload).forEach((key) => {
        if (nextPayload[key] !== savedValues[key]) {
          changedValues[key] = nextPayload[key];
        }
      });
      const result = await updateCompanySettingSectionRequest("ai_behavior", changedValues);
      const nextValues = result?.values ? { ...WORKFLOW_DEFAULT_VALUES, ...result.values } : { ...values, ...nextPayload };
      setValues(nextValues);
      setSavedValues(nextValues);
      setLocked(result?.locked_keys || []);
      setStatus("Conversation workflow saved.");
    } catch (error) { setStatus(error.message || "Settings could not save."); }
    finally { setSaving(false); }
  }

  return <div className="workflow-settings-card">
    <div className="workflow-setting-row"><div><strong>Who may reply?</strong><span>Exclusive takeover is safest. Shared mode lets the first employee reply claim an unassigned human chat atomically.</span></div><select value={values.reply_access_mode} disabled={locked.includes("reply_access_mode")} onChange={(e) => setValues({ ...values, reply_access_mode: e.target.value })}><option value="take_required">Take conversation required</option><option value="shared_until_taken">Anyone until first reply</option></select></div>
    <div className="workflow-setting-row"><div><strong>Automatic read</strong><span>Only the assigned employee clears unread messages. Accidental previews by other employees never hide work.</span></div><select value="assigned_owner_only" disabled><option>Assigned owner only</option></select></div>
    <div className="workflow-setting-row"><div><strong>Return to AI timeout</strong><span>After the employee's last reply, ownership is released and AI resumes automatically.</span></div><div className="timeout-input"><input type="number" min="1" max="1440" value={values.return_to_ai_timeout_minutes} disabled={locked.includes("return_to_ai_timeout_minutes")} onChange={(e) => setValues({ ...values, return_to_ai_timeout_minutes: e.target.value })}/><span>minutes</span></div></div>
    <label className="workflow-toggle"><input type="checkbox" checked={Boolean(values.auto_release_to_ai)} disabled={locked.includes("auto_release_to_ai")} onChange={(e) => setValues({ ...values, auto_release_to_ai: e.target.checked })}/><div><strong>Auto-release ownership and return to AI</strong><span>Clears the assigned employee when the timeout expires, completing the full cycle.</span></div></label>
    <div className="workflow-settings-footer"><span>{status}</span><button type="button" onClick={save} disabled={saving}>{saving ? "Saving..." : "Save workflow"}</button></div>
  </div>;
}

function GenericSectionEditor({ section, canManage }) {
  const [frontendId, , , fields] = section;
  const backendSectionId = resolveBackendSectionId(frontendId);
  const fieldKeys = useMemo(() => fields.map((label) => [label, slugifyFieldLabel(label)]), [fields]);

  const [values, setValues] = useState({});
  const [savedValues, setSavedValues] = useState({});
  const [locked, setLocked] = useState([]);
  const [status, setStatus] = useState("");
  const [loading, setLoading] = useState(true);
  const [saving, setSaving] = useState(false);

  useEffect(() => {
    let cancelled = false;
    setLoading(true);
    setStatus("");
    getCompanySettingSectionRequest(backendSectionId)
      .then((result) => {
        if (cancelled) return;
        const loadedValues = result?.values || {};
        setValues(loadedValues);
        setSavedValues(loadedValues);
        setLocked(result?.locked_keys || []);
      })
      .catch((error) => {
        if (cancelled) return;
        setStatus(error.message || "Settings could not load.");
      })
      .finally(() => {
        if (!cancelled) setLoading(false);
      });
    return () => { cancelled = true; };
  }, [backendSectionId]);

  function updateField(key, value) {
    setValues((current) => ({ ...current, [key]: value }));
  }

  async function save() {
    setSaving(true);
    setStatus("");
    try {
      // Only submit fields the user actually changed. Locked fields are
      // disabled and therefore never change, but even for unlocked fields
      // the backend rejects the whole update if any submitted key is
      // locked -- so re-sending an untouched locked field's value would
      // needlessly 409 the save of an unrelated field in the same section.
      const changedValues = {};
      fieldKeys.forEach(([, key]) => {
        const nextValue = values[key] ?? "";
        const previousValue = savedValues[key] ?? "";
        if (nextValue !== previousValue) {
          changedValues[key] = nextValue;
        }
      });
      const result = await updateCompanySettingSectionRequest(backendSectionId, changedValues);
      const nextValues = result?.values || { ...values, ...changedValues };
      setValues(nextValues);
      setSavedValues(nextValues);
      setLocked(result?.locked_keys || []);
      setStatus("Saved.");
    } catch (error) {
      setStatus(error.message || "Settings could not save.");
    } finally {
      setSaving(false);
    }
  }

  if (loading) {
    return <div className="company-setting-fields"><p>Loading section...</p></div>;
  }

  if (!fieldKeys.length) {
    return <div className="company-setting-fields"><p>This section has no configurable fields yet.</p></div>;
  }

  return <>
    {!canManage ? (
      <p className="company-settings-readonly-note"><LockOutlined fontSize="small" /> You have read-only access to this section. Ask an administrator for the "Manage Settings" permission to make changes.</p>
    ) : null}
    <div className="company-setting-fields">
      {fieldKeys.map(([label, key]) => {
        const isLocked = locked.includes(key);
        const disabled = saving || !canManage || isLocked;
        return (
          <article className="company-setting-field" key={key}>
            <div>
              <strong>{label}</strong>
              <span>{isLocked ? "Locked by Super Admin." : "Company-wide setting."}</span>
            </div>
            <input
              type="text"
              value={values[key] ?? ""}
              disabled={disabled}
              onChange={(e) => updateField(key, e.target.value)}
              placeholder={label}
            />
          </article>
        );
      })}
    </div>
    <div className="workflow-settings-footer">
      <span>{status}</span>
      {canManage ? (
        <button type="button" onClick={save} disabled={saving}>{saving ? "Saving..." : "Save"}</button>
      ) : null}
    </div>
  </>;
}

function FacebookConnectAction({ canManage }) {
  const [connecting, setConnecting] = useState(false);
  const [error, setError] = useState("");

  async function connect() {
    setConnecting(true);
    setError("");
    try {
      const result = await facebookConnectRequest();
      if (!result?.authorize_url) {
        throw new Error("The server did not return a Facebook authorization link.");
      }
      // Facebook's OAuth dialog cannot be reached with fetch/XHR -- the
      // browser itself must navigate there so Facebook can eventually
      // redirect it back to our backend callback.
      window.location.href = result.authorize_url;
    } catch (err) {
      setError(err.message || "Could not start the Facebook connection. Please try again.");
      setConnecting(false);
    }
  }

  return (
    <article className="channel-connect-card">
      <div>
        <strong>Facebook Messenger &amp; Instagram</strong>
        <span>Connect a Facebook Page to link Messenger, and any Instagram Business account linked to it.</span>
        {error ? <span className="channel-connect-error">{error}</span> : null}
      </div>
      <button type="button" onClick={connect} disabled={!canManage || connecting}>
        {connecting ? "Connecting..." : "Connect Facebook"}
      </button>
    </article>
  );
}

function ReplyFlowEditor({ canManage }) {
  const [order, setOrder] = useState(REPLY_FLOW_STAGE_IDS);
  const [enabled, setEnabled] = useState(() => new Set(REPLY_FLOW_STAGE_IDS));
  const [loading, setLoading] = useState(true);
  const [status, setStatus] = useState("");
  const [saving, setSaving] = useState(false);

  function applySteps(steps) {
    const activeKnown = (Array.isArray(steps) ? steps : []).filter((id) => REPLY_FLOW_STAGE_IDS.includes(id));
    const missing = REPLY_FLOW_STAGE_IDS.filter((id) => !activeKnown.includes(id));
    setOrder([...activeKnown, ...missing]);
    setEnabled(new Set(activeKnown));
  }

  useEffect(() => {
    let cancelled = false;
    setLoading(true);
    setStatus("");
    getCompanySettingSectionRequest("reply_flow")
      .then((result) => {
        if (cancelled) return;
        applySteps(result?.values?.steps);
      })
      .catch((error) => {
        if (cancelled) return;
        setStatus(error.message || "Reply flow could not load.");
      })
      .finally(() => {
        if (!cancelled) setLoading(false);
      });
    return () => { cancelled = true; };
  }, []);

  function toggleStage(id) {
    setEnabled((current) => {
      const next = new Set(current);
      if (next.has(id)) next.delete(id); else next.add(id);
      return next;
    });
  }

  function moveStage(id, direction) {
    setOrder((current) => {
      const index = current.indexOf(id);
      const swapWith = index + direction;
      if (index < 0 || swapWith < 0 || swapWith >= current.length) return current;
      const next = [...current];
      [next[index], next[swapWith]] = [next[swapWith], next[index]];
      return next;
    });
  }

  async function save() {
    setSaving(true);
    setStatus("");
    try {
      const steps = order.filter((id) => enabled.has(id));
      const result = await updateCompanySettingSectionRequest("reply_flow", { steps });
      applySteps(result?.values?.steps || steps);
      setStatus("Reply flow saved.");
    } catch (error) {
      setStatus(error.message || "Reply flow could not save.");
    } finally {
      setSaving(false);
    }
  }

  if (loading) {
    return <div className="company-setting-fields"><p>Loading reply flow...</p></div>;
  }

  return <>
    {!canManage ? (
      <p className="company-settings-readonly-note"><LockOutlined fontSize="small" /> You have read-only access to this section. Ask an administrator for the "Manage Settings" permission to make changes.</p>
    ) : null}
    <ol className="reply-flow-list">
      {order.map((id, index) => {
        const stage = REPLY_FLOW_STAGES.find((item) => item.id === id);
        const isOn = enabled.has(id);
        const disabled = saving || !canManage;
        return (
          <li key={id} className={`reply-flow-card${stage.conditional ? " is-conditional" : ""}${isOn ? "" : " is-off"}`}>
            <div className="reply-flow-card-main">
              <div className="reply-flow-card-order">
                <button type="button" onClick={() => moveStage(id, -1)} disabled={disabled || index === 0} aria-label={`Move ${stage.label} earlier`}><ExpandLessOutlined fontSize="small" /></button>
                <span>{index + 1}</span>
                <button type="button" onClick={() => moveStage(id, 1)} disabled={disabled || index === order.length - 1} aria-label={`Move ${stage.label} later`}><ExpandMoreOutlined fontSize="small" /></button>
              </div>
              <div className="reply-flow-card-copy">
                <strong>{stage.conditional ? <ForumOutlined fontSize="small" /> : null} {stage.label}</strong>
                <span>{stage.description}</span>
              </div>
              <button
                type="button"
                className={isOn ? "settings-toggle settings-toggle-on" : "settings-toggle"}
                aria-pressed={isOn}
                aria-label={`${stage.label} ${isOn ? "on" : "off"}`}
                disabled={disabled}
                onClick={() => toggleStage(id)}
              ><span /></button>
            </div>
            {stage.conditional ? (
              <div className="reply-flow-condition">
                <p><strong>Condition:</strong> the AI decides during the conversation whether the customer needs a human.</p>
                <div className="reply-flow-condition-outcomes">
                  <div className="reply-flow-outcome is-yes"><strong>Yes</strong><span>&rarr; hand off to a human employee (shown to the customer)</span></div>
                  <div className="reply-flow-outcome is-no"><strong>No</strong><span>&rarr; the AI keeps handling the conversation</span></div>
                </div>
                <p className="reply-flow-condition-note">
                  {isOn
                    ? "Active: when the AI decides a handoff is needed, the customer sees the escalation."
                    : "Off: escalation is never shown to the customer, even if the AI internally decides a handoff is needed."}
                </p>
              </div>
            ) : null}
          </li>
        );
      })}
    </ol>
    <div className="workflow-settings-footer">
      <span>{status}</span>
      {canManage ? (
        <button type="button" onClick={save} disabled={saving}>{saving ? "Saving..." : "Save reply flow"}</button>
      ) : null}
    </div>
  </>;
}

export default function CompanySettingsPage() {
  const navigate = useNavigate();
  const location = useLocation();
  const { hasPermission } = useAuth();
  const [active, setActive] = useState(() => sectionFromPath(location.pathname) || "profile");
  const [query, setQuery] = useState("");
  const [searchParams, setSearchParams] = useSearchParams();
  const [channelBanner, setChannelBanner] = useState(null);

  // Keep the active tab in sync with the URL (e.g. navigating to
  // /company-settings/channels from the Dashboard's "Add channel" button)
  // even when this component is already mounted and doesn't remount.
  useEffect(() => {
    const section = sectionFromPath(location.pathname);
    if (section) setActive(section);
  }, [location.pathname]);

  // Facebook's OAuth callback redirects the raw browser back here with
  // ?connected=facebook&status=ok|error(&reason=...). Read that once on
  // mount, jump straight to the Channels tab, surface a clear banner, then
  // strip the params so a refresh doesn't re-show it.
  useEffect(() => {
    if (searchParams.get("connected") !== "facebook") return;

    const outcome = searchParams.get("status");
    const reason = searchParams.get("reason");

    setActive("channels");
    setChannelBanner(
      outcome === "ok"
        ? { type: "success", message: "Facebook connected successfully." }
        : {
          type: "error",
          message: reason
            ? `Facebook connection failed: ${humanizeReason(reason)}.`
            : "Facebook connection failed.",
        },
    );

    const nextParams = new URLSearchParams(searchParams);
    nextParams.delete("connected");
    nextParams.delete("status");
    nextParams.delete("reason");
    setSearchParams(nextParams, { replace: true });
    // Intentionally mount-only: this must fire once for the redirect that
    // landed us here, not every time searchParams changes.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  const canViewSection = (id) => {
    if (SENSITIVE_SECTIONS.has(id)) return hasPermission("settings.manage");
    return hasPermission("settings.view");
  };
  const canManageSettings = hasPermission("settings.manage");

  const allowedSections = useMemo(
    () => SECTIONS.filter(([id]) => canViewSection(id)),
    // eslint-disable-next-line react-hooks/exhaustive-deps
    [hasPermission],
  );

  const visible = useMemo(
    () => allowedSections.filter(([, title, description]) => `${title} ${description}`.toLowerCase().includes(query.toLowerCase())),
    [allowedSections, query],
  );

  if (!allowedSections.length) {
    return (
      <section className="company-settings-shell company-settings-locked-layout">
        <main className="company-settings-content">
          <div className="company-settings-content-scroll">
            <header>
              <span>COMPANY CONTROL</span>
              <h2>Company Settings</h2>
              <p>You don't have access to company settings. Ask a company administrator to grant you the "View Settings" permission.</p>
            </header>
            <button type="button" onClick={() => navigate("/dashboard")}><ArrowBackOutlined /> Back to platform</button>
          </div>
        </main>
      </section>
    );
  }

  const selected = allowedSections.find(([id]) => id === active) || visible[0] || allowedSections[0];
  const selectedCanManage = canManageSettings;

  return <section className="company-settings-shell company-settings-locked-layout"><aside className="company-settings-nav"><button className="company-settings-back" type="button" onClick={() => navigate("/dashboard")}><ArrowBackOutlined /> Back to platform</button><div className="company-settings-nav-heading"><span>COMPANY CONTROL</span><h1>Company Settings</h1></div><label className="settings-search"><SearchOutlined /><input value={query} onChange={(e) => setQuery(e.target.value)} placeholder="Search company settings..." /></label><nav className="company-settings-nav-scroll">{visible.map(([id,title]) => <button type="button" key={id} className={active===id?"is-active":""} onClick={()=>setActive(id)}>{title}</button>)}</nav></aside><main className="company-settings-content"><div className="company-settings-content-scroll"><header><span>COMPANY CONTROL</span><h2>{selected[1]}</h2><p>{selected[2]}</p></header>
    {active === "channels" && channelBanner ? (
      <div className={`settings-banner is-${channelBanner.type}`} role="status">
        {channelBanner.type === "success" ? <CheckCircleOutlined fontSize="small" /> : <ErrorOutlined fontSize="small" />}
        <span>{channelBanner.message}</span>
        <button type="button" className="settings-banner-dismiss" onClick={() => setChannelBanner(null)} aria-label="Dismiss">
          <CloseOutlined fontSize="small" />
        </button>
      </div>
    ) : null}
    {active === "ai" ? (
      <WorkflowSettings />
    ) : active === "flow" ? (
      <ReplyFlowEditor canManage={selectedCanManage} />
    ) : (
      <>
        {active === "channels" ? <FacebookConnectAction canManage={selectedCanManage} /> : null}
        <GenericSectionEditor section={selected} canManage={selectedCanManage} />
        <div className="settings-card-grid"><article className="settings-card"><h3>Company-wide setting</h3><p>Changes in this section apply to authorized users across the company.</p></article><article className="settings-card"><h3>Super Admin policy</h3><p>Availability, labels and locked defaults can be controlled by the separate Super Admin control plane.</p></article></div>
      </>
    )}
    </div></main></section>;
}
