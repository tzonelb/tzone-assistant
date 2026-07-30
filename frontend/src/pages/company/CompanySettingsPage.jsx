import { useEffect, useMemo, useState } from "react";
import { ArrowBackOutlined, SearchOutlined } from "@mui/icons-material";
import { useNavigate } from "react-router-dom";
import { getCompanySettingSectionRequest, updateCompanySettingSectionRequest, getMySubscriptionRequest, getMyModulesRequest, getPlansCatalogRequest, requestPlanChangeRequest, getMySubscriptionRequestsRequest, listSavedRepliesRequest, createSavedReplyRequest, updateSavedReplyRequest, deleteSavedReplyRequest, listKnowledgeEntriesRequest, createKnowledgeEntryRequest, updateKnowledgeEntryRequest, deleteKnowledgeEntryRequest, listDepartmentsRequest, createDepartmentRequest, deleteDepartmentRequest, listInstructionsRequest, createInstructionRequest, updateInstructionRequest, deleteInstructionRequest, reorderInstructionsRequest } from "../../api/client";
import SecureChannelsPanel from "./SecureChannelsPanel";

const SECTIONS = [
  ["profile", "Company Profile", "Identity, contact information, branches, timezone, business details and branding.", ["Company name", "Workspace code", "Timezone", "Default language", "Logo"]],
  ["departments", "Departments", "Your own departments — set these up first, before AI Behavior, since routing and knowledge scoping depend on them.", []],
  ["ai", "AI Behavior", "AI response timing and human takeover workflow.", []],
  ["knowledge", "AI Teaching & Knowledge", "Instructions, files, FAQs and answer safety.", ["System instructions", "Knowledge sources", "Fallback behavior", "Answer confidence"]],
  ["flow", "Reply Flow & Saved Replies", "Order of welcome, language, intent, knowledge, escalation — plus reusable replies employees can insert from any conversation.", []],
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
    company_name: "", workspace_code: "", timezone: "Asia/Beirut", default_language: "ar", logo_url: "",
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
        <div>
          <strong>Logo</strong>
          <br />
          <span style={{ fontWeight: 400, color: "#6b7280" }}>Paste an image URL — shown in the sidebar and on customer-facing pages.</span>
        </div>
        <div style={{ display: "flex", alignItems: "center", gap: 10 }}>
          {values.logo_url ? (
            <img src={values.logo_url} alt="Logo preview" style={{ width: 36, height: 36, borderRadius: 8, objectFit: "cover", border: "1px solid #e5e7eb" }} />
          ) : null}
          <input value={values.logo_url} disabled={locked.includes("logo_url")} onChange={(e) => setValues({ ...values, logo_url: e.target.value })} placeholder="https://..." />
        </div>
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

const CHANNEL_OPTIONS = ["messenger", "whatsapp", "instagram", "telegram", "website"];

function TagPicker({ departments, selectedDepartments, setSelectedDepartments, selectedChannels, setSelectedChannels, extraTagsInput, setExtraTagsInput }) {
  function toggle(list, setList, value) {
    setList(list.includes(value) ? list.filter((v) => v !== value) : [...list, value]);
  }
  return (
    <>
      <div>
        <span style={{ fontSize: 12, fontWeight: 600, color: "#374151" }}>Visible to departments (optional — leave all unchecked for every department)</span>
        <div style={{ display: "flex", flexWrap: "wrap", gap: 6, marginTop: 6 }}>
          {departments.filter((d) => d !== "Unassigned").map((d) => {
            const value = d.toLowerCase();
            const active = selectedDepartments.includes(value);
            return (
              <button key={d} type="button" onClick={() => toggle(selectedDepartments, setSelectedDepartments, value)}
                style={{ fontSize: 12, padding: "5px 12px", borderRadius: 999, border: active ? "1px solid #4F63F0" : "1px solid #d5dae5", background: active ? "#EEF1FE" : "#fff", color: active ? "#4F63F0" : "#374151", cursor: "pointer" }}>
                {d}
              </button>
            );
          })}
        </div>
      </div>
      <div>
        <span style={{ fontSize: 12, fontWeight: 600, color: "#374151" }}>Visible to channels (optional — leave all unchecked for every channel)</span>
        <div style={{ display: "flex", flexWrap: "wrap", gap: 6, marginTop: 6 }}>
          {CHANNEL_OPTIONS.map((c) => {
            const active = selectedChannels.includes(c);
            return (
              <button key={c} type="button" onClick={() => toggle(selectedChannels, setSelectedChannels, c)}
                style={{ fontSize: 12, padding: "5px 12px", borderRadius: 999, border: active ? "1px solid #17A369" : "1px solid #d5dae5", background: active ? "#E7FAF1" : "#fff", color: active ? "#17A369" : "#374151", cursor: "pointer", textTransform: "capitalize" }}>
                {c}
              </button>
            );
          })}
        </div>
      </div>
      <input value={extraTagsInput} onChange={(e) => setExtraTagsInput(e.target.value)}
        placeholder="Other custom tags, comma-separated (optional) — e.g. vip, ramadan-campaign"
        style={{ padding: "8px 10px", borderRadius: 8, border: "1px solid #d5dae5", fontSize: 13 }} />
      <span style={{ fontSize: 12, color: "#9296AC" }}>Nothing checked and no custom tags = applies everywhere.</span>
    </>
  );
}

function splitTags(tags, departments) {
  const departmentNamesLower = departments.map((d) => d.toLowerCase());
  return {
    departmentTags: tags.filter((t) => departmentNamesLower.includes(t)),
    channelTags: tags.filter((t) => CHANNEL_OPTIONS.includes(t)),
    extraTags: tags.filter((t) => !departmentNamesLower.includes(t) && !CHANNEL_OPTIONS.includes(t)),
  };
}

function KnowledgeManager() {
  const [entries, setEntries] = useState([]);
  const [departments, setDepartments] = useState([]);
  const [error, setError] = useState("");
  const [saving, setSaving] = useState(false);
  const [editingId, setEditingId] = useState(null);
  const [title, setTitle] = useState("");
  const [content, setContent] = useState("");
  const [department, setDepartment] = useState("Unassigned");
  const [selectedDepartments, setSelectedDepartments] = useState([]);
  const [selectedChannels, setSelectedChannels] = useState([]);
  const [extraTagsInput, setExtraTagsInput] = useState("");
  const [filterDepartment, setFilterDepartment] = useState("all");

  function load() {
    listKnowledgeEntriesRequest()
      .then((result) => setEntries(result?.entries || []))
      .catch((e) => setError(e.message || "Could not load knowledge base."));
    listDepartmentsRequest()
      .then((result) => setDepartments(result?.departments || []))
      .catch(() => {});
  }

  useEffect(() => { load(); }, []);

  function startEdit(entry) {
    setEditingId(entry.id);
    setTitle(entry.title);
    setContent(entry.content);
    setDepartment(entry.department || "Unassigned");
    const { departmentTags, channelTags, extraTags } = splitTags(entry.tags || [], departments);
    setSelectedDepartments(departmentTags);
    setSelectedChannels(channelTags);
    setExtraTagsInput(extraTags.join(", "));
  }

  function startNew() {
    setEditingId("new");
    setTitle("");
    setContent("");
    setDepartment("Unassigned");
    setSelectedDepartments([]);
    setSelectedChannels([]);
    setExtraTagsInput("");
  }

  async function save(e) {
    e.preventDefault();
    setSaving(true);
    setError("");
    const extraTags = extraTagsInput.split(",").map((t) => t.trim()).filter(Boolean);
    const tags = [...selectedDepartments, ...selectedChannels, ...extraTags];
    try {
      if (editingId === "new") {
        await createKnowledgeEntryRequest(title, content, department, tags);
      } else {
        await updateKnowledgeEntryRequest(editingId, title, content, department, tags);
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
      await deleteKnowledgeEntryRequest(id);
      load();
    } catch (x) {
      setError(x.message);
    }
  }

  const visibleEntries = filterDepartment === "all" ? entries : entries.filter((e) => e.department === filterDepartment);

  return (
    <div className="workflow-settings-card">
      <div className="workflow-setting-row" style={{ borderBottom: "none" }}>
        <div>
          <strong>What your AI knows about your business</strong>
          <br />
          <span style={{ fontWeight: 400, color: "#6b7280" }}>
            Add questions and answers about your pricing, services, policies — anything customers ask.
            The AI uses these (not generic knowledge) when replying. Each company manages its own — this list is private to you.
          </span>
        </div>
        {editingId ? null : <button type="button" onClick={startNew}>+ New knowledge entry</button>}
      </div>

      {error ? <p style={{ color: "#c0392b" }}>{error}</p> : null}

      {editingId ? (
        <form onSubmit={save} style={{ display: "flex", flexDirection: "column", gap: 10, padding: "12px 0" }}>
          <input value={title} onChange={(e) => setTitle(e.target.value)} placeholder="Question or topic (e.g. What internet speed do I need?)" required
            style={{ padding: "8px 10px", borderRadius: 8, border: "1px solid #d5dae5" }} />
          <textarea value={content} onChange={(e) => setContent(e.target.value)} placeholder="Answer" required rows={4}
            style={{ padding: "8px 10px", borderRadius: 8, border: "1px solid #d5dae5" }} />
          <select value={department} onChange={(e) => setDepartment(e.target.value)}
            style={{ padding: "8px 10px", borderRadius: 8, border: "1px solid #d5dae5" }}>
            {departments.map((d) => <option key={d} value={d}>{d}</option>)}
          </select>
          <TagPicker
            departments={departments}
            selectedDepartments={selectedDepartments} setSelectedDepartments={setSelectedDepartments}
            selectedChannels={selectedChannels} setSelectedChannels={setSelectedChannels}
            extraTagsInput={extraTagsInput} setExtraTagsInput={setExtraTagsInput}
          />
          <div style={{ display: "flex", gap: 10 }}>
            <button type="submit" disabled={saving}>{saving ? "Saving..." : "Save"}</button>
            <button type="button" onClick={() => setEditingId(null)}>Cancel</button>
          </div>
        </form>
      ) : null}

      {!editingId ? (
        <div style={{ padding: "10px 0" }}>
          <select value={filterDepartment} onChange={(e) => setFilterDepartment(e.target.value)}
            style={{ padding: "6px 10px", borderRadius: 8, border: "1px solid #d5dae5", fontSize: 13 }}>
            <option value="all">All departments</option>
            {departments.map((d) => <option key={d} value={d}>{d}</option>)}
          </select>
        </div>
      ) : null}

      {visibleEntries.map((entry) => (
        <div className="workflow-setting-row" key={entry.id}>
          <div>
            <span style={{ fontSize: 11, fontWeight: 700, color: "#4F63F0", textTransform: "uppercase" }}>{entry.department}</span>
            {(entry.tags || []).map((tag) => (
              <span key={tag} style={{ fontSize: 10, fontWeight: 700, color: "#17A369", background: "#E7FAF1", borderRadius: 999, padding: "2px 8px", marginLeft: 6 }}>{tag}</span>
            ))}
            <br />
            <strong>{entry.title}</strong>
            <br />
            <span style={{ fontWeight: 400, color: "#6b7280" }}>{entry.content}</span>
          </div>
          <div style={{ display: "flex", gap: 8 }}>
            <button type="button" onClick={() => startEdit(entry)}>Edit</button>
            <button type="button" onClick={() => remove(entry.id)}>Delete</button>
          </div>
        </div>
      ))}
      {!visibleEntries.length && !editingId ? (
        <p style={{ padding: "12px 0", color: "#6b7280" }}>
          {entries.length ? "No entries in this department." : "No knowledge added yet — your AI is replying generically until you add some."}
        </p>
      ) : null}
    </div>
  );
}

function InstructionsManager() {
  const [instructions, setInstructions] = useState([]);
  const [departments, setDepartments] = useState([]);
  const [error, setError] = useState("");
  const [saving, setSaving] = useState(false);
  const [newText, setNewText] = useState("");
  const [newDepartments, setNewDepartments] = useState([]);
  const [newChannels, setNewChannels] = useState([]);
  const [newExtraTags, setNewExtraTags] = useState("");
  const [editingId, setEditingId] = useState(null);
  const [editingText, setEditingText] = useState("");
  const [editingDepartments, setEditingDepartments] = useState([]);
  const [editingChannels, setEditingChannels] = useState([]);
  const [editingExtraTags, setEditingExtraTags] = useState("");

  function load() {
    listInstructionsRequest()
      .then((result) => setInstructions(result?.instructions || []))
      .catch((e) => setError(e.message || "Could not load instructions."));
    listDepartmentsRequest()
      .then((result) => setDepartments(result?.departments || []))
      .catch(() => {});
  }

  useEffect(() => { load(); }, []);

  async function addInstruction(e) {
    e.preventDefault();
    const value = newText.trim();
    if (!value) return;
    setSaving(true);
    setError("");
    const extraTags = newExtraTags.split(",").map((t) => t.trim()).filter(Boolean);
    const tags = [...newDepartments, ...newChannels, ...extraTags];
    try {
      await createInstructionRequest(value, tags);
      setNewText("");
      setNewDepartments([]);
      setNewChannels([]);
      setNewExtraTags("");
      load();
    } catch (x) {
      setError(x.message);
    } finally {
      setSaving(false);
    }
  }

  function startEditing(instruction) {
    setEditingId(instruction.id);
    setEditingText(instruction.text);
    const { departmentTags, channelTags, extraTags } = splitTags(instruction.tags || [], departments);
    setEditingDepartments(departmentTags);
    setEditingChannels(channelTags);
    setEditingExtraTags(extraTags.join(", "));
  }

  async function saveEdit(id) {
    setSaving(true);
    setError("");
    const extraTags = editingExtraTags.split(",").map((t) => t.trim()).filter(Boolean);
    const tags = [...editingDepartments, ...editingChannels, ...extraTags];
    try {
      await updateInstructionRequest(id, editingText, tags);
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
      await deleteInstructionRequest(id);
      load();
    } catch (x) {
      setError(x.message);
    }
  }

  async function move(index, direction) {
    const nextIndex = index + direction;
    if (nextIndex < 0 || nextIndex >= instructions.length) return;
    const reordered = [...instructions];
    [reordered[index], reordered[nextIndex]] = [reordered[nextIndex], reordered[index]];
    setInstructions(reordered);
    try {
      await reorderInstructionsRequest(reordered.map((i) => i.id));
    } catch (x) {
      setError(x.message);
      load();
    }
  }

  return (
    <div className="workflow-settings-card" style={{ marginTop: 20 }}>
      <div className="workflow-setting-row" style={{ borderBottom: "none" }}>
        <div>
          <strong>Instructions — how your AI should behave</strong>
          <br />
          <span style={{ fontWeight: 400, color: "#6b7280" }}>
            Behavior rules, not facts — e.g. "Don't share prices", "Use emojis when appropriate", "Don't send follow-up messages".
            Earlier rules take priority when they conflict. This is separate from Knowledge above.
          </span>
        </div>
      </div>

      {error ? <p style={{ color: "#c0392b" }}>{error}</p> : null}

      <form onSubmit={addInstruction} style={{ display: "flex", flexDirection: "column", gap: 8, padding: "12px 0" }}>
        <input
          value={newText}
          onChange={(e) => setNewText(e.target.value)}
          placeholder="e.g. Don't share prices in the first message"
          style={{ padding: "8px 10px", borderRadius: 8, border: "1px solid #d5dae5" }}
        />
        <TagPicker
          departments={departments}
          selectedDepartments={newDepartments} setSelectedDepartments={setNewDepartments}
          selectedChannels={newChannels} setSelectedChannels={setNewChannels}
          extraTagsInput={newExtraTags} setExtraTagsInput={setNewExtraTags}
        />
        <button type="submit" disabled={saving || !newText.trim()} style={{ alignSelf: "flex-start" }}>{saving ? "Adding..." : "+ Add instruction"}</button>
      </form>

      {instructions.map((instruction, index) => (
        <div className="workflow-setting-row" key={instruction.id} style={{ alignItems: editingId === instruction.id ? "flex-start" : "center" }}>
          {editingId === instruction.id ? (
            <>
              <div style={{ flex: 1, display: "flex", flexDirection: "column", gap: 8 }}>
                <input
                  value={editingText}
                  onChange={(e) => setEditingText(e.target.value)}
                  style={{ padding: "6px 8px", borderRadius: 6, border: "1px solid #d5dae5" }}
                />
                <TagPicker
                  departments={departments}
                  selectedDepartments={editingDepartments} setSelectedDepartments={setEditingDepartments}
                  selectedChannels={editingChannels} setSelectedChannels={setEditingChannels}
                  extraTagsInput={editingExtraTags} setExtraTagsInput={setEditingExtraTags}
                />
                <div style={{ display: "flex", gap: 8 }}>
                  <button type="button" onClick={() => saveEdit(instruction.id)} disabled={saving}>Save</button>
                  <button type="button" onClick={() => setEditingId(null)}>Cancel</button>
                </div>
              </div>
            </>
          ) : (
            <>
              <div>
                <span style={{ color: "#9296AC", marginRight: 8 }}>{index + 1}.</span>{instruction.text}
                {(instruction.tags || []).map((tag) => (
                  <span key={tag} style={{ fontSize: 10, fontWeight: 700, color: "#17A369", background: "#E7FAF1", borderRadius: 999, padding: "2px 8px", marginLeft: 6 }}>{tag}</span>
                ))}
              </div>
              <div style={{ display: "flex", gap: 6 }}>
                <button type="button" onClick={() => move(index, -1)} disabled={index === 0}>↑</button>
                <button type="button" onClick={() => move(index, 1)} disabled={index === instructions.length - 1}>↓</button>
                <button type="button" onClick={() => startEditing(instruction)}>Edit</button>
                <button type="button" onClick={() => remove(instruction.id)}>Delete</button>
              </div>
            </>
          )}
        </div>
      ))}
      {!instructions.length ? <p style={{ padding: "12px 0", color: "#6b7280" }}>No instructions yet.</p> : null}
    </div>
  );
}


function KnowledgeAndInstructions() {
  return (
    <div>
      <KnowledgeManager />
      <InstructionsManager />
    </div>
  );
}

function DepartmentsManager() {
  const [departments, setDepartments] = useState([]);
  const [error, setError] = useState("");
  const [saving, setSaving] = useState(false);
  const [newName, setNewName] = useState("");

  function load() {
    listDepartmentsRequest()
      .then((result) => setDepartments(result?.departments || []))
      .catch((e) => setError(e.message || "Could not load departments."));
  }

  useEffect(() => { load(); }, []);

  async function addDepartment(e) {
    e.preventDefault();
    const value = newName.trim();
    if (!value) return;
    setSaving(true);
    setError("");
    try {
      const result = await createDepartmentRequest(value);
      setDepartments(result.departments || []);
      setNewName("");
    } catch (x) {
      setError(x.message);
    } finally {
      setSaving(false);
    }
  }

  async function remove(name) {
    setError("");
    try {
      const result = await deleteDepartmentRequest(name);
      setDepartments(result.departments || []);
    } catch (x) {
      setError(x.message);
    }
  }

  return (
    <div className="workflow-settings-card">
      <div className="workflow-setting-row" style={{ borderBottom: "none" }}>
        <div>
          <strong>Your company's departments</strong>
          <br />
          <span style={{ fontWeight: 400, color: "#6b7280" }}>
            Set these up first — used for routing conversations, and can scope AI Knowledge and Reply Flow steps to a specific
            department. Every company defines its own list; nothing is preset for you.
          </span>
        </div>
      </div>

      {error ? <p style={{ color: "#c0392b" }}>{error}</p> : null}

      <form onSubmit={addDepartment} style={{ display: "flex", gap: 10, padding: "12px 0" }}>
        <input
          value={newName}
          onChange={(e) => setNewName(e.target.value)}
          placeholder="e.g. Sales, Technical Support, Billing"
          style={{ flex: 1, padding: "8px 10px", borderRadius: 8, border: "1px solid #d5dae5" }}
        />
        <button type="submit" disabled={saving || !newName.trim()}>{saving ? "Adding..." : "+ Add department"}</button>
      </form>

      {departments.map((name) => (
        <div className="workflow-setting-row" key={name}>
          <div><strong>{name}</strong>{name === "Unassigned" ? <span style={{ marginLeft: 8, fontSize: 11, color: "#9296AC" }}>(always available)</span> : null}</div>
          {name !== "Unassigned" ? (
            <button type="button" onClick={() => remove(name)}>Delete</button>
          ) : null}
        </div>
      ))}
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
  return <section className="company-settings-shell company-settings-locked-layout"><aside className="company-settings-nav"><button className="company-settings-back" type="button" onClick={() => navigate("/dashboard")}><ArrowBackOutlined /> Back to platform</button><div className="company-settings-nav-heading"><span>COMPANY CONTROL</span><h1>Company Settings</h1></div><label className="settings-search"><SearchOutlined /><input value={query} onChange={(e) => setQuery(e.target.value)} placeholder="Search company settings..." /></label><nav className="company-settings-nav-scroll">{visible.map(([id,title]) => <button type="button" key={id} className={active===id?"is-active":""} onClick={()=>setActive(id)}>{title}</button>)}</nav></aside><main className="company-settings-content"><div className="company-settings-content-scroll"><header><span>COMPANY CONTROL</span><h2>{selected[1]}</h2><p>{selected[2]}</p></header>{active === "ai" ? <WorkflowSettings /> : active === "channels" ? <SecureChannelsPanel /> : active === "subscription" ? <SubscriptionView /> : active === "profile" ? <ProfileSettings /> : active === "flow" ? <ReplyFlowAndSavedReplies /> : active === "security" ? <SecurityStatusView /> : active === "knowledge" ? <KnowledgeAndInstructions /> : active === "departments" ? <DepartmentsManager /> : <><div className="company-setting-fields">{selected[3].map((field,index)=><article className="company-setting-field" key={field}><div><strong>{field}</strong><span>{index===0?"Configured from this company workspace.":"Ready for company-wide configuration."}</span></div><button type="button">Configure</button></article>)}</div><div className="settings-card-grid"><article className="settings-card"><h3>Company-wide setting</h3><p>Changes in this section apply to authorized users across the company.</p></article><article className="settings-card"><h3>Super Admin policy</h3><p>Availability, labels and locked defaults can be controlled by the separate Super Admin control plane.</p></article></div></>}</div></main></section>;
}
