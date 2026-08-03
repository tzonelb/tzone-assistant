import { useEffect, useMemo, useState } from "react";
import { ArrowBackOutlined, LockOutlined, SearchOutlined } from "@mui/icons-material";
import { useNavigate } from "react-router-dom";
import { getCompanySettingSectionRequest, updateCompanySettingSectionRequest } from "../../api/client";
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

function GenericSectionEditor({ section, canManage }) {
  const [frontendId, , , fields] = section;
  const backendSectionId = resolveBackendSectionId(frontendId);
  const fieldKeys = useMemo(() => fields.map((label) => [label, slugifyFieldLabel(label)]), [fields]);

  const [values, setValues] = useState({});
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
        setValues(result?.values || {});
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
      const changedValues = {};
      fieldKeys.forEach(([, key]) => {
        changedValues[key] = values[key] ?? "";
      });
      const result = await updateCompanySettingSectionRequest(backendSectionId, changedValues);
      setValues(result?.values || changedValues);
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

export default function CompanySettingsPage() {
  const navigate = useNavigate();
  const { hasPermission } = useAuth();
  const [active, setActive] = useState("profile");
  const [query, setQuery] = useState("");

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

  return <section className="company-settings-shell company-settings-locked-layout"><aside className="company-settings-nav"><button className="company-settings-back" type="button" onClick={() => navigate("/dashboard")}><ArrowBackOutlined /> Back to platform</button><div className="company-settings-nav-heading"><span>COMPANY CONTROL</span><h1>Company Settings</h1></div><label className="settings-search"><SearchOutlined /><input value={query} onChange={(e) => setQuery(e.target.value)} placeholder="Search company settings..." /></label><nav className="company-settings-nav-scroll">{visible.map(([id,title]) => <button type="button" key={id} className={active===id?"is-active":""} onClick={()=>setActive(id)}>{title}</button>)}</nav></aside><main className="company-settings-content"><div className="company-settings-content-scroll"><header><span>COMPANY CONTROL</span><h2>{selected[1]}</h2><p>{selected[2]}</p></header>{active === "ai" ? <WorkflowSettings /> : <><GenericSectionEditor section={selected} canManage={selectedCanManage} /><div className="settings-card-grid"><article className="settings-card"><h3>Company-wide setting</h3><p>Changes in this section apply to authorized users across the company.</p></article><article className="settings-card"><h3>Super Admin policy</h3><p>Availability, labels and locked defaults can be controlled by the separate Super Admin control plane.</p></article></div></>}</div></main></section>;
}
