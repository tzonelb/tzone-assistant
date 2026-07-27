import { useEffect, useMemo, useState } from "react";
import { ArrowBackOutlined, SearchOutlined } from "@mui/icons-material";
import { useNavigate } from "react-router-dom";
import { getCompanySettingSectionRequest, updateCompanySettingSectionRequest, getMySubscriptionRequest, getMyModulesRequest, getPlansCatalogRequest, requestPlanChangeRequest, getMySubscriptionRequestsRequest, listSavedRepliesRequest, createSavedReplyRequest, updateSavedReplyRequest, deleteSavedReplyRequest } from "../../api/client";
import SecureChannelsPanel from "./SecureChannelsPanel";

const SECTIONS = [
  ["profile", "Company Profile", "Identity, contact information, branches, timezone and business details.", ["Company name", "Workspace code", "Timezone", "Default language"]],
  ["branding", "Branding", "Company logo, colours and customer-facing identity.", ["Logo", "Primary colour", "Secondary colour", "Export branding"]],
  ["ai", "AI Behavior", "AI response timing and human takeover workflow.", []],
  ["knowledge", "AI Teaching & Knowledge", "Instructions, files, FAQs and answer safety.", ["System instructions", "Knowledge sources", "Fallback behavior", "Answer confidence"]],
  ["flow", "Reply Flow & Saved Replies", "Order of welcome, language, intent, knowledge, escalation — plus reusable replies employees can insert from any conversation.", []],
  ["departments", "Departments", "Departments, routing and escalation paths.", ["Department list", "Default queue", "Escalation owner", "Assignment rules"]],
  ["channels", "Channels", "Messenger, WhatsApp, Instagram, Telegram, email and website.", ["Connected accounts", "Connection status", "Permissions", "Branch mapping"]],
  ["api", "API & Webhooks", "Callbacks, access keys and integration health.", ["Webhook URL", "Verify token", "API access", "Delivery logs"]],
  ["security", "Security", "Tenant isolation, sessions and audit controls.", ["Encryption status", "Session policy", "Audit retention", "IP restrictions"]],
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
  const [modules, setModules] = useState(null);
  const [plans, setPlans] = useState([]);
  const [myRequests, setMyRequests] = useState([]);
  const [error, setError] = useState("");
  const [requesting, setRequesting] = useState(null);
  const [notes, setNotes] = useState({});

  function load() {
    Promise.all([
      getMySubscriptionRequest(),
      getMyModulesRequest(),
      getPlansCatalogRequest(),
      getMySubscriptionRequestsRequest(),
    ])
      .then(([sub, mods, catalog, requests]) => {
        setData(sub);
        setModules(mods);
        setPlans(catalog.plans || []);
        setMyRequests(requests.requests || []);
      })
      .catch((e) => setError(e.message || "Could not load subscription."));
  }

  useEffect(() => { load(); }, []);

  async function submitRequest(planId) {
    setRequesting(planId);
    setError("");
    try {
      await requestPlanChangeRequest(planId, notes[planId] || "");
      load();
    } catch (e) {
      setError(e.message);
    } finally {
      setRequesting(null);
    }
  }

  if (error) return <p style={{ color: "#c0392b" }}>{error}</p>;
  if (!data || !modules) return <p>Loading…</p>;

  const pendingPlanIds = new Set(myRequests.filter((r) => r.status === "pending").map((r) => r.plan_id));

  return (
    <div className="company-setting-fields">
      {/* 1. Current plan, front and center */}
      {data.has_subscription ? (
        <div className="workflow-settings-card">
          <div className="workflow-setting-row" style={{ borderBottom: "none" }}>
            <div>
              <strong style={{ fontSize: 16 }}>{data.plan_name}</strong>
              <br />
              <span style={{ color: "#6b7280" }}>
                {data.subscription_status}
                {data.expires_at ? ` · renews/expires ${data.expires_at.slice(0, 10)}` : ""}
                {" · $"}{data.price_monthly}/mo
              </span>
            </div>
          </div>
          <div style={{ display: "grid", gridTemplateColumns: "repeat(auto-fit, minmax(140px, 1fr))", gap: 12, padding: "8px 0" }}>
            <div><strong>{data.users.used} / {data.users.max}</strong><br /><span style={{ fontSize: 12, color: "#6b7280" }}>Users</span></div>
            <div><strong>{data.channels.used} / {data.channels.max}</strong><br /><span style={{ fontSize: 12, color: "#6b7280" }}>Channels</span></div>
            <div><strong>{data.max_ai_messages}</strong><br /><span style={{ fontSize: 12, color: "#6b7280" }}>AI msgs/mo limit</span></div>
          </div>
        </div>
      ) : (
        <p>No active subscription yet — request a plan below.</p>
      )}

      {/* 2. Modules — compact, read-only */}
      <div className="workflow-settings-card">
        <div className="workflow-setting-row" style={{ borderBottom: "none" }}>
          <div>
            <strong>Modules enabled for your company</strong>
            <br />
            <span style={{ color: "#6b7280" }}>Set by your platform administrator. To let an employee use one, grant the matching permission from Roles &amp; Permissions.</span>
          </div>
        </div>
        <div style={{ display: "flex", flexWrap: "wrap", gap: 8, padding: "4px 0 8px" }}>
          {Object.entries(MODULE_LABELS).map(([key, label]) => (
            <span
              key={key}
              style={{
                fontSize: 12, padding: "4px 10px", borderRadius: 999,
                background: modules[key] ? "#e6f4ea" : "#f3f4f6",
                color: modules[key] ? "#1e7e34" : "#9ca3af",
              }}
            >
              {modules[key] ? "✓" : "—"} {label}
            </span>
          ))}
        </div>
      </div>

      {/* 3. Compare & request — cards, not a cramped table */}
      <div className="workflow-setting-row" style={{ borderBottom: "none", marginTop: 8 }}>
        <div>
          <strong>Compare plans</strong>
          <br />
          <span style={{ color: "#6b7280" }}>Requesting a plan sends it to your platform administrator for approval — nothing changes until they approve it.</span>
        </div>
      </div>

      <div style={{ display: "grid", gridTemplateColumns: "repeat(auto-fit, minmax(220px, 1fr))", gap: 14 }}>
        {plans.map((plan) => {
          const isCurrent = data.has_subscription && plan.code === data.plan_code;
          const isPending = pendingPlanIds.has(plan.id);
          const features = [
            plan.voice_ai_enabled && "Voice AI",
            plan.image_ai_enabled && "Image AI",
            plan.accounting_connector_enabled && "Accounting",
            plan.product_connector_enabled && "Products",
          ].filter(Boolean);

          return (
            <div key={plan.id} className="workflow-settings-card" style={isCurrent ? { borderColor: "#4f7fff", borderWidth: 2 } : undefined}>
              <div style={{ padding: "4px 0" }}>
                <strong style={{ fontSize: 15 }}>{plan.name}</strong>
                <div style={{ fontSize: 20, fontWeight: 700, margin: "4px 0" }}>${plan.price_monthly}<span style={{ fontSize: 13, fontWeight: 400, color: "#6b7280" }}>/mo</span></div>
                <ul style={{ listStyle: "none", padding: 0, margin: "8px 0", fontSize: 13, color: "#374151", lineHeight: 1.8 }}>
                  <li>{plan.max_users} users</li>
                  <li>{plan.max_channel_accounts} channels</li>
                  <li>{plan.max_ai_messages} AI messages/mo</li>
                  <li>{features.length ? features.join(", ") : "No extra features"}</li>
                </ul>
                {isCurrent ? (
                  <span style={{ fontSize: 13, fontWeight: 600, color: "#4f7fff" }}>Current plan</span>
                ) : isPending ? (
                  <span style={{ fontSize: 13, fontWeight: 600, color: "#b8860b" }}>Requested — pending review</span>
                ) : (
                  <div style={{ display: "flex", flexDirection: "column", gap: 8 }}>
                    <input
                      type="text"
                      placeholder="Note (optional) — e.g. Whish reference #"
                      value={notes[plan.id] || ""}
                      onChange={(e) => setNotes({ ...notes, [plan.id]: e.target.value })}
                      style={{ padding: "7px 9px", borderRadius: 8, border: "1px solid #d5dae5", fontSize: 12 }}
                    />
                    <button type="button" disabled={requesting === plan.id} onClick={() => submitRequest(plan.id)}>
                      {requesting === plan.id ? "Requesting…" : "Request this plan"}
                    </button>
                  </div>
                )}
              </div>
            </div>
          );
        })}
      </div>
    </div>
  );
}


