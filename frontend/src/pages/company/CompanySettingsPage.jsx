import { useEffect, useMemo, useState } from "react";
import { ArrowBackOutlined, SearchOutlined } from "@mui/icons-material";
import { useNavigate, useSearchParams } from "react-router-dom";
import { getCompanySettingSectionRequest, updateCompanySettingSectionRequest, getMySubscriptionRequest, getMyModulesRequest, getPlansCatalogRequest, requestPlanChangeRequest, getMySubscriptionRequestsRequest, listDepartmentsRequest, createDepartmentRequest, deleteDepartmentRequest, listSupportTicketsRequest, createSupportTicketRequest, getCurrentUserRequest } from "../../api/client";
import SecureChannelsPanel from "./SecureChannelsPanel";

const SECTIONS = [
  ["profile", "Company Profile", "Identity, contact information, branches, timezone, business details and branding.", ["Company name", "Workspace code", "Timezone", "Default language", "Logo"]],
  ["departments", "Departments", "Your own departments — set these up first, before Chatbot Control, since routing and knowledge scoping depend on them.", []],
  ["ai", "Chatbot Control", "One place for all bot behaviour — greeting, who replies first, human takeover workflow and saved replies.", []],
  ["flow", "Reply Flow & Saved Replies", "Design the real step-by-step conversation flow per channel and department, plus reusable replies employees can insert from any conversation. Admins only.", [], "users.manage"],
  ["roles", "Roles & Permissions", "Manage employee roles and exactly what each one is allowed to do. Admins only.", [], "users.manage"],
  ["channels", "Channels", "Messenger, WhatsApp, Instagram, Telegram, email and website.", ["Connected accounts", "Connection status", "Permissions", "Branch mapping"]],
  ["api", "API & Webhooks", "Not built yet — each connected channel already has its own real webhook wired up automatically; a general API-key/webhook-management screen isn't available.", []],
  ["security", "Security", "How channel credential access is protected — verification, encryption at rest, and the session change log.", []],
  ["backup", "Backup", "Not built yet — there is no self-service backup/restore control in T-ZONE. Contact support if you need a restore.", []],
  ["billing", "Billing", "Your plan, usage limits, billing history, and plan-change or renewal requests.", ["Current plan", "Users limit", "AI usage", "Renewal date"]],
  ["help", "Help", "Frequently asked questions about running your workspace on T-ZONE.", []],
  ["ticketing", "Ticketing", "Open a support or maintenance ticket to the T-ZONE team about platform issues.", []],
];

