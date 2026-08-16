import { useCallback, useEffect, useState } from "react";
import { useNavigate } from "react-router-dom";
import { ArrowBackOutlined } from "@mui/icons-material";

import {
  CONSOLE_BASE_PATH,
  createCompanyRequest,
  listPlansRequest,
} from "../platformClient";
import {
  ConsoleBanner,
  ConsoleButton,
  ConsolePage,
  ConsolePanel,
  WorkspaceCodeReveal,
} from "../components/ConsoleUI";


const MIN_OWNER_PASSWORD = 10;

// Mirrors PlatformService.slugify so the suggested slug is the one the server
// would derive anyway; the field stays editable because the server has the last
// word on collisions.
function slugify(value) {
  return String(value || "")
    .trim()
    .toLowerCase()
    .replace(/[^a-z0-9]+/g, "-")
    .replace(/^-+|-+$/g, "");
}

function emptyForm() {
  return {
    name: "",
    slug: "",
    workspace: "",
    owner_name: "",
    owner_email: "",
    owner_password: "",
    country: "",
    currency: "USD",
    timezone: "Asia/Beirut",
    language: "ar",
    plan_code: "",
  };
}


export default function NewCompanyPage() {
  const navigate = useNavigate();

  const [form, setForm] = useState(emptyForm);
  const [slugTouched, setSlugTouched] = useState(false);
  const [plans, setPlans] = useState([]);
  const [submitting, setSubmitting] = useState(false);
  const [error, setError] = useState("");
  const [created, setCreated] = useState(null);

  const loadPlans = useCallback(async () => {
    try {
      const result = await listPlansRequest();
      setPlans(Array.isArray(result?.items) ? result.items : []);
    } catch {
      // A plan is optional at creation, so a failure here must not block the
      // form; it can be assigned from the company screen afterwards.
      setPlans([]);
    }
  }, []);

  useEffect(() => {
    loadPlans();
  }, [loadPlans]);

  function update(key, value) {
    setForm((current) => ({ ...current, [key]: value }));
  }

  async function handleSubmit(event) {
    event.preventDefault();

    setSubmitting(true);
    setError("");

    try {
      const result = await createCompanyRequest({
        name: form.name.trim(),
        slug: (slugTouched ? form.slug : slugify(form.name)).trim(),
        workspace: form.workspace.trim(),
        owner_email: form.owner_email.trim(),
        owner_name: form.owner_name.trim(),
        owner_password: form.owner_password,
        country: form.country.trim() || null,
        currency: form.currency.trim() || "USD",
        timezone: form.timezone.trim() || "Asia/Beirut",
        language: form.language.trim() || "ar",
        plan_code: form.plan_code || null,
      });

      setCreated(result);
    } catch (requestError) {
      setError(requestError.message || "The company could not be created.");
    } finally {
      setSubmitting(false);
    }
  }

  if (created) {
    return (
      <ConsolePage
        eyebrow="COMPANY CREATED"
        title={created.name}
        description={`Slug ${created.slug} · owner ${created.owner_email}`}
      >
        <ConsolePanel title="Hand this code to the company">
          <WorkspaceCodeReveal
            code={created.workspace_code}
            notice={created.workspace_code_notice}
          >
            <ConsoleButton
              onClick={() =>
                navigate(`${CONSOLE_BASE_PATH}/companies/${created.company_id}`)
              }
            >
              Open the company
            </ConsoleButton>
          </WorkspaceCodeReveal>

          <p className="sa-note">
            {created.owner_user_created
              ? "A new owner account was created with the password you entered."
              : "An existing user account was made the owner; its password was not changed."}
          </p>
        </ConsolePanel>
      </ConsolePage>
    );
  }

  return (
    <ConsolePage
      eyebrow="CONTROL PLANE"
      title="New company"
      description="Provisions the company, its encrypted database, its roles and its owner account."
      actions={
        <ConsoleButton onClick={() => navigate(`${CONSOLE_BASE_PATH}/companies`)}>
          <ArrowBackOutlined fontSize="small" />
          Companies
        </ConsoleButton>
      }
    >
      <form onSubmit={handleSubmit}>
        <ConsolePanel title="Company">
          <div className="sa-field-grid">
            <label className="sa-field" htmlFor="sa-company-name">
              <span>Company name</span>

              <input
                id="sa-company-name"
                type="text"
                value={form.name}
                minLength={2}
                maxLength={120}
                onChange={(event) => update("name", event.target.value)}
                required
              />
            </label>

            <label className="sa-field" htmlFor="sa-company-slug">
              <span>Slug</span>

              <input
                id="sa-company-slug"
                type="text"
                value={slugTouched ? form.slug : slugify(form.name)}
                minLength={2}
                maxLength={120}
                spellCheck={false}
                onChange={(event) => {
                  setSlugTouched(true);
                  update("slug", event.target.value);
                }}
                required
              />
            </label>

            <label className="sa-field" htmlFor="sa-company-workspace">
              <span>Workspace name</span>

              <input
                id="sa-company-workspace"
                type="text"
                value={form.workspace}
                minLength={2}
                maxLength={120}
                onChange={(event) => update("workspace", event.target.value)}
                required
              />
            </label>

            <label className="sa-field" htmlFor="sa-company-country">
              <span>Country</span>

              <input
                id="sa-company-country"
                type="text"
                value={form.country}
                maxLength={60}
                onChange={(event) => update("country", event.target.value)}
              />
            </label>

            <label className="sa-field" htmlFor="sa-company-currency">
              <span>Currency</span>

              <input
                id="sa-company-currency"
                type="text"
                value={form.currency}
                maxLength={10}
                onChange={(event) => update("currency", event.target.value)}
              />
            </label>

            <label className="sa-field" htmlFor="sa-company-timezone">
              <span>Timezone</span>

              <input
                id="sa-company-timezone"
                type="text"
                value={form.timezone}
                maxLength={60}
                onChange={(event) => update("timezone", event.target.value)}
              />
            </label>

            <label className="sa-field" htmlFor="sa-company-language">
              <span>Default language</span>

              <input
                id="sa-company-language"
                type="text"
                value={form.language}
                maxLength={10}
                onChange={(event) => update("language", event.target.value)}
              />
            </label>

            <label className="sa-field" htmlFor="sa-company-plan">
              <span>Plan (optional)</span>

              <select
                id="sa-company-plan"
                value={form.plan_code}
                onChange={(event) => update("plan_code", event.target.value)}
              >
                <option value="">No plan</option>

                {plans.map((plan) => (
                  <option key={plan.code} value={plan.code}>
                    {plan.name} — {plan.code}
                  </option>
                ))}
              </select>
            </label>
          </div>
        </ConsolePanel>

        <ConsolePanel
          title="Owner account"
          description="The first employee of the company, created with the owner role."
        >
          <div className="sa-field-grid">
            <label className="sa-field" htmlFor="sa-owner-name">
              <span>Owner name</span>

              <input
                id="sa-owner-name"
                type="text"
                value={form.owner_name}
                minLength={2}
                maxLength={120}
                onChange={(event) => update("owner_name", event.target.value)}
                required
              />
            </label>

            <label className="sa-field" htmlFor="sa-owner-email">
              <span>Owner email</span>

              <input
                id="sa-owner-email"
                type="email"
                value={form.owner_email}
                onChange={(event) => update("owner_email", event.target.value)}
                required
              />
            </label>

            <label className="sa-field" htmlFor="sa-owner-password">
              <span>Owner password</span>

              <input
                id="sa-owner-password"
                type="password"
                value={form.owner_password}
                minLength={MIN_OWNER_PASSWORD}
                maxLength={200}
                autoComplete="new-password"
                onChange={(event) => update("owner_password", event.target.value)}
                required
              />

              <small>At least {MIN_OWNER_PASSWORD} characters.</small>
            </label>
          </div>

          <ConsoleBanner tone="error">{error}</ConsoleBanner>

          <div className="sa-form-actions">
            <ConsoleButton type="submit" variant="primary" loading={submitting}>
              Create company
            </ConsoleButton>
          </div>

          <p className="sa-note">
            The workspace code is generated during provisioning and shown once
            on the next screen.
          </p>
        </ConsolePanel>
      </form>
    </ConsolePage>
  );
}
