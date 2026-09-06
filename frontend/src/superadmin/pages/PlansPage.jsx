import { useCallback, useEffect, useMemo, useState } from "react";
import {
  AddOutlined,
  CloseOutlined,
  EditOutlined,
  RefreshOutlined,
} from "@mui/icons-material";

import {
  createPlanRequest,
  listCompaniesRequest,
  listPlansRequest,
  updatePlanRequest,
} from "../platformClient";
import { formatCount } from "../format";
import {
  ConsoleBanner,
  ConsoleButton,
  ConsoleEmpty,
  ConsoleLoading,
  ConsolePage,
  ConsolePanel,
} from "../components/ConsoleUI";


/*
 * The editable shape of a plan, mirroring PlatformService.PLAN_NUMERIC_FIELDS
 * and PLAN_FLAG_FIELDS. The service refuses a key it does not know rather than
 * dropping it, so anything sent from here has to be one of these: a stored
 * typo would look like a setting that was applied.
 */
const NUMERIC_FIELDS = [
  ["price_monthly", "Price per month", 0.01],
  ["max_users", "Max users", 1],
  ["max_channel_accounts", "Max channel accounts", 1],
  ["max_ai_messages", "Max AI messages", 1],
  ["max_knowledge_items", "Max knowledge items", 1],
];

// The third entry is the short name the table column uses: the full one is
// what the forms say, where there is room for it.
const FLAG_FIELDS = [
  ["voice_ai_enabled", "Voice AI", "Voice"],
  ["image_ai_enabled", "Image AI", "Image"],
  ["accounting_connector_enabled", "Accounting connector", "Accounting"],
  ["product_connector_enabled", "Product connector", "Products"],
];

/*
 * The server's own rule, so a code it will refuse is caught before the request.
 * The hyphen is escaped because the browser compiles a `pattern` with the `v`
 * flag, which rejects a bare one inside a character class — unescaped, the
 * attribute is dropped as invalid and the field validates nothing.
 */
const CODE_PATTERN = "[a-z0-9][a-z0-9_\\-]{1,39}";


function emptyForm() {
  const form = { code: "", name: "" };

  NUMERIC_FIELDS.forEach(([field]) => {
    form[field] = "0";
  });

  FLAG_FIELDS.forEach(([field]) => {
    form[field] = false;
  });

  return form;
}

function formFromPlan(plan) {
  const form = { code: plan.code, name: plan.name || "" };

  NUMERIC_FIELDS.forEach(([field]) => {
    form[field] = String(plan[field] ?? 0);
  });

  FLAG_FIELDS.forEach(([field]) => {
    form[field] = Boolean(plan[field]);
  });

  return form;
}

// Only the fields the operator actually moved, so the audit entry reads as the
// change that was made rather than as every number on the screen.
function changedValues(plan, form) {
  const values = {};

  if (form.name.trim() !== String(plan.name || "")) {
    values.name = form.name.trim();
  }

  NUMERIC_FIELDS.forEach(([field]) => {
    const next = Number(form[field]);
    const current = Number(plan[field] ?? 0);

    if (Number.isFinite(next) && next !== current) {
      values[field] = next;
    }
  });

  FLAG_FIELDS.forEach(([field]) => {
    if (Boolean(form[field]) !== Boolean(plan[field])) {
      values[field] = form[field];
    }
  });

  return values;
}

function enabledFeatures(plan) {
  return FLAG_FIELDS.filter(([field]) => plan[field]).map(([, , short]) => short);
}


function NumericField({ idPrefix, field, label, step, value, onChange }) {
  return (
    <label className="sa-field" htmlFor={`${idPrefix}-${field}`}>
      <span>{label}</span>

      <input
        id={`${idPrefix}-${field}`}
        type="number"
        min={0}
        step={step}
        value={value}
        onChange={(event) => onChange(field, event.target.value)}
        required
      />
    </label>
  );
}

function FlagSwitches({ form, onChange }) {
  return (
    <div className="sa-switch-grid">
      {FLAG_FIELDS.map(([field, label]) => (
        <label key={field} className="sa-switch">
          <input
            type="checkbox"
            checked={Boolean(form[field])}
            onChange={(event) => onChange(field, event.target.checked)}
          />
          <span className="sa-switch-track" aria-hidden="true" />
          <span>{label}</span>
        </label>
      ))}
    </div>
  );
}