function WorkflowSettings() {
  const [values, setValues] = useState({ mode: "ai_first", greeting_message: "", reply_access_mode: "take_required", return_to_ai_timeout_minutes: 5, auto_release_to_ai: true, voice_reply_enabled: false });
  const [locked, setLocked] = useState([]);
  const [status, setStatus] = useState("");
  const [saving, setSaving] = useState(false);
  const [voiceAiOnPlan, setVoiceAiOnPlan] = useState(true);

  useEffect(() => {
    getCompanySettingSectionRequest("ai_behavior")
      .then((result) => { setValues((current) => ({ ...current, ...(result?.values || {}) })); setLocked(result?.locked_keys || []); })
      .catch((error) => setStatus(error.message || "Settings could not load."));
    getMySubscriptionRequest()
      .then((result) => setVoiceAiOnPlan(Boolean(result?.features?.voice_ai)))
      .catch(() => {});
  }, []);

  async function save() {
    setSaving(true); setStatus("");
    try {
      const result = await updateCompanySettingSectionRequest("ai_behavior", {
        mode: values.mode,
        greeting_message: values.greeting_message || "",
        reply_access_mode: values.reply_access_mode,
        return_to_ai_timeout_minutes: Math.max(1, Number(values.return_to_ai_timeout_minutes) || 5),
        auto_release_to_ai: Boolean(values.auto_release_to_ai),
        voice_reply_enabled: Boolean(values.voice_reply_enabled),
      });
      setValues((current) => ({ ...current, ...(result?.values || {}) }));
      setStatus("Chatbot control saved.");
    } catch (error) { setStatus(error.message || "Settings could not save."); }
    finally { setSaving(false); }
  }

  return <div className="workflow-settings-card">
    <div className="workflow-setting-row" style={{ flexDirection: "column", alignItems: "stretch", gap: 8 }}><div><strong>Greeting message</strong><span>Sent when a new conversation starts. Leave blank to use the built-in default welcome.</span></div><textarea rows={2} value={values.greeting_message || ""} disabled={locked.includes("greeting_message")} onChange={(e) => setValues({ ...values, greeting_message: e.target.value })} placeholder="Welcome! How can we help you today?" style={{ padding: "8px 10px", borderRadius: 8, border: "1px solid #d5dae5" }} /></div>
    <div className="workflow-setting-row"><div><strong>Who replies first?</strong><span>AI first lets the bot answer immediately; human first holds new chats for an employee to open them.</span></div><select value={values.mode} disabled={locked.includes("mode")} onChange={(e) => setValues({ ...values, mode: e.target.value })}><option value="ai_first">AI replies first</option><option value="human_first">Human replies first</option></select></div>
    <div className="workflow-setting-row"><div><strong>Who may reply?</strong><span>Exclusive takeover is safest. Shared mode lets the first employee reply claim an unassigned human chat atomically.</span></div><select value={values.reply_access_mode} disabled={locked.includes("reply_access_mode")} onChange={(e) => setValues({ ...values, reply_access_mode: e.target.value })}><option value="take_required">Take conversation required</option><option value="shared_until_taken">Anyone until first reply</option></select></div>
    <div className="workflow-setting-row"><div><strong>Return to AI timeout</strong><span>After the employee's last reply, ownership is released and AI resumes automatically.</span></div><div className="timeout-input"><input type="number" min="1" max="1440" value={values.return_to_ai_timeout_minutes} disabled={locked.includes("return_to_ai_timeout_minutes")} onChange={(e) => setValues({ ...values, return_to_ai_timeout_minutes: e.target.value })}/><span>minutes</span></div></div>
    <label className="workflow-toggle"><input type="checkbox" checked={Boolean(values.auto_release_to_ai)} disabled={locked.includes("auto_release_to_ai")} onChange={(e) => setValues({ ...values, auto_release_to_ai: e.target.checked })}/><div><strong>Auto-release ownership and return to AI</strong><span>Clears the assigned employee when the timeout expires, completing the full cycle.</span></div></label>
    <label className="workflow-toggle"><input type="checkbox" checked={Boolean(values.voice_reply_enabled)} disabled={locked.includes("voice_reply_enabled") || !voiceAiOnPlan} onChange={(e) => setValues({ ...values, voice_reply_enabled: e.target.checked })}/><div><strong>Reply with voice</strong><span>{voiceAiOnPlan ? "The AI sends a real voice note instead of text whenever the reply doesn't need buttons." : "Not included on your current plan — upgrade under Billing to enable."}</span></div></label>
    <div className="workflow-setting-row" style={{ borderBottom: "none" }}><div><strong>Saved reply behaviour</strong><span>Saved replies are manual snippets employees insert inside a conversation — they never send automatically. Manage them under the "Reply Flow &amp; Saved Replies" section.</span></div></div>
    <div className="workflow-settings-footer"><span>{status}</span><button type="button" onClick={save} disabled={saving}>{saving ? "Saving..." : "Save chatbot control"}</button></div>
  </div>;
}

const REQUEST_STATUS_COLORS = { pending: "#b8860b", approved: "#1e7e34", rejected: "#c0392b" };

