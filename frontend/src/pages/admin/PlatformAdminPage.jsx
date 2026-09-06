import { useEffect, useState } from "react";
import { useNavigate } from "react-router-dom";
import {
  listPlatformCompaniesRequest,
  createPlatformCompanyRequest,
  setPlatformCompanyStatusRequest,
  listPlatformPlansRequest,
  createPlatformPlanRequest,
  updatePlatformPlanRequest,
  changePlatformCompanyPlanRequest,
  updatePlatformCompanyModulesRequest,
  listSubscriptionRequestsRequest,
  reviewSubscriptionRequestRequest,
  getPlatformUsageRequest,
  getPlatformRevenueRequest,
  listPlatformAuditLogsRequest,
  getCompanySubscriptionHistoryRequest,
} from "../../api/client";

const FEATURE_FIELDS = [
  ["voice_ai_enabled", "Voice AI"],
  ["image_ai_enabled", "Image AI"],
  ["accounting_connector_enabled", "Accounting connector"],
  ["product_connector_enabled", "Product connector"],
];

const LIMIT_FIELDS = [
  ["max_users", "Max users"],
  ["max_channel_accounts", "Max channels"],
  ["max_ai_messages", "Max AI messages / mo"],
  ["max_knowledge_items", "Max knowledge items"],
];

const emptyPlanForm = {
  name: "", code: "", price_monthly: 0, currency: "USD",
  max_users: 1, max_channel_accounts: 1, max_ai_messages: 500, max_knowledge_items: 100,
  voice_ai_enabled: false, image_ai_enabled: false,
  accounting_connector_enabled: false, product_connector_enabled: false,
};