function ProfileSettings() {
  const [values, setValues] = useState({
    company_name: "", workspace_code: "", timezone: "Asia/Beirut", default_language: "ar",
    business_hours: {
      sunday: { open: true, from: "09:00", to: "18:00" },
      monday: { open: true, from: "09:00", to: "18:00" },
      tuesday: { open: true, from: "09:00", to: "18:00" },
      wednesday: { open: true, from: "09:00", to: "18:00" },
      thursday: { open: true, from: "09:00", to: "18:00" },
      friday: { open: false, from: "09:00", to: "18:00" },
      saturday: { open: false, from: "09:00", to: "18:00" },
    },
  });
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
      <div className="workflow-setting-row" style={{ flexDirection: "column", alignItems: "stretch", gap: 10 }}>
        <strong>Business hours</strong>
        {["sunday", "monday", "tuesday", "wednesday", "thursday", "friday", "saturday"].map((day) => {
          const dayValue = values.business_hours?.[day] || { open: false, from: "09:00", to: "18:00" };
          return (
            <div key={day} style={{ display: "flex", alignItems: "center", gap: 12 }}>
              <label style={{ display: "flex", alignItems: "center", gap: 6, width: 110, textTransform: "capitalize" }}>
                <input
                  type="checkbox"
                  checked={dayValue.open}
                  disabled={locked.includes("business_hours")}
                  onChange={(e) => setValues({
                    ...values,
                    business_hours: { ...values.business_hours, [day]: { ...dayValue, open: e.target.checked } },
                  })}
                />
                {day}
              </label>
              <input
                type="time"
                value={dayValue.from}
                disabled={locked.includes("business_hours") || !dayValue.open}
                onChange={(e) => setValues({
                  ...values,
                  business_hours: { ...values.business_hours, [day]: { ...dayValue, from: e.target.value } },
                })}
              />
              <span>to</span>
              <input
                type="time"
                value={dayValue.to}
                disabled={locked.includes("business_hours") || !dayValue.open}
                onChange={(e) => setValues({
                  ...values,
                  business_hours: { ...values.business_hours, [day]: { ...dayValue, to: e.target.value } },
                })}
              />
              {!dayValue.open ? <span style={{ color: "#9ca3af", fontSize: 12 }}>Closed</span> : null}
            </div>
          );
        })}
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

function SavedRepliesManager() {
  const [replies, setReplies] = useState([]);
  const [error, setError] = useState("");
  const [saving, setSaving] = useState(false);
  const [editingId, setEditingId] = useState(null);
  const [title, setTitle] = useState("");
  const [body, setBody] = useState("");

  function load() {
    listSavedRepliesRequest()
      .then((result) => setReplies(result?.replies || []))
      .catch((e) => setError(e.message || "Could not load saved replies."));
  }

  useEffect(() => { load(); }, []);

  function startEdit(reply) {
    setEditingId(reply.id);
    setTitle(reply.title);
    setBody(reply.body);
  }

  function startNew() {
    setEditingId("new");
    setTitle("");
    setBody("");
  }

  async function save(e) {
    e.preventDefault();
    setSaving(true);
    setError("");
    try {
      if (editingId === "new") {
        await createSavedReplyRequest(title, body);
      } else {
        await updateSavedReplyRequest(editingId, title, body);
      }
      setEditingId(null);
      load();
    } catch (x) {
      setError(x.message);
    } finally {
      setSaving(false);
    }
  }

  async function remove(id) {
    setError("");
    try {
      await deleteSavedReplyRequest(id);
      load();
    } catch (x) {
      setError(x.message);
    }
  }

  return (
    <div className="workflow-settings-card" style={{ marginTop: 20 }}>
      <div className="workflow-setting-row" style={{ borderBottom: "none" }}>
        <div>
          <strong>Saved replies</strong>
          <br />
          <span style={{ fontWeight: 400, color: "#6b7280" }}>Employees insert these from inside any conversation — they never send automatically.</span>
        </div>
        {editingId ? null : <button type="button" onClick={startNew}>+ New saved reply</button>}
      </div>

      {error ? <p style={{ color: "#c0392b" }}>{error}</p> : null}

      {editingId ? (
        <form onSubmit={save} style={{ display: "flex", flexDirection: "column", gap: 10, padding: "12px 0" }}>
          <input value={title} onChange={(e) => setTitle(e.target.value)} placeholder="Title (e.g. Greeting)" required
            style={{ padding: "8px 10px", borderRadius: 8, border: "1px solid #d5dae5" }} />
          <textarea value={body} onChange={(e) => setBody(e.target.value)} placeholder="Message text" required rows={3}
            style={{ padding: "8px 10px", borderRadius: 8, border: "1px solid #d5dae5" }} />
          <div style={{ display: "flex", gap: 10 }}>
            <button type="submit" disabled={saving}>{saving ? "Saving..." : "Save"}</button>
            <button type="button" onClick={() => setEditingId(null)}>Cancel</button>
          </div>
        </form>
      ) : null}

      {replies.map((reply) => (
        <div className="workflow-setting-row" key={reply.id}>
          <div><strong>{reply.title}</strong><br /><span style={{ fontWeight: 400, color: "#6b7280" }}>{reply.body}</span></div>
          <div style={{ display: "flex", gap: 8 }}>
            <button type="button" onClick={() => startEdit(reply)}>Edit</button>
            <button type="button" onClick={() => remove(reply.id)}>Delete</button>
          </div>
        </div>
      ))}
      {!replies.length && !editingId ? <p style={{ padding: "12px 0", color: "#6b7280" }}>No saved replies yet.</p> : null}
    </div>
  );
}

function ReplyFlowAndSavedReplies() {
  return (
    <div>
      <ReplyFlowSettings />
      <SavedRepliesManager />
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
  return <section className="company-settings-shell company-settings-locked-layout"><aside className="company-settings-nav"><button className="company-settings-back" type="button" onClick={() => navigate("/dashboard")}><ArrowBackOutlined /> Back to platform</button><div className="company-settings-nav-heading"><span>COMPANY CONTROL</span><h1>Company Settings</h1></div><label className="settings-search"><SearchOutlined /><input value={query} onChange={(e) => setQuery(e.target.value)} placeholder="Search company settings..." /></label><nav className="company-settings-nav-scroll">{visible.map(([id,title]) => <button type="button" key={id} className={active===id?"is-active":""} onClick={()=>setActive(id)}>{title}</button>)}</nav></aside><main className="company-settings-content"><div className="company-settings-content-scroll"><header><span>COMPANY CONTROL</span><h2>{selected[1]}</h2><p>{selected[2]}</p></header>{active === "ai" ? <WorkflowSettings /> : active === "channels" ? <SecureChannelsPanel /> : active === "subscription" ? <SubscriptionView /> : active === "profile" ? <ProfileSettings /> : active === "flow" ? <ReplyFlowAndSavedReplies /> : active === "security" ? <SecurityStatusView /> : <><div className="company-setting-fields">{selected[3].map((field,index)=><article className="company-setting-field" key={field}><div><strong>{field}</strong><span>{index===0?"Configured from this company workspace.":"Ready for company-wide configuration."}</span></div><button type="button">Configure</button></article>)}</div><div className="settings-card-grid"><article className="settings-card"><h3>Company-wide setting</h3><p>Changes in this section apply to authorized users across the company.</p></article><article className="settings-card"><h3>Super Admin policy</h3><p>Availability, labels and locked defaults can be controlled by the separate Super Admin control plane.</p></article></div></>}</div></main></section>;
}
