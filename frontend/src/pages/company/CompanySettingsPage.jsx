import { useEffect, useMemo, useState } from "react";
import { ArrowBackOutlined, SearchOutlined } from "@mui/icons-material";
import { useNavigate } from "react-router-dom";
import { getCompanySettingSectionRequest, updateCompanySettingSectionRequest, getMySubscriptionRequest } from "../../api/client";
import SecureChannelsPanel from "./SecureChannelsPanel";

const SECTIONS = [
  ["profile", "Company Profile", "Identity, contact information, branches, timezone and business details.", ["Company name", "Workspace code", "Timezone", "Default language"]],
  ["branding", "Branding", "Company logo, colours and customer-facing identity.", ["Logo", "Primary colour", "Secondary colour", "Export branding"]],
  ["ai", "AI Behavior", "AI response timing and human takeover workflow.", []],
  ["knowledge", "AI Teaching & Knowledge", "Instructions, files, FAQs and answer safety.", ["System instructions", "Knowledge sources", "Fallback behavior", "Answer confidence"]],
  ["flow", "Reply Flow", "Order of welcome, language, intent, knowledge and escalation.", ["Welcome step", "Language detection", "Intent detection", "Escalation"]],
  ["replies", "Saved Replies", "Reusable replies, menus and channel templates.", ["Opening message", "Away message", "Quick replies", "Buttons and menus"]],
  ["departments", "Departments", "Departments, routing and escalation paths.", ["Department list", "Default queue", "Escalation owner", "Assignment rules"]],
  ["hours", "Business Hours", "Opening hours and outside-hours behavior.", ["Weekly schedule", "Holidays", "Outside-hours reply", "Timezone"]],
  ["channels", "Channels", "Messenger, WhatsApp, Instagram, Telegram, email and website.", ["Connected accounts", "Connection status", "Permissions", "Branch mapping"]],
  ["api", "API & Webhooks", "Callbacks, access keys and integration health.", ["Webhook URL", "Verify token", "API access", "Delivery logs"]],
  ["security", "Security", "Tenant isolation, sessions and audit controls.", ["Encryption status", "Session policy", "Audit retention", "IP restrictions"]],
  ["modules", "Modules", "Enable or hide optional platform modules.", ["Appointments", "Scheduler", "Catalogue", "Team Chat"]],
  ["backup", "Backup", "Backup policy, retention and restoration.", ["Automatic backup", "Retention", "Last backup", "Restore point"]],
  ["subscription", "Subscription", "Plan, limits, usage and billing.", ["Current plan", "Users limit", "AI usage", "Renewal date"]],
];