export default function PlatformAdminPage() {
  const navigate = useNavigate();
  const [tab, setTab] = useState("companies");
  const [companies, setCompanies] = useState([]);
  const [plans, setPlans] = useState([]);
  const [usage, setUsage] = useState(null);
  const [revenue, setRevenue] = useState(null);
  const [subscriptionRequests, setSubscriptionRequests] = useState([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState("");
  const [saving, setSaving] = useState(false);

  const [companyModalOpen, setCompanyModalOpen] = useState(false);
  const [newCompany, setNewCompany] = useState({
    name: "", slug: "", country: "", currency: "USD", plan_id: "", trial_days: 14,
    main_admin_email: "", contact_phone: "", license_code: "",
  });
  const [planTarget, setPlanTarget] = useState(null);
  const [modulesTarget, setModulesTarget] = useState(null);
  const [historyTarget, setHistoryTarget] = useState(null); // { companyId, companyName, items, loading }

  const [planModal, setPlanModal] = useState(null); // { mode: "create" | "edit", form, planId }

  const [auditLogs, setAuditLogs] = useState([]);
  const [auditTotal, setAuditTotal] = useState(0);
  const [auditFilters, setAuditFilters] = useState({ companyId: "", action: "" });
  const [auditLoading, setAuditLoading] = useState(false);

  async function load() {
    setLoading(true);
    try {
      const [companiesResult, plansResult, usageResult, requestsResult, revenueResult] = await Promise.all([
        listPlatformCompaniesRequest(),
        listPlatformPlansRequest(false),
        getPlatformUsageRequest(),
        listSubscriptionRequestsRequest(),
        getPlatformRevenueRequest(),
      ]);
      setCompanies(companiesResult.companies || []);
      setPlans(plansResult.plans || []);
      setUsage(usageResult);
      setRevenue(revenueResult);
      setSubscriptionRequests(requestsResult.requests || []);
      setError("");
    } catch (e) {
      setError(e.message || "Unable to load platform data.");
    } finally {
      setLoading(false);
    }
  }

  useEffect(() => { load(); }, []);

  async function loadAuditLogs() {
    setAuditLoading(true);
    try {
      const result = await listPlatformAuditLogsRequest({
        companyId: auditFilters.companyId || undefined,
        action: auditFilters.action || undefined,
        limit: 100,
      });
      setAuditLogs(result.items || []);
      setAuditTotal(result.total || 0);
      setError("");
    } catch (e) {
      setError(e.message || "Unable to load audit logs.");
    } finally {
      setAuditLoading(false);
    }
  }

  useEffect(() => {
    if (tab === "audit") loadAuditLogs();
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [tab]);

  async function openHistory(company) {
    setHistoryTarget({ companyId: company.id, companyName: company.name, items: [], loading: true });
    try {
      const result = await getCompanySubscriptionHistoryRequest(company.id);
      setHistoryTarget({ companyId: company.id, companyName: company.name, items: result.history || [], loading: false });
    } catch (e) {
      setError(e.message || "Unable to load subscription history.");
      setHistoryTarget(null);
    }
  }

  async function createCompany(e) {
    e.preventDefault();
    setSaving(true);
    try {
      await createPlatformCompanyRequest({
        ...newCompany,
        plan_id: newCompany.plan_id ? Number(newCompany.plan_id) : null,
        trial_days: Number(newCompany.trial_days) || 14,
      });
      setNewCompany({
        name: "", slug: "", country: "", currency: "USD", plan_id: "", trial_days: 14,
        main_admin_email: "", contact_phone: "", license_code: "",
      });
      setCompanyModalOpen(false);
      await load();
    } catch (x) {
      setError(x.message);
    } finally {
      setSaving(false);
    }
  }

  async function toggleStatus(company) {
    const nextStatus = company.status === "active" ? "suspended" : "active";
    setSaving(true);
    try {
      await setPlatformCompanyStatusRequest(company.id, nextStatus);
      await load();
    } catch (x) {
      setError(x.message);
    } finally {
      setSaving(false);
    }
  }

  async function changePlan(e) {
    e.preventDefault();
    if (!planTarget) return;
    setSaving(true);
    try {
      await changePlatformCompanyPlanRequest(planTarget.companyId, Number(planTarget.planId), 30);
      setPlanTarget(null);
      await load();
    } catch (x) {
      setError(x.message);
    } finally {
      setSaving(false);
    }
  }

  async function saveModules(e) {
    e.preventDefault();
    if (!modulesTarget) return;
    setSaving(true);
    try {
      await updatePlatformCompanyModulesRequest(modulesTarget.companyId, modulesTarget.values);
      setModulesTarget(null);
      await load();
    } catch (x) {
      setError(x.message);
    } finally {
      setSaving(false);
    }
  }

  function openCreatePlan() {
    setPlanModal({ mode: "create", form: { ...emptyPlanForm } });
  }

  function openEditPlan(plan) {
    setPlanModal({ mode: "edit", planId: plan.id, form: { ...plan } });
  }

  async function savePlan(e) {
    e.preventDefault();
    setSaving(true);
    try {
      const form = planModal.form;
      if (planModal.mode === "create") {
        await createPlatformPlanRequest(form);
      } else {
        await updatePlatformPlanRequest(planModal.planId, form);
      }
      setPlanModal(null);
      await load();
    } catch (x) {
      setError(x.message);
    } finally {
      setSaving(false);
    }
  }

  async function retirePlan(plan) {
    setSaving(true);
    try {
      await updatePlatformPlanRequest(plan.id, { status: plan.status === "active" ? "retired" : "active" });
      await load();
    } catch (x) {
      setError(x.message);
    } finally {
      setSaving(false);
    }
  }

  async function reviewRequest(requestId, approve) {
    setSaving(true);
    try {
      await reviewSubscriptionRequestRequest(requestId, approve);
      await load();
    } catch (x) {
      setError(x.message);
    } finally {
      setSaving(false);
    }
  }

  return (
    <div style={styles.shell}>
      <header style={styles.header}>
        <div style={styles.headerLeft}>
          <span style={styles.headerBadge}>PLATFORM ADMIN</span>
          <strong style={styles.headerTitle}>T-ZONE Super Admin</strong>
        </div>
        <button type="button" style={styles.backButton} onClick={() => navigate("/dashboard")}>
          ← Back to Dashboard
        </button>
      </header>

      <nav style={styles.tabBar}>
        {[["companies", "Companies"], ["plans", "Plans & Features"], ["requests", "Subscription Requests"], ["usage", "Usage"], ["audit", "Audit Log"]].map(([key, label]) => (
          <button
            key={key}
            type="button"
            onClick={() => setTab(key)}
            style={{ ...styles.tabButton, ...(tab === key ? styles.tabButtonActive : {}) }}
          >
            {label}
          </button>
        ))}
      </nav>

      <main style={styles.content}>
        {error ? <div style={styles.error}>{error}</div> : null}
        {loading ? <div style={styles.loading}>Loading platform data…</div> : null}

        {!loading && tab === "companies" ? (
          <section>
            <div style={styles.sectionHeader}>
              <div>
                <h2 style={styles.sectionTitle}>Companies</h2>
                <p style={styles.sectionSubtitle}>{companies.length} companies · {usage?.companies_by_status?.active || 0} active</p>
              </div>
              <button type="button" style={styles.primaryButton} onClick={() => setCompanyModalOpen(true)}>
                + Add company
              </button>
            </div>

            <div style={styles.tableWrap}>
              <table style={styles.table}>
                <thead>
                  <tr style={styles.tableHeadRow}>
                    <th style={styles.th}>Company</th>
                    <th style={styles.th}>License</th>
                    <th style={styles.th}>Plan</th>
                    <th style={styles.th}>Subscription</th>
                    <th style={styles.th}>Users</th>
                    <th style={styles.th}>Channels</th>
                    <th style={styles.th}>Status</th>
                    <th style={styles.th}></th>
                  </tr>
                </thead>
                <tbody>
                  {companies.map((company) => (
                    <tr key={company.id} style={styles.tableRow}>
                      <td style={styles.td}>
                        <strong>{company.name}</strong>
                        <div style={styles.muted}>{company.slug}</div>
                      </td>
                      <td style={styles.td}>
                        <div>{company.license_code || "—"}</div>
                        <div style={styles.muted}>{company.main_admin_email || "no admin email set"}</div>
                        <div style={styles.muted}>{company.purchased_at ? `since ${company.purchased_at.slice(0, 10)}` : ""}</div>
                      </td>
                      <td style={styles.td}>{company.plan_name || "No plan"}</td>
                      <td style={styles.td}>
                        {company.subscription_status
                          ? `${company.subscription_status}${company.expires_at ? ` · until ${company.expires_at.slice(0, 10)}` : ""}`
                          : "No subscription"}
                        <div>
                          <button
                            type="button"
                            style={styles.linkButton}
                            disabled={saving}
                            onClick={() => setPlanTarget({ companyId: company.id, planId: company.plan_id || "" })}
                          >
                            Change plan
                          </button>
                        </div>
                      </td>
                      <td style={styles.td}>
                        {company.active_users}{company.max_users != null ? ` / ${company.max_users}` : ""}
                      </td>
                      <td style={styles.td}>
                        {company.active_channels}{company.max_channel_accounts != null ? ` / ${company.max_channel_accounts}` : ""}
                      </td>
                      <td style={styles.td}>
                        <span style={{ ...styles.statusPill, ...(company.status === "active" ? styles.statusActive : styles.statusInactive) }}>
                          {company.status}
                        </span>
                      </td>
                      <td style={styles.td}>
                        <button type="button" style={styles.linkButton} disabled={saving} onClick={() => toggleStatus(company)}>
                          {company.status === "active" ? "Suspend" : "Reactivate"}
                        </button>
                        <div>
                          <button
                            type="button"
                            style={styles.linkButton}
                            onClick={() => setModulesTarget({
                              companyId: company.id,
                              values: {
                                appointments: Boolean(company.module_appointments_enabled),
                                scheduler: Boolean(company.module_scheduler_enabled),
                                catalogue: Boolean(company.module_catalogue_enabled),
                                team_chat: Boolean(company.module_team_chat_enabled),
                                comments: Boolean(company.module_comments_enabled),
                              },
                            })}
                          >
                            Modules
                          </button>
                        </div>
                        <div>
                          <button type="button" style={styles.linkButton} onClick={() => openHistory(company)}>
                            History
                          </button>
                        </div>
                      </td>
                    </tr>
                  ))}
                  {!companies.length ? (
                    <tr><td style={styles.td} colSpan={8}>No companies yet — add the first one.</td></tr>
                  ) : null}
                </tbody>
              </table>
            </div>
          </section>
        ) : null}

        {!loading && tab === "plans" ? (
          <section>
            <div style={styles.sectionHeader}>
              <div>
                <h2 style={styles.sectionTitle}>Plans & Features</h2>
                <p style={styles.sectionSubtitle}>What every plan allows — limits and enabled features.</p>
              </div>
              <button type="button" style={styles.primaryButton} onClick={openCreatePlan}>
                + New plan
              </button>
            </div>

            <div style={styles.planGrid}>
              {plans.map((plan) => (
                <div key={plan.id} style={styles.planCard}>
                  <div style={styles.planCardHeader}>
                    <strong>{plan.name}</strong>
                    <span style={{ ...styles.statusPill, ...(plan.status === "active" ? styles.statusActive : styles.statusInactive) }}>
                      {plan.status}
                    </span>
                  </div>
                  <div style={styles.muted}>{plan.code} · ${plan.price_monthly}/mo</div>
                  <ul style={styles.limitList}>
                    {LIMIT_FIELDS.map(([field, label]) => (
                      <li key={field}>{label}: <strong>{plan[field]}</strong></li>
                    ))}
                  </ul>
                  <div style={styles.featureRow}>
                    {FEATURE_FIELDS.map(([field, label]) => (
                      <span key={field} style={{ ...styles.featureBadge, ...(plan[field] ? styles.featureOn : styles.featureOff) }}>
                        {label}
                      </span>
                    ))}
                  </div>
                  <div style={styles.planCardActions}>
                    <button type="button" style={styles.linkButton} onClick={() => openEditPlan(plan)}>Edit</button>
                    <button type="button" style={styles.linkButton} onClick={() => retirePlan(plan)}>
                      {plan.status === "active" ? "Retire" : "Reactivate"}
                    </button>
                  </div>
                </div>
              ))}
            </div>
          </section>
        ) : null}

        {!loading && tab === "requests" ? (
          <section>
            <h2 style={styles.sectionTitle}>Subscription Requests</h2>
            <p style={styles.sectionSubtitle}>Companies requesting a plan change or renewal — approving applies the plan immediately.</p>
            <div style={styles.tableWrap}>
              <table style={styles.table}>
                <thead>
                  <tr style={styles.tableHeadRow}>
                    <th style={styles.th}>Company</th>
                    <th style={styles.th}>Requested plan</th>
                    <th style={styles.th}>Note</th>
                    <th style={styles.th}>Status</th>
                    <th style={styles.th}>Requested</th>
                    <th style={styles.th}></th>
                  </tr>
                </thead>
                <tbody>
                  {subscriptionRequests.map((req) => (
                    <tr key={req.id} style={styles.tableRow}>
                      <td style={styles.td}>{req.company_name}</td>
                      <td style={styles.td}>{req.plan_name}</td>
                      <td style={styles.td}>{req.note || "—"}</td>
                      <td style={styles.td}>
                        <span style={{ ...styles.statusPill, ...(req.status === "pending" ? styles.statusInactive : styles.statusActive) }}>
                          {req.status}
                        </span>
                      </td>
                      <td style={styles.td}>{req.created_at?.slice(0, 10)}</td>
                      <td style={styles.td}>
                        {req.status === "pending" ? (
                          <div style={{ display: "flex", gap: 10 }}>
                            <button type="button" style={styles.linkButton} disabled={saving} onClick={() => reviewRequest(req.id, true)}>Approve</button>
                            <button type="button" style={styles.linkButton} disabled={saving} onClick={() => reviewRequest(req.id, false)}>Reject</button>
                          </div>
                        ) : null}
                      </td>
                    </tr>
                  ))}
                  {!subscriptionRequests.length ? (
                    <tr><td style={styles.td} colSpan={6}>No subscription requests yet.</td></tr>
                  ) : null}
                </tbody>
              </table>
            </div>
          </section>
        ) : null}

        {!loading && tab === "usage" ? (
          <section>
            <h2 style={styles.sectionTitle}>Revenue</h2>
            <p style={styles.sectionSubtitle}>MRR is computed from paying ("active") subscriptions only — trials are shown separately, not counted as revenue.</p>
            <div style={styles.usageGrid}>
              <div style={styles.usageCard}>
                <div style={styles.muted}>Total MRR</div>
                <strong style={styles.usageNumber}>${(revenue?.total_mrr ?? 0).toFixed(2)}</strong>
                <div style={styles.muted}>{revenue?.paid_count ?? 0} paid · {revenue?.trial_count ?? 0} trialing</div>
              </div>
            </div>

            <div style={{ ...styles.tableWrap, marginTop: 16 }}>
              <table style={styles.table}>
                <thead>
                  <tr style={styles.tableHeadRow}>
                    <th style={styles.th}>Plan</th>
                    <th style={styles.th}>Active subscriptions</th>
                    <th style={styles.th}>MRR contribution</th>
                  </tr>
                </thead>
                <tbody>
                  {(revenue?.by_plan || []).map((row) => (
                    <tr key={row.plan_id} style={styles.tableRow}>
                      <td style={styles.td}>{row.plan_name}</td>
                      <td style={styles.td}>{row.active_subscriptions}</td>
                      <td style={styles.td}>${row.mrr?.toFixed?.(2) ?? row.mrr}</td>
                    </tr>
                  ))}
                  {!revenue?.by_plan?.length ? (
                    <tr><td style={styles.td} colSpan={3}>No active or trialing subscriptions yet.</td></tr>
                  ) : null}
                </tbody>
              </table>
            </div>

            <h2 style={{ ...styles.sectionTitle, marginTop: 28 }}>Platform usage (this month)</h2>
            <div style={styles.usageGrid}>
              {(usage?.usage_this_month || []).map((row) => (
                <div key={row.usage_type} style={styles.usageCard}>
                  <div style={styles.muted}>{row.usage_type}</div>
                  <strong style={styles.usageNumber}>{row.total_quantity}</strong>
                  <div style={styles.muted}>${row.total_cost?.toFixed?.(2) ?? row.total_cost}</div>
                </div>
              ))}
              {!usage?.usage_this_month?.length ? <div style={styles.muted}>No usage recorded yet this month.</div> : null}
            </div>
          </section>
        ) : null}

        {!loading && tab === "audit" ? (
          <section>
            <div style={styles.sectionHeader}>
              <div>
                <h2 style={styles.sectionTitle}>Audit Log</h2>
                <p style={styles.sectionSubtitle}>{auditTotal} total events · every super-admin mutation, newest first.</p>
              </div>
            </div>

            <form
              style={{ display: "flex", gap: 12, alignItems: "flex-end", marginBottom: 16, flexWrap: "wrap" }}
              onSubmit={(e) => { e.preventDefault(); loadAuditLogs(); }}
            >
              <label style={styles.label}>
                Company
                <select
                  style={styles.input}
                  value={auditFilters.companyId}
                  onChange={(e) => setAuditFilters({ ...auditFilters, companyId: e.target.value })}
                >
                  <option value="">All companies</option>
                  {companies.map((c) => (
                    <option key={c.id} value={c.id}>{c.name}</option>
                  ))}
                </select>
              </label>
              <label style={styles.label}>
                Action contains
                <input
                  style={styles.input}
                  value={auditFilters.action}
                  onChange={(e) => setAuditFilters({ ...auditFilters, action: e.target.value })}
                  placeholder="e.g. company_status"
                />
              </label>
              <button type="submit" style={styles.primaryButton} disabled={auditLoading}>
                {auditLoading ? "Filtering…" : "Filter"}
              </button>
            </form>

            <div style={styles.tableWrap}>
              <table style={styles.table}>
                <thead>
                  <tr style={styles.tableHeadRow}>
                    <th style={styles.th}>Time</th>
                    <th style={styles.th}>Company</th>
                    <th style={styles.th}>Actor</th>
                    <th style={styles.th}>Action</th>
                    <th style={styles.th}>Entity</th>
                  </tr>
                </thead>
                <tbody>
                  {auditLogs.map((log) => (
                    <tr key={log.id} style={styles.tableRow}>
                      <td style={styles.td}>{log.created_at ? String(log.created_at).replace("T", " ").slice(0, 19) : "—"}</td>
                      <td style={styles.td}>{log.company_name || "—"}</td>
                      <td style={styles.td}>
                        {log.user_email || log.user_full_name
                          ? `${log.user_full_name || ""}${log.user_email ? ` (${log.user_email})` : ""}`.trim()
                          : "System"}
                      </td>
                      <td style={styles.td}>{log.action}</td>
                      <td style={styles.td}>{log.entity_type ? `${log.entity_type}${log.entity_id ? ` #${log.entity_id}` : ""}` : "—"}</td>
                    </tr>
                  ))}
                  {!auditLogs.length ? (
                    <tr><td style={styles.td} colSpan={5}>{auditLoading ? "Loading…" : "No audit log events match these filters."}</td></tr>
                  ) : null}
                </tbody>
              </table>
            </div>
          </section>
        ) : null}
      </main>

      {companyModalOpen ? (
        <div style={styles.modalBackdrop} onMouseDown={() => setCompanyModalOpen(false)}>
          <form style={styles.modal} onSubmit={createCompany} onMouseDown={(e) => e.stopPropagation()}>
            <div style={styles.modalHeader}>
              <h2 style={styles.sectionTitle}>Add company</h2>
              <button type="button" style={styles.closeButton} onClick={() => setCompanyModalOpen(false)}>×</button>
            </div>
            <label style={styles.label}>
              Company name
              <input style={styles.input} value={newCompany.name} onChange={(e) => setNewCompany({ ...newCompany, name: e.target.value })} required />
            </label>
            <label style={styles.label}>
              Slug
              <input style={styles.input} value={newCompany.slug} onChange={(e) => setNewCompany({ ...newCompany, slug: e.target.value.toLowerCase().replace(/\s+/g, "-") })} required />
            </label>
            <label style={styles.label}>
              Country
              <input style={styles.input} value={newCompany.country} onChange={(e) => setNewCompany({ ...newCompany, country: e.target.value })} />
            </label>
            <label style={styles.label}>
              Main admin email
              <input style={styles.input} type="email" value={newCompany.main_admin_email} onChange={(e) => setNewCompany({ ...newCompany, main_admin_email: e.target.value })} placeholder="admin@company.com" />
            </label>
            <label style={styles.label}>
              Contact phone
              <input style={styles.input} value={newCompany.contact_phone} onChange={(e) => setNewCompany({ ...newCompany, contact_phone: e.target.value })} />
            </label>
            <label style={styles.label}>
              License code (leave blank to auto-generate)
              <input style={styles.input} value={newCompany.license_code} onChange={(e) => setNewCompany({ ...newCompany, license_code: e.target.value })} placeholder="TZ-XXXX-XXXX-XXXX" />
            </label>
            <label style={styles.label}>
              Plan
              <select style={styles.input} value={newCompany.plan_id} onChange={(e) => setNewCompany({ ...newCompany, plan_id: e.target.value })}>
                <option value="">No plan yet</option>
                {plans.filter((p) => p.status === "active").map((plan) => (
                  <option key={plan.id} value={plan.id}>{plan.name} — ${plan.price_monthly}/mo</option>
                ))}
              </select>
            </label>
            <label style={styles.label}>
              Trial days
              <input style={styles.input} type="number" min="0" value={newCompany.trial_days} onChange={(e) => setNewCompany({ ...newCompany, trial_days: e.target.value })} />
            </label>
            <button style={styles.primaryButton} type="submit" disabled={saving}>{saving ? "Creating…" : "Create company"}</button>
          </form>
        </div>
      ) : null}

      {planTarget ? (
        <div style={styles.modalBackdrop} onMouseDown={() => setPlanTarget(null)}>
          <form style={styles.modal} onSubmit={changePlan} onMouseDown={(e) => e.stopPropagation()}>
            <div style={styles.modalHeader}>
              <h2 style={styles.sectionTitle}>Change plan</h2>
              <button type="button" style={styles.closeButton} onClick={() => setPlanTarget(null)}>×</button>
            </div>
            <label style={styles.label}>
              New plan
              <select style={styles.input} value={planTarget.planId} onChange={(e) => setPlanTarget({ ...planTarget, planId: e.target.value })} required>
                <option value="">Select a plan</option>
                {plans.filter((p) => p.status === "active").map((plan) => (
                  <option key={plan.id} value={plan.id}>{plan.name} — ${plan.price_monthly}/mo</option>
                ))}
              </select>
            </label>
            <button style={styles.primaryButton} type="submit" disabled={saving || !planTarget.planId}>{saving ? "Saving…" : "Change plan"}</button>
          </form>
        </div>
      ) : null}

      {modulesTarget ? (
        <div style={styles.modalBackdrop} onMouseDown={() => setModulesTarget(null)}>
          <form style={styles.modal} onSubmit={saveModules} onMouseDown={(e) => e.stopPropagation()}>
            <div style={styles.modalHeader}>
              <h2 style={styles.sectionTitle}>Modules for this company</h2>
              <button type="button" style={styles.closeButton} onClick={() => setModulesTarget(null)}>×</button>
            </div>
            {[
              ["appointments", "Appointments"],
              ["scheduler", "Scheduler"],
              ["catalogue", "Catalogue"],
              ["team_chat", "Team Chat"],
              ["comments", "Comments"],
            ].map(([key, label]) => (
              <label key={key} style={styles.checkboxLabel}>
                <input
                  type="checkbox"
                  checked={Boolean(modulesTarget.values[key])}
                  onChange={(e) => setModulesTarget({
                    ...modulesTarget,
                    values: { ...modulesTarget.values, [key]: e.target.checked },
                  })}
                />
                {label}
              </label>
            ))}
            <button style={styles.primaryButton} type="submit" disabled={saving}>{saving ? "Saving…" : "Save modules"}</button>
          </form>
        </div>
      ) : null}

      {planModal ? (
        <div style={styles.modalBackdrop} onMouseDown={() => setPlanModal(null)}>
          <form style={styles.modal} onSubmit={savePlan} onMouseDown={(e) => e.stopPropagation()}>
            <div style={styles.modalHeader}>
              <h2 style={styles.sectionTitle}>{planModal.mode === "create" ? "New plan" : `Edit ${planModal.form.name}`}</h2>
              <button type="button" style={styles.closeButton} onClick={() => setPlanModal(null)}>×</button>
            </div>
            <label style={styles.label}>
              Plan name
              <input style={styles.input} value={planModal.form.name} onChange={(e) => setPlanModal({ ...planModal, form: { ...planModal.form, name: e.target.value } })} required />
            </label>
            {planModal.mode === "create" ? (
              <label style={styles.label}>
                Plan code (unique)
                <input style={styles.input} value={planModal.form.code} onChange={(e) => setPlanModal({ ...planModal, form: { ...planModal.form, code: e.target.value.toLowerCase().replace(/\s+/g, "-") } })} required />
              </label>
            ) : null}
            <label style={styles.label}>
              Price / month (USD)
              <input style={styles.input} type="number" min="0" step="0.01" value={planModal.form.price_monthly} onChange={(e) => setPlanModal({ ...planModal, form: { ...planModal.form, price_monthly: Number(e.target.value) } })} />
            </label>

            <div style={styles.formGrid}>
              {LIMIT_FIELDS.map(([field, label]) => (
                <label key={field} style={styles.label}>
                  {label}
                  <input
                    style={styles.input}
                    type="number"
                    min="0"
                    value={planModal.form[field]}
                    onChange={(e) => setPlanModal({ ...planModal, form: { ...planModal.form, [field]: Number(e.target.value) } })}
                  />
                </label>
              ))}
            </div>

            <div style={styles.formGrid}>
              {FEATURE_FIELDS.map(([field, label]) => (
                <label key={field} style={styles.checkboxLabel}>
                  <input
                    type="checkbox"
                    checked={Boolean(planModal.form[field])}
                    onChange={(e) => setPlanModal({ ...planModal, form: { ...planModal.form, [field]: e.target.checked } })}
                  />
                  {label}
                </label>
              ))}
            </div>

            <button style={styles.primaryButton} type="submit" disabled={saving}>
              {saving ? "Saving…" : planModal.mode === "create" ? "Create plan" : "Save changes"}
            </button>
          </form>
        </div>
      ) : null}

      {historyTarget ? (
        <div style={styles.modalBackdrop} onMouseDown={() => setHistoryTarget(null)}>
          <div style={{ ...styles.modal, width: 560 }} onMouseDown={(e) => e.stopPropagation()}>
            <div style={styles.modalHeader}>
              <h2 style={styles.sectionTitle}>Subscription history — {historyTarget.companyName}</h2>
              <button type="button" style={styles.closeButton} onClick={() => setHistoryTarget(null)}>×</button>
            </div>
            {historyTarget.loading ? (
              <div style={styles.loading}>Loading…</div>
            ) : (
              <div style={styles.tableWrap}>
                <table style={styles.table}>
                  <thead>
                    <tr style={styles.tableHeadRow}>
                      <th style={styles.th}>Plan</th>
                      <th style={styles.th}>Status</th>
                      <th style={styles.th}>Starts</th>
                      <th style={styles.th}>Expires</th>
                      <th style={styles.th}>Created</th>
                    </tr>
                  </thead>
                  <tbody>
                    {historyTarget.items.map((row) => (
                      <tr key={row.id} style={styles.tableRow}>
                        <td style={styles.td}>{row.plan_name}{row.price_monthly != null ? ` ($${row.price_monthly}/mo)` : ""}</td>
                        <td style={styles.td}>
                          <span style={{ ...styles.statusPill, ...(row.status === "active" || row.status === "trialing" ? styles.statusActive : styles.statusInactive) }}>
                            {row.status}
                          </span>
                        </td>
                        <td style={styles.td}>{row.starts_at ? String(row.starts_at).slice(0, 10) : "—"}</td>
                        <td style={styles.td}>{row.expires_at ? String(row.expires_at).slice(0, 10) : "—"}</td>
                        <td style={styles.td}>{row.created_at ? String(row.created_at).slice(0, 10) : "—"}</td>
                      </tr>
                    ))}
                    {!historyTarget.items.length ? (
                      <tr><td style={styles.td} colSpan={5}>No subscription history for this company.</td></tr>
                    ) : null}
                  </tbody>
                </table>
              </div>
            )}
          </div>
        </div>
      ) : null}
    </div>
  );
}

const styles = {
  shell: { minHeight: "100vh", background: "#0b1220", color: "#e7ecf5", fontFamily: "inherit" },
  header: {
    display: "flex", alignItems: "center", justifyContent: "space-between",
    padding: "16px 28px", background: "#111a2e", borderBottom: "1px solid #223052",
  },
  headerLeft: { display: "flex", alignItems: "center", gap: 12 },
  headerBadge: {
    fontSize: 11, fontWeight: 700, letterSpacing: 1, color: "#8fb3ff",
    background: "#1b2947", padding: "4px 8px", borderRadius: 6,
  },
  headerTitle: { fontSize: 18 },
  backButton: {
    background: "transparent", border: "1px solid #2c3c63", color: "#c8d4ea",
    padding: "8px 14px", borderRadius: 8, cursor: "pointer",
  },
  tabBar: { display: "flex", gap: 4, padding: "0 28px", background: "#0e1626", borderBottom: "1px solid #223052" },
  tabButton: {
    background: "transparent", border: "none", color: "#8695b3", padding: "12px 16px",
    cursor: "pointer", fontSize: 14, borderBottom: "2px solid transparent",
  },
  tabButtonActive: { color: "#fff", borderBottom: "2px solid #4f7fff" },
  content: { padding: "24px 28px 60px", maxWidth: 1200, margin: "0 auto" },
  error: { background: "#3a1a24", border: "1px solid #7a2b3f", color: "#ffb4c4", padding: "10px 14px", borderRadius: 8, marginBottom: 16 },
  loading: { color: "#8695b3", padding: "40px 0", textAlign: "center" },
  sectionHeader: { display: "flex", alignItems: "flex-start", justifyContent: "space-between", marginBottom: 18 },
  sectionTitle: { fontSize: 18, margin: 0 },
  sectionSubtitle: { color: "#8695b3", fontSize: 13, margin: "4px 0 0" },
  primaryButton: {
    background: "#4f7fff", color: "#fff", border: "none", padding: "10px 16px",
    borderRadius: 8, cursor: "pointer", fontSize: 14, fontWeight: 600,
  },
  linkButton: { background: "transparent", border: "none", color: "#8fb3ff", cursor: "pointer", fontSize: 13, padding: "2px 0" },
  tableWrap: { background: "#111a2e", border: "1px solid #223052", borderRadius: 12, overflow: "hidden" },
  table: { width: "100%", borderCollapse: "collapse" },
  tableHeadRow: { background: "#151f38" },
  th: { textAlign: "left", padding: "12px 16px", fontSize: 12, color: "#8695b3", fontWeight: 600 },
  tableRow: { borderTop: "1px solid #1c2947" },
  td: { padding: "14px 16px", fontSize: 14, verticalAlign: "top" },
  muted: { color: "#8695b3", fontSize: 12 },
  statusPill: { padding: "3px 10px", borderRadius: 999, fontSize: 12, fontWeight: 600 },
  statusActive: { background: "#123a2b", color: "#5fd99a" },
  statusInactive: { background: "#3a1a1a", color: "#ff9a9a" },
  planGrid: { display: "grid", gridTemplateColumns: "repeat(auto-fill, minmax(260px, 1fr))", gap: 16 },
  planCard: { background: "#111a2e", border: "1px solid #223052", borderRadius: 12, padding: 18 },
  planCardHeader: { display: "flex", alignItems: "center", justifyContent: "space-between", marginBottom: 4 },
  limitList: { listStyle: "none", padding: 0, margin: "12px 0", fontSize: 13, color: "#c8d4ea", lineHeight: 1.8 },
  featureRow: { display: "flex", flexWrap: "wrap", gap: 6, marginBottom: 14 },
  featureBadge: { fontSize: 11, padding: "3px 8px", borderRadius: 999 },
  featureOn: { background: "#123a2b", color: "#5fd99a" },
  featureOff: { background: "#1c2438", color: "#5c6a8a" },
  planCardActions: { display: "flex", gap: 12 },
  usageGrid: { display: "grid", gridTemplateColumns: "repeat(auto-fill, minmax(180px, 1fr))", gap: 16 },
  usageCard: { background: "#111a2e", border: "1px solid #223052", borderRadius: 12, padding: 16 },
  usageNumber: { display: "block", fontSize: 24, margin: "6px 0" },
  modalBackdrop: { position: "fixed", inset: 0, background: "rgba(4,8,18,0.7)", display: "flex", alignItems: "center", justifyContent: "center", zIndex: 50 },
  modal: { background: "#111a2e", border: "1px solid #223052", borderRadius: 14, padding: 24, width: 420, maxHeight: "85vh", overflowY: "auto", display: "flex", flexDirection: "column", gap: 12 },
  modalHeader: { display: "flex", alignItems: "center", justifyContent: "space-between" },
  closeButton: { background: "transparent", border: "none", color: "#8695b3", fontSize: 20, cursor: "pointer" },
  label: { display: "flex", flexDirection: "column", gap: 6, fontSize: 13, color: "#c8d4ea" },
  input: { background: "#0b1220", border: "1px solid #2c3c63", borderRadius: 8, padding: "9px 10px", color: "#e7ecf5", fontSize: 14 },
  checkboxLabel: { display: "flex", alignItems: "center", gap: 8, fontSize: 13, color: "#c8d4ea" },
  formGrid: { display: "grid", gridTemplateColumns: "1fr 1fr", gap: 10 },
};