function BillingView() {
  const [data, setData] = useState(null);
  const [modules, setModules] = useState(null);
  const [plans, setPlans] = useState([]);
  const [myRequests, setMyRequests] = useState([]);
  const [error, setError] = useState("");
  const [requesting, setRequesting] = useState(null);
  const [renewing, setRenewing] = useState(false);
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

  async function renewCurrentPlan(currentPlanId) {
    if (!currentPlanId) return;
    setRenewing(true);
    setError("");
    try {
      await requestPlanChangeRequest(currentPlanId, "Renewal request");
      load();
    } catch (e) {
      setError(e.message);
    } finally {
      setRenewing(false);
    }
  }

  if (error) return <p style={{ color: "#c0392b" }}>{error}</p>;
  if (!data || !modules) return <p>Loading…</p>;

  const pendingPlanIds = new Set(myRequests.filter((r) => r.status === "pending").map((r) => r.plan_id));
  // Uses data.plan_id straight from the subscription itself, not a match
  // against `plans` (which the backend restricts to active plans only) -
  // otherwise a company whose current plan was later retired could never
  // renew again: currentPlan would never be found, so the button stayed
  // disabled with no explanation.
  const currentPlanId = data.has_subscription ? data.plan_id || null : null;
  const renewalPending = currentPlanId != null && pendingPlanIds.has(currentPlanId);

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
          <div className="workflow-setting-row" style={{ borderBottom: "none", alignItems: "center" }}>
            <div>
              <strong>Renew this plan</strong>
              <br />
              <span style={{ color: "#6b7280", fontSize: 13 }}>
                Online payment isn't wired up yet — renewing sends a renewal request to the T-ZONE team, who confirm it and extend your expiry. No card is charged here.
              </span>
            </div>
            {renewalPending ? (
              <span style={{ fontSize: 13, fontWeight: 600, color: "#b8860b", whiteSpace: "nowrap" }}>Renewal requested — pending</span>
            ) : (
              <button type="button" disabled={renewing || !currentPlanId} onClick={() => renewCurrentPlan(currentPlanId)}>
                {renewing ? "Requesting…" : "Renew plan"}
              </button>
            )}
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

      {/* 4. Billing history — real plan-change / renewal request records (no fake invoices) */}
      <div className="workflow-setting-row" style={{ borderBottom: "none", marginTop: 8 }}>
        <div>
          <strong>Billing history</strong>
          <br />
          <span style={{ color: "#6b7280" }}>
            No online invoices yet — payment isn't wired up. This is the real log of the plan-change and renewal requests your company has submitted.
          </span>
        </div>
      </div>
      <div className="workflow-settings-card">
        {myRequests.length ? myRequests.map((req) => (
          <div className="workflow-setting-row" key={req.id}>
            <div>
              <strong>{req.plan_name || `Plan #${req.plan_id}`}</strong>
              <br />
              <span style={{ color: "#6b7280", fontSize: 13 }}>
                {req.created_at ? req.created_at.slice(0, 10) : ""}
                {req.note ? ` · ${req.note}` : ""}
              </span>
            </div>
            <span style={{ fontSize: 13, fontWeight: 600, color: REQUEST_STATUS_COLORS[req.status] || "#6b7280", textTransform: "capitalize" }}>
              {req.status}
            </span>
          </div>
        )) : <p style={{ padding: "12px 0", color: "#6b7280" }}>No billing history yet.</p>}
      </div>
    </div>
  );
}