function WorkflowSettings() {
  const [values, setValues] = useState({ reply_access_mode: "take_required", return_to_ai_timeout_minutes: 5, auto_release_to_ai: true, auto_read_mode: "assigned_owner_only" });
  const [locked, setLocked] = useState([]);
  const [status, setStatus] = useState("");
  const [saving, setSaving] = useState(false);

  useEffect(() => {
    getCompanySettingSectionRequest("ai_behavior")
      .then((result) => { setValues((current) => ({ ...current, ...(result?.values || {}) })); setLocked(result?.locked_keys || []); })
      .catch((error) => setStatus(error.message || "Settings could not load."));
  }, []);

  async function save() {
    setSaving(true); setStatus("");
    try {
      const result = await updateCompanySettingSectionRequest("ai_behavior", {
        reply_access_mode: values.reply_access_mode,
        return_to_ai_timeout_minutes: Math.max(1, Number(values.return_to_ai_timeout_minutes) || 5),
        auto_release_to_ai: Boolean(values.auto_release_to_ai),
        auto_read_mode: "assigned_owner_only",
      });
      setValues((current) => ({ ...current, ...(result?.values || {}) }));
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

function SubscriptionView() {
  const [data, setData] = useState(null);
  const [error, setError] = useState("");

  useEffect(() => {
    getMySubscriptionRequest()
      .then(setData)
      .catch((e) => setError(e.message || "Could not load subscription."));
  }, []);

  if (error) return <p style={{ color: "#c0392b" }}>{error}</p>;
  if (!data) return <p>Loading…</p>;
  if (!data.has_subscription) return <p>No active subscription yet — contact your platform administrator.</p>;

  return (
    <div className="company-setting-fields">
      <article className="company-setting-field">
        <div>
          <strong>{data.plan_name} plan</strong>
          <span>
            {data.subscription_status}
            {data.expires_at ? ` · renews/expires ${data.expires_at.slice(0, 10)}` : ""}
            {" · $"}{data.price_monthly}/mo
          </span>
        </div>
      </article>
      <article className="company-setting-field">
        <div><strong>Users</strong><span>{data.users.used} / {data.users.max} used</span></div>
      </article>
      <article className="company-setting-field">
        <div><strong>Connected channels</strong><span>{data.channels.used} / {data.channels.max} used</span></div>
      </article>
      <article className="company-setting-field">
        <div><strong>AI messages / month</strong><span>limit: {data.max_ai_messages}</span></div>
      </article>
      <article className="company-setting-field">
        <div>
          <strong>Features on this plan</strong>
          <span>
            {Object.entries(data.features).filter(([, on]) => on).map(([k]) => k).join(", ") || "none enabled"}
          </span>
        </div>
      </article>
      <div className="settings-card-grid">
        <article className="settings-card">
          <h3>Managed by your platform administrator</h3>
          <p>Plan changes, limits and feature toggles are controlled from the Super Admin dashboard — this view is read-only.</p>
        </article>
      </div>
    </div>
  );
}

function ProfileSettings() {
  const [values, setValues] = useState({ company_name: "", workspace_code: "", timezone: "Asia/Beirut", default_language: "ar" });
  const [locked, setLocked] = useState([]);
  const [status, setStatus] = useState("");
  const [saving, setSaving] = useState(false);

  useEffect(() => {
    getCompanySettingSectionRequest("company_profile")
      .then((result) => { setValues((current) => ({ ...current, ...(result?.values || {}) })); setLocked(result?.locked_keys || []); })
      .catch((error) => setStatus(error.message || "Settings could not load."));
  }, []);

  async function save() {
    setSaving(true); setStatus("");
    try {
      const result = await updateCompanySettingSectionRequest("company_profile", values);
      setValues((current) => ({ ...current, ...(result?.values || {}) }));
      setStatus("Company profile saved.");
    } catch (error) { setStatus(error.message || "Settings could not save."); }
    finally { setSaving(false); }
  }

  return (
    <div className="workflow-settings-card">
      <div className="workflow-setting-row">
        <div><strong>Company name</strong></div>
        <input value={values.company_name} disabled={locked.includes("company_name")} onChange={(e) => setValues({ ...values, company_name: e.target.value })} />
      </div>
      <div className="workflow-setting-row">
        <div><strong>Workspace code</strong></div>
        <input value={values.workspace_code} disabled={locked.includes("workspace_code")} onChange={(e) => setValues({ ...values, workspace_code: e.target.value })} />
      </div>
      <div className="workflow-setting-row">
        <div><strong>Timezone</strong></div>
        <input value={values.timezone} disabled={locked.includes("timezone")} onChange={(e) => setValues({ ...values, timezone: e.target.value })} placeholder="Asia/Beirut" />
      </div>
      <div className="workflow-setting-row">
        <div><strong>Default language</strong></div>
        <select value={values.default_language} disabled={locked.includes("default_language")} onChange={(e) => setValues({ ...values, default_language: e.target.value })}>
          <option value="ar">Arabic</option>
          <option value="en">English</option>
        </select>
      </div>
      <div className="workflow-settings-footer"><span>{status}</span><button type="button" onClick={save} disabled={saving}>{saving ? "Saving..." : "Save profile"}</button></div>
    </div>
  );
}

const FLOW_STEP_LABELS = {
  welcome: "Welcome message",
  language_detection: "Language detection",
  intent_detection: "Intent detection",
  knowledge_lookup: "Knowledge lookup",
  answer: "Answer",
  escalation: "Escalation to human",
};

function ReplyFlowSettings() {
  const [steps, setSteps] = useState([]);
  const [locked, setLocked] = useState([]);
  const [status, setStatus] = useState("");
  const [saving, setSaving] = useState(false);

  useEffect(() => {
    getCompanySettingSectionRequest("reply_flow")
      .then((result) => { setSteps(result?.values?.steps || []); setLocked(result?.locked_keys || []); })
      .catch((error) => setStatus(error.message || "Settings could not load."));
  }, []);

  function moveStep(index, direction) {
    const next = [...steps];
    const target = index + direction;
    if (target < 0 || target >= next.length) return;
    [next[index], next[target]] = [next[target], next[index]];
    setSteps(next);
  }

  async function save() {
    setSaving(true); setStatus("");
    try {
      const result = await updateCompanySettingSectionRequest("reply_flow", { steps });
      setSteps(result?.values?.steps || steps);
      setStatus("Reply flow order saved.");
    } catch (error) { setStatus(error.message || "Settings could not save."); }
    finally { setSaving(false); }
  }

  return (
    <div className="workflow-settings-card">
      {steps.map((step, index) => (
        <div className="workflow-setting-row" key={step}>
          <div><strong>{index + 1}. {FLOW_STEP_LABELS[step] || step}</strong></div>
          <div style={{ display: "flex", gap: 8 }}>
            <button type="button" disabled={locked.includes("steps") || index === 0} onClick={() => moveStep(index, -1)}>↑</button>
            <button type="button" disabled={locked.includes("steps") || index === steps.length - 1} onClick={() => moveStep(index, 1)}>↓</button>
          </div>
        </div>
      ))}
      <div className="workflow-settings-footer"><span>{status}</span><button type="button" onClick={save} disabled={saving}>{saving ? "Saving..." : "Save order"}</button></div>
    </div>
  );
}

const MODULE_LABELS = {
  appointments: "Appointments",
  scheduler: "Scheduler",
  catalogue: "Catalogue",
  team_chat: "Team Chat",
  comments: "Comments",
};

function ModulesSettings() {
  const [values, setValues] = useState({});
  const [locked, setLocked] = useState([]);
  const [status, setStatus] = useState("");
  const [saving, setSaving] = useState(false);

  useEffect(() => {
    getCompanySettingSectionRequest("modules")
      .then((result) => { setValues(result?.values || {}); setLocked(result?.locked_keys || []); })
      .catch((error) => setStatus(error.message || "Settings could not load."));
  }, []);

  async function save() {
    setSaving(true); setStatus("");
    try {
      const result = await updateCompanySettingSectionRequest("modules", values);
      setValues(result?.values || values);
      setStatus("Modules saved.");
    } catch (error) { setStatus(error.message || "Settings could not save."); }
    finally { setSaving(false); }
  }

  return (
    <div className="workflow-settings-card">
      {Object.entries(MODULE_LABELS).map(([key, label]) => (
        <label className="workflow-toggle" key={key}>
          <input
            type="checkbox" checked={Boolean(values[key])} disabled={locked.includes(key)}
            onChange={(e) => setValues({ ...values, [key]: e.target.checked })}
          />
          <div><strong>{label}</strong></div>
        </label>
      ))}
      <div className="workflow-settings-footer"><span>{status}</span><button type="button" onClick={save} disabled={saving}>{saving ? "Saving..." : "Save modules"}</button></div>
    </div>
  );
}

function SecurityStatusView() {
  const [changes, setChanges] = useState([]);
  const [status, setStatus] = useState("Checked, ready to load.");

  useEffect(() => {
    setStatus("");
  }, []);

  return (
    <div className="company-setting-fields">
      <article className="company-setting-field">
        <div><strong>Channel credential access</strong><span>Requires a 6-digit email verification code, valid for 20 minutes per session. See Channels tab.</span></div>
      </article>
      <article className="company-setting-field">
        <div><strong>Credential storage</strong><span>Channel access tokens are encrypted at rest before being saved.</span></div>
      </article>
      <article className="company-setting-field">
        <div><strong>Session change log</strong><span>Every connect/disconnect during a verified session is recorded and shown to you when you finish — see Channels tab, "Done — show what changed".</span></div>
      </article>
      {status ? <p>{status}</p> : null}
    </div>
  );
}

export default function CompanySettingsPage() {
  const navigate = useNavigate(); const [active, setActive] = useState("profile"); const [query, setQuery] = useState("");
  const visible = useMemo(() => SECTIONS.filter(([, title, description]) => `${title} ${description}`.toLowerCase().includes(query.toLowerCase())), [query]);
  const selected = SECTIONS.find(([id]) => id === active) || visible[0] || SECTIONS[0];
  return <section className="company-settings-shell company-settings-locked-layout"><aside className="company-settings-nav"><button className="company-settings-back" type="button" onClick={() => navigate("/dashboard")}><ArrowBackOutlined /> Back to platform</button><div className="company-settings-nav-heading"><span>COMPANY CONTROL</span><h1>Company Settings</h1></div><label className="settings-search"><SearchOutlined /><input value={query} onChange={(e) => setQuery(e.target.value)} placeholder="Search company settings..." /></label><nav className="company-settings-nav-scroll">{visible.map(([id,title]) => <button type="button" key={id} className={active===id?"is-active":""} onClick={()=>setActive(id)}>{title}</button>)}</nav></aside><main className="company-settings-content"><div className="company-settings-content-scroll"><header><span>COMPANY CONTROL</span><h2>{selected[1]}</h2><p>{selected[2]}</p></header>{active === "ai" ? <WorkflowSettings /> : active === "channels" ? <SecureChannelsPanel /> : active === "subscription" ? <SubscriptionView /> : active === "profile" ? <ProfileSettings /> : active === "flow" ? <ReplyFlowSettings /> : active === "modules" ? <ModulesSettings /> : active === "security" ? <SecurityStatusView /> : <><div className="company-setting-fields">{selected[3].map((field,index)=><article className="company-setting-field" key={field}><div><strong>{field}</strong><span>{index===0?"Configured from this company workspace.":"Ready for company-wide configuration."}</span></div><button type="button">Configure</button></article>)}</div><div className="settings-card-grid"><article className="settings-card"><h3>Company-wide setting</h3><p>Changes in this section apply to authorized users across the company.</p></article><article className="settings-card"><h3>Super Admin policy</h3><p>Availability, labels and locked defaults can be controlled by the separate Super Admin control plane.</p></article></div></>}</div></main></section>;
}