export default function PlansPage() {
  const [plans, setPlans] = useState([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState("");

  const [companies, setCompanies] = useState([]);
  const [companiesError, setCompaniesError] = useState("");

  const [createForm, setCreateForm] = useState(null);
  const [createBusy, setCreateBusy] = useState(false);
  const [createError, setCreateError] = useState("");

  const [editForm, setEditForm] = useState(null);
  const [editBusy, setEditBusy] = useState(false);
  const [editError, setEditError] = useState("");

  const [status, setStatus] = useState("");

  const loadPlans = useCallback(async () => {
    setLoading(true);
    setError("");

    try {
      const result = await listPlansRequest();
      setPlans(Array.isArray(result?.items) ? result.items : []);
    } catch (requestError) {
      setError(requestError.message || "Plans could not be loaded.");
      setPlans([]);
    } finally {
      setLoading(false);
    }
  }, []);

  /*
   * Which companies are on which plan, read from the same list the Companies
   * page shows: a company's plan there is its latest subscription, so a count
   * taken from it is the number of businesses an edit moves right now.
   */
  const loadCompanies = useCallback(async () => {
    setCompaniesError("");

    try {
      const result = await listCompaniesRequest();
      setCompanies(Array.isArray(result?.items) ? result.items : []);
    } catch (requestError) {
      setCompanies([]);
      setCompaniesError(
        requestError.message ||
          "Companies could not be loaded, so this page cannot say who is on each plan.",
      );
    }
  }, []);

  useEffect(() => {
    loadPlans();
    loadCompanies();
  }, [loadPlans, loadCompanies]);

  const companiesByPlan = useMemo(() => {
    const grouped = new Map();

    companies.forEach((company) => {
      const code = company.plan_code;

      if (!code) {
        return;
      }

      if (!grouped.has(code)) {
        grouped.set(code, []);
      }

      grouped.get(code).push(company);
    });

    return grouped;
  }, [companies]);

  const editingPlan = useMemo(
    () => plans.find((plan) => plan.code === editForm?.code) || null,
    [plans, editForm],
  );

  const pendingEdit = useMemo(
    () => (editingPlan && editForm ? changedValues(editingPlan, editForm) : {}),
    [editingPlan, editForm],
  );

  function updateCreate(field, value) {
    setCreateForm((current) => (current ? { ...current, [field]: value } : current));
  }

  function updateEdit(field, value) {
    setEditForm((current) => (current ? { ...current, [field]: value } : current));
  }

  function startCreate() {
    setEditForm(null);
    setEditError("");
    setCreateError("");
    setStatus("");
    setCreateForm(emptyForm());
  }

  function startEdit(plan) {
    setCreateForm(null);
    setCreateError("");
    setEditError("");
    setStatus("");
    setEditForm(formFromPlan(plan));
  }

  async function handleCreate(event) {
    event.preventDefault();

    setCreateBusy(true);
    setCreateError("");
    setStatus("");

    const values = {};

    NUMERIC_FIELDS.forEach(([field]) => {
      values[field] = Number(createForm[field]);
    });

    FLAG_FIELDS.forEach(([field]) => {
      values[field] = Boolean(createForm[field]);
    });

    try {
      const created = await createPlanRequest({
        code: createForm.code.trim().toLowerCase(),
        name: createForm.name.trim(),
        values,
      });

      setCreateForm(null);
      setStatus(`Plan ${created?.name || created?.code} created.`);
      await loadPlans();
    } catch (requestError) {
      // Verbatim: the service answers with the field it refused and why.
      setCreateError(requestError.message || "The plan could not be created.");
    } finally {
      setCreateBusy(false);
    }
  }

  async function handleEdit(event) {
    event.preventDefault();

    setEditBusy(true);
    setEditError("");
    setStatus("");

    try {
      const saved = await updatePlanRequest(editForm.code, pendingEdit);

      setEditForm(null);
      setStatus(`Plan ${saved?.name || saved?.code} saved.`);
      await loadPlans();
    } catch (requestError) {
      setEditError(requestError.message || "The plan could not be saved.");
    } finally {
      setEditBusy(false);
    }
  }

  const editingCompanies = editingPlan
    ? companiesByPlan.get(editingPlan.code) || []
    : [];

  return (
    <ConsolePage
      eyebrow="CONTROL PLANE"
      title="Plans"
      description="The commercial offer: what each plan costs, what it allows, and how many companies are on it."
      actions={
        <>
          <ConsoleButton
            onClick={() => {
              loadPlans();
              loadCompanies();
            }}
          >
            <RefreshOutlined fontSize="small" />
            Refresh
          </ConsoleButton>

          <ConsoleButton variant="primary" onClick={startCreate}>
            <AddOutlined fontSize="small" />
            New plan
          </ConsoleButton>
        </>
      }
    >
      <ConsolePanel>
        <div className="sa-toolbar">
          <span className="sa-count">
            {plans.length} {plans.length === 1 ? "plan" : "plans"}
          </span>
        </div>

        <ConsoleBanner tone="error">{error}</ConsoleBanner>
        <ConsoleBanner tone="warning">{companiesError}</ConsoleBanner>
        <ConsoleBanner tone="success">{status}</ConsoleBanner>

        {loading ? <ConsoleLoading label="Loading plans..." /> : null}

        {!loading && !error && !plans.length ? (
          <ConsoleEmpty
            title="No plans yet"
            description="Create the first plan to put companies on."
          />
        ) : null}

        {!loading && plans.length ? (
          <div className="sa-table-scroll">
            <table className="sa-table">
              <thead>
                <tr>
                  <th>Plan</th>
                  <th className="is-numeric">Price</th>
                  <th className="is-numeric">Users</th>
                  <th className="is-numeric">Channels</th>
                  <th className="is-numeric">AI messages</th>
                  <th className="is-numeric">Knowledge</th>
                  <th>Add-ons</th>
                  <th className="is-numeric">On this plan</th>
                  <th aria-label="Actions" />
                </tr>
              </thead>

              <tbody>
                {plans.map((plan) => {
                  const onPlan = companiesByPlan.get(plan.code) || [];
                  const features = enabledFeatures(plan);

                  return (
                    <tr key={plan.code}>
                      <td>
                        <strong>{plan.name}</strong>
                        <span className="sa-subtle">{plan.code}</span>
                      </td>

                      <td className="is-numeric">{formatCount(plan.price_monthly)}</td>
                      <td className="is-numeric">{formatCount(plan.max_users)}</td>
                      <td className="is-numeric">
                        {formatCount(plan.max_channel_accounts)}
                      </td>
                      <td className="is-numeric">{formatCount(plan.max_ai_messages)}</td>
                      <td className="is-numeric">
                        {formatCount(plan.max_knowledge_items)}
                      </td>

                      <td>
                        {features.length ? (
                          features.join(" · ")
                        ) : (
                          <span className="sa-subtle">No add-ons</span>
                        )}
                      </td>

                      <td className="is-numeric">
                        {companiesError ? (
                          <span className="sa-subtle">Unknown</span>
                        ) : (
                          <>
                            <span>{formatCount(onPlan.length)}</span>
                            {onPlan.length ? (
                              <span className="sa-subtle">
                                {onPlan
                                  .slice(0, 3)
                                  .map((company) => company.name)
                                  .join(", ")}
                                {onPlan.length > 3
                                  ? ` +${onPlan.length - 3} more`
                                  : ""}
                              </span>
                            ) : null}
                          </>
                        )}
                      </td>

                      <td>
                        <ConsoleButton onClick={() => startEdit(plan)}>
                          <EditOutlined fontSize="small" />
                          Edit
                        </ConsoleButton>
                      </td>
                    </tr>
                  );
                })}
              </tbody>
            </table>
          </div>
        ) : null}

        <p className="sa-note">
          A company&apos;s plan is set on that company&apos;s screen. Editing a
          plan here changes the allowance of every company already on it.
        </p>
      </ConsolePanel>

      {createForm ? (
        <form onSubmit={handleCreate}>
          <ConsolePanel
            title="New plan"
            description="A plan nobody is on yet. Companies are moved onto it one at a time from their own screens."
            actions={
              <ConsoleButton onClick={() => setCreateForm(null)}>
                <CloseOutlined fontSize="small" />
                Cancel
              </ConsoleButton>
            }
          >
            <div className="sa-field-grid">
              <label className="sa-field" htmlFor="sa-plan-new-code">
                <span>Code</span>

                <input
                  id="sa-plan-new-code"
                  type="text"
                  value={createForm.code}
                  pattern={CODE_PATTERN}
                  minLength={2}
                  maxLength={40}
                  spellCheck={false}
                  title="Lower-case letters, digits, hyphens and underscores; starts with a letter or digit."
                  onChange={(event) =>
                    updateCreate("code", event.target.value.toLowerCase())
                  }
                  required
                />

                <small>
                  Lower-case letters, digits, hyphens and underscores. Permanent:
                  every subscription points at a plan by its code, so it cannot
                  be changed afterwards.
                </small>
              </label>

              <label className="sa-field" htmlFor="sa-plan-new-name">
                <span>Name</span>

                <input
                  id="sa-plan-new-name"
                  type="text"
                  value={createForm.name}
                  minLength={1}
                  maxLength={120}
                  onChange={(event) => updateCreate("name", event.target.value)}
                  required
                />

                <small>What operators see in the plan lists. Editable later.</small>
              </label>

              {NUMERIC_FIELDS.map(([field, label, step]) => (
                <NumericField
                  key={field}
                  idPrefix="sa-plan-new"
                  field={field}
                  label={label}
                  step={step}
                  value={createForm[field]}
                  onChange={updateCreate}
                />
              ))}
            </div>

            <p className="sa-note">
              Allowances are ceilings, and 0 means none of that resource.
            </p>

            <FlagSwitches form={createForm} onChange={updateCreate} />

            <ConsoleBanner tone="error">{createError}</ConsoleBanner>

            <div className="sa-form-actions">
              <ConsoleButton type="submit" variant="primary" loading={createBusy}>
                Create plan
              </ConsoleButton>
            </div>
          </ConsolePanel>
        </form>
      ) : null}

      {editForm && editingPlan ? (
        <form onSubmit={handleEdit}>
          <ConsolePanel
            title={`Edit ${editingPlan.name}`}
            description={`Code ${editingPlan.code} · cannot be changed`}
            actions={
              <ConsoleButton onClick={() => setEditForm(null)}>
                <CloseOutlined fontSize="small" />
                Cancel
              </ConsoleButton>
            }
          >
            <p className="sa-note is-strong">
              {companiesError
                ? "Saving changes this plan for every company on it, and this page could not read which companies those are."
                : editingCompanies.length
                  ? `Saving moves every company on this plan. ${formatCount(
                      editingCompanies.length,
                    )} ${
                      editingCompanies.length === 1 ? "company is" : "companies are"
                    } on ${editingPlan.name} right now, and ${
                      editingCompanies.length === 1 ? "its" : "their"
                    } allowances change the moment you save. To accommodate one customer, set a per-company override on that company instead.`
                  : `No company is on ${editingPlan.name} right now, so this edit moves nobody.`}
            </p>

            {editingCompanies.length ? (
              <p className="sa-note">
                Moving with this edit:{" "}
                {editingCompanies
                  .map(
                    (company) =>
                      `${company.name}${company.plan_active ? "" : " (lapsed)"}`,
                  )
                  .join(", ")}
                .
              </p>
            ) : null}

            <div className="sa-field-grid">
              <label className="sa-field" htmlFor="sa-plan-edit-name">
                <span>Name</span>

                <input
                  id="sa-plan-edit-name"
                  type="text"
                  value={editForm.name}
                  minLength={1}
                  maxLength={120}
                  onChange={(event) => updateEdit("name", event.target.value)}
                  required
                />
              </label>

              {NUMERIC_FIELDS.map(([field, label, step]) => (
                <NumericField
                  key={field}
                  idPrefix="sa-plan-edit"
                  field={field}
                  label={label}
                  step={step}
                  value={editForm[field]}
                  onChange={updateEdit}
                />
              ))}
            </div>

            <FlagSwitches form={editForm} onChange={updateEdit} />

            <ConsoleBanner tone="error">{editError}</ConsoleBanner>

            <div className="sa-form-actions">
              <ConsoleButton
                type="submit"
                variant="primary"
                loading={editBusy}
                disabled={!Object.keys(pendingEdit).length}
              >
                Save plan
              </ConsoleButton>
            </div>

            <p className="sa-note">
              {Object.keys(pendingEdit).length
                ? `Sending: ${Object.keys(pendingEdit).sort().join(", ")}.`
                : "Nothing has been changed yet."}
            </p>
          </ConsolePanel>
        </form>
      ) : null}
    </ConsolePage>
  );
}