const FAQ_ITEMS = [
  {
    q: "How do I connect a messaging channel?",
    a: "Open Company Settings → Channels. Pick the channel (Messenger, WhatsApp, Instagram, Telegram, email or website), then follow the connect flow. Connecting or disconnecting a channel first asks for a 6-digit email verification code, valid for 20 minutes, so credentials stay protected.",
  },
  {
    q: "How does AI-to-human handoff work?",
    a: "Under Chatbot Control you choose who replies first. In AI-first mode the bot answers immediately and an employee can take over a conversation at any time; in human-first mode new chats wait for an employee to open them. After the employee's last reply, ownership is released and the AI resumes automatically once the return-to-AI timeout passes.",
  },
  {
    q: "How do I add employees and control what they can do?",
    a: "Invite people from the Employees area, then assign each a role in Roles & Permissions. A module being enabled on your plan only makes it available — an employee also needs the matching permission granted to their role before they can use it.",
  },
  {
    q: "How does billing work?",
    a: "Your current plan, usage limits and history live under the Billing section here. Online card payment isn't wired up yet, so upgrading, changing or renewing a plan submits a request to the T-ZONE team, who review it and apply the change. Nothing is charged automatically and no plan changes until staff approve the request.",
  },
  {
    q: "How do I reach T-ZONE support?",
    a: "Open the Ticketing section here and create a support ticket with a subject, description and priority. The T-ZONE team picks it up from there. Use this for platform issues — for your own customers' questions, use the conversations screen.",
  },
  {
    q: "Is my company's data isolated from other companies?",
    a: "Yes. Every record is scoped to your company (tenant), and channel access tokens are encrypted at rest. The Security section summarizes credential access, storage and the session change log.",
  },
];

function HelpView() {
  const [openIndex, setOpenIndex] = useState(0);
  return (
    <div className="company-setting-fields">
      <div className="workflow-settings-card">
        {FAQ_ITEMS.map((item, index) => {
          const isOpen = openIndex === index;
          return (
            <div className="workflow-setting-row" key={item.q} style={{ flexDirection: "column", alignItems: "stretch", gap: 8, cursor: "pointer" }} onClick={() => setOpenIndex(isOpen ? -1 : index)}>
              <div style={{ display: "flex", justifyContent: "space-between", alignItems: "center", gap: 12 }}>
                <strong>{item.q}</strong>
                <span style={{ color: "#6b7280", fontSize: 18, lineHeight: 1 }}>{isOpen ? "−" : "+"}</span>
              </div>
              {isOpen ? <span style={{ color: "#374151", fontSize: 14, lineHeight: 1.6 }}>{item.a}</span> : null}
            </div>
          );
        })}
      </div>
    </div>
  );
}

const TICKET_PRIORITIES = ["low", "normal", "high", "urgent"];
const TICKET_STATUS_COLORS = { open: "#1e7e34", in_progress: "#b8860b", closed: "#6b7280", resolved: "#4f7fff" };

function TicketingView() {
  const [tickets, setTickets] = useState([]);
  const [error, setError] = useState("");
  const [creating, setCreating] = useState(false);
  const [showForm, setShowForm] = useState(false);
  const [subject, setSubject] = useState("");
  const [description, setDescription] = useState("");
  const [priority, setPriority] = useState("normal");

  function load() {
    listSupportTicketsRequest()
      .then((result) => setTickets(result?.tickets || []))
      .catch((e) => setError(e.message || "Could not load tickets."));
  }

  useEffect(() => { load(); }, []);

  async function submit(e) {
    e.preventDefault();
    setCreating(true);
    setError("");
    try {
      await createSupportTicketRequest(subject, description, priority);
      setSubject(""); setDescription(""); setPriority("normal"); setShowForm(false);
      load();
    } catch (x) {
      setError(x.message);
    } finally {
      setCreating(false);
    }
  }

  return (
    <div className="company-setting-fields">
      <div className="workflow-settings-card">
        <div className="workflow-setting-row" style={{ borderBottom: "none" }}>
          <div>
            <strong>Support &amp; maintenance tickets</strong>
            <br />
            <span style={{ fontWeight: 400, color: "#6b7280" }}>
              Report a platform issue to the T-ZONE team. Use this for problems with the platform itself — not for your own customers' conversations.
            </span>
          </div>
          {showForm ? null : <button type="button" onClick={() => setShowForm(true)}>+ New ticket</button>}
        </div>

        {error ? <p style={{ color: "#c0392b" }}>{error}</p> : null}

        {showForm ? (
          <form onSubmit={submit} style={{ display: "flex", flexDirection: "column", gap: 10, padding: "12px 0" }}>
            <input value={subject} onChange={(e) => setSubject(e.target.value)} placeholder="Subject" required
              style={{ padding: "8px 10px", borderRadius: 8, border: "1px solid #d5dae5" }} />
            <textarea value={description} onChange={(e) => setDescription(e.target.value)} placeholder="Describe the issue — what happened, what you expected, and any steps to reproduce." required rows={4}
              style={{ padding: "8px 10px", borderRadius: 8, border: "1px solid #d5dae5" }} />
            <label style={{ display: "flex", alignItems: "center", gap: 10, fontSize: 14 }}>
              Priority
              <select value={priority} onChange={(e) => setPriority(e.target.value)}>
                {TICKET_PRIORITIES.map((p) => <option key={p} value={p} style={{ textTransform: "capitalize" }}>{p}</option>)}
              </select>
            </label>
            <div style={{ display: "flex", gap: 10 }}>
              <button type="submit" disabled={creating}>{creating ? "Submitting…" : "Submit ticket"}</button>
              <button type="button" onClick={() => setShowForm(false)}>Cancel</button>
            </div>
          </form>
        ) : null}
      </div>

      <div className="workflow-settings-card">
        {tickets.length ? tickets.map((ticket) => (
          <div className="workflow-setting-row" key={ticket.id} style={{ flexDirection: "column", alignItems: "stretch", gap: 4 }}>
            <div style={{ display: "flex", justifyContent: "space-between", alignItems: "center", gap: 12 }}>
              <strong>{ticket.subject}</strong>
              <span style={{ fontSize: 12, fontWeight: 600, color: TICKET_STATUS_COLORS[ticket.status] || "#6b7280", textTransform: "capitalize", whiteSpace: "nowrap" }}>
                {(ticket.status || "open").replace("_", " ")}
              </span>
            </div>
            <span style={{ color: "#6b7280", fontSize: 13 }}>{ticket.description}</span>
            <span style={{ color: "#9ca3af", fontSize: 12 }}>
              Priority: <span style={{ textTransform: "capitalize" }}>{ticket.priority}</span>
              {ticket.created_at ? ` · ${ticket.created_at.slice(0, 10)}` : ""}
            </span>
          </div>
        )) : <p style={{ padding: "12px 0", color: "#6b7280" }}>No tickets yet. Open one above if you hit a platform issue.</p>}
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

function ReplyFlowLink() {
  const navigate = useNavigate();
  return (
    <div className="workflow-settings-card">
      <div className="workflow-setting-row" style={{ borderBottom: "none" }}>
        <div>
          <strong>Reply Flow</strong>
          <br />
          <span style={{ fontWeight: 400, color: "#6b7280" }}>
            Design the real step-by-step conversation flow — greeting, AI reply mode, appointments, task
            creation, human handoff — per channel and department, on its own drag-and-drop canvas.
          </span>
        </div>
        <button type="button" onClick={() => navigate("/reply-flows")}>Open Reply Flows</button>
      </div>
    </div>
  );
}

function RolesPermissionsLink() {
  const navigate = useNavigate();
  return (
    <div className="workflow-settings-card">
      <div className="workflow-setting-row" style={{ borderBottom: "none" }}>
        <div>
          <strong>Roles &amp; Permissions</strong>
          <br />
          <span style={{ fontWeight: 400, color: "#6b7280" }}>
            Create roles, grant exactly the permissions each one needs, and assign employees to them.
          </span>
        </div>
        <button type="button" onClick={() => navigate("/roles")}>Open Roles &amp; Permissions</button>
      </div>
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

function SavedRepliesLink() {
  const navigate = useNavigate();
  return (
    <div className="workflow-settings-card" style={{ marginTop: 20 }}>
      <div className="workflow-setting-row" style={{ borderBottom: "none" }}>
        <div>
          <strong>Saved replies</strong>
          <br />
          <span style={{ fontWeight: 400, color: "#6b7280" }}>
            Saved replies now live on their own page. Admins add and manage them there; employees insert
            department-relevant ones from inside any conversation.
          </span>
        </div>
        <button type="button" onClick={() => navigate("/saved-replies")}>Open Saved Replies</button>
      </div>
    </div>
  );
}

function ReplyFlowAndSavedReplies() {
  return (
    <div>
      <ReplyFlowLink />
      <div style={{ marginTop: 20 }}><SavedRepliesLink /></div>
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
    </div>
  );
}

export default function CompanySettingsPage() {
  const navigate = useNavigate();
  const [searchParams] = useSearchParams();
  const requestedSection = searchParams.get("section");
  const [active, setActive] = useState(
    requestedSection && SECTIONS.some(([id]) => id === requestedSection) ? requestedSection : "profile",
  );
  const [query, setQuery] = useState("");
  const [isAdmin, setIsAdmin] = useState(false);

  useEffect(() => {
    let cancelled = false;
    getCurrentUserRequest()
      .then((response) => {
        if (cancelled) return;
        if (response?.user?.is_super_admin) { setIsAdmin(true); return; }
        const activeCompanyId = response?.user?.active_company_id;
        const companies = Array.isArray(response?.companies) ? response.companies : [];
        const activeCompany = companies.find((company) => company.id === activeCompanyId) || companies[0];
        const permissionCodes = activeCompany?.permission_codes || [];
        setIsAdmin(activeCompany?.role_code === "owner" || permissionCodes.includes("users.manage"));
      })
      .catch(() => {
        // Not fatal — the destination routes already enforce this server-side either way.
      });
    return () => { cancelled = true; };
  }, []);

  const allowedSections = useMemo(() => SECTIONS.filter(([, , , , requiredPermission]) => !requiredPermission || isAdmin), [isAdmin]);
  const visible = useMemo(() => allowedSections.filter(([, title, description]) => `${title} ${description}`.toLowerCase().includes(query.toLowerCase())), [allowedSections, query]);
  const selected = allowedSections.find(([id]) => id === active) || visible[0] || allowedSections[0];
  return <section className="company-settings-shell company-settings-locked-layout"><aside className="company-settings-nav"><button className="company-settings-back" type="button" onClick={() => navigate("/dashboard")}><ArrowBackOutlined /> Back to platform</button><div className="company-settings-nav-heading"><span>COMPANY CONTROL</span><h1>Company Settings</h1></div><label className="settings-search"><SearchOutlined /><input value={query} onChange={(e) => setQuery(e.target.value)} placeholder="Search company settings..." /></label><nav className="company-settings-nav-scroll">{visible.map(([id,title]) => <button type="button" key={id} className={active===id?"is-active":""} onClick={()=>setActive(id)}>{title}</button>)}</nav></aside><main className="company-settings-content"><div className="company-settings-content-scroll"><header><span>COMPANY CONTROL</span><h2>{selected[1]}</h2><p>{selected[2]}</p></header>{active === "ai" ? <WorkflowSettings /> : active === "channels" ? <SecureChannelsPanel /> : active === "billing" ? <BillingView /> : active === "help" ? <HelpView /> : active === "ticketing" ? <TicketingView /> : active === "profile" ? <ProfileSettings /> : active === "flow" ? <ReplyFlowAndSavedReplies /> : active === "roles" ? <RolesPermissionsLink /> : active === "security" ? <SecurityStatusView /> : active === "departments" ? <DepartmentsManager /> : (active === "api" || active === "backup") ? null : <><div className="company-setting-fields">{selected[3].map((field,index)=><article className="company-setting-field" key={field}><div><strong>{field}</strong><span>{index===0?"Configured from this company workspace.":"Ready for company-wide configuration."}</span></div><button type="button">Configure</button></article>)}</div><div className="settings-card-grid"><article className="settings-card"><h3>Company-wide setting</h3><p>Changes in this section apply to authorized users across the company.</p></article><article className="settings-card"><h3>Super Admin policy</h3><p>Availability, labels and locked defaults can be controlled by the separate Super Admin control plane.</p></article></div></>}</div></main></section>;
}
