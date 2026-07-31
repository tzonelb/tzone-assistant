import { useEffect, useState } from "react";
import { Link, Navigate, useNavigate } from "react-router-dom";

import { useAuth } from "../../contexts/AuthContext";
import { saveAccessToken, signupPlansRequest, signupRequest } from "../../api/client";
import tzoneLogo from "../../assets/tzone-logo.png";


function formatPrice(plan) {
  const price = Number(plan.price_monthly || 0);
  if (price <= 0) {
    return "Free";
  }
  const currency = plan.currency || "USD";
  return `${currency} ${price}/mo`;
}


export default function SignupPage() {
  const { authenticated, loading, refreshUser } = useAuth();
  const navigate = useNavigate();

  const [companyName, setCompanyName] = useState("");
  const [fullName, setFullName] = useState("");
  const [email, setEmail] = useState("");
  const [password, setPassword] = useState("");
  const [showPassword, setShowPassword] = useState(false);

  const [plans, setPlans] = useState([]);
  const [selectedPlanId, setSelectedPlanId] = useState(null);

  const [submitting, setSubmitting] = useState(false);
  const [error, setError] = useState("");

  useEffect(() => {
    let active = true;

    signupPlansRequest()
      .then((result) => {
        if (!active) return;

        const list = result?.plans || [];
        setPlans(list);

        if (list.length) {
          // Preselect the cheapest *paid* plan (the intended entry tier) —
          // a $0 plan is usually a mis-seeded/"custom" tier and shouldn't be
          // the silent default. Fall back to the first plan if all are free.
          const paid = list.filter((plan) => Number(plan.price_monthly || 0) > 0);
          setSelectedPlanId((paid[0] || list[0]).id);
        }
      })
      .catch(() => {
        // A plan is optional for signup; the page still works without them.
      });

    return () => {
      active = false;
    };
  }, []);

  if (!loading && authenticated) {
    return <Navigate to="/dashboard" replace />;
  }

  async function handleSubmit(event) {
    event.preventDefault();

    setError("");
    setSubmitting(true);

    try {
      const result = await signupRequest({
        company_name: companyName.trim(),
        owner_full_name: fullName.trim(),
        owner_email: email.trim(),
        password,
        plan_id: selectedPlanId,
      });

      if (!result?.access_token) {
        throw new Error("The server did not return an access token.");
      }

      saveAccessToken(result.access_token);
      await refreshUser();

      navigate("/dashboard", { replace: true });
    } catch (signupError) {
      setError(
        signupError.message ||
        "Sign-up failed. Please check your information and try again.",
      );
    } finally {
      setSubmitting(false);
    }
  }

  return (
    <main className="login-page">
      <section className="login-presentation">
        <img src={tzoneLogo} alt="T-ZONE" className="login-logo" />

        <div>
          <span className="login-kicker">
            AI BUSINESS PLATFORM
          </span>

          <h1>
            Launch your workspace in minutes.
          </h1>

          <p>
            Create your company account, invite your team, and start
            managing every customer conversation, appointment and
            automation from one secure platform.
          </p>
        </div>
      </section>

      <section className="login-card">
        <div className="login-card-header">
          <span>CREATE YOUR COMPANY</span>
          <h2>Start free</h2>
          <p>
            Set up your workspace and owner account to get started.
          </p>
        </div>

        <form className="login-form" onSubmit={handleSubmit}>
          <label htmlFor="signup-company">
            Company name
          </label>

          <input
            id="signup-company"
            type="text"
            value={companyName}
            placeholder="Acme Widgets"
            autoComplete="organization"
            onChange={(event) => setCompanyName(event.target.value)}
            required
          />

          <label htmlFor="signup-name">
            Your full name
          </label>

          <input
            id="signup-name"
            type="text"
            value={fullName}
            placeholder="Ada Lovelace"
            autoComplete="name"
            onChange={(event) => setFullName(event.target.value)}
            required
          />

          <label htmlFor="signup-email">
            Work email
          </label>

          <input
            id="signup-email"
            type="email"
            value={email}
            placeholder="name@company.com"
            autoComplete="email"
            onChange={(event) => setEmail(event.target.value)}
            required
          />

          <label htmlFor="signup-password">
            Password
          </label>

          <div className="password-control">
            <input
              id="signup-password"
              type={showPassword ? "text" : "password"}
              value={password}
              placeholder="At least 8 characters"
              autoComplete="new-password"
              minLength={8}
              onChange={(event) => setPassword(event.target.value)}
              required
            />

            <button
              type="button"
              onClick={() => setShowPassword((current) => !current)}
            >
              {showPassword ? "Hide" : "Show"}
            </button>
          </div>

          {plans.length ? (
            <>
              <label>Choose a plan</label>

              <div className="signup-plan-grid">
                {plans.map((plan) => {
                  const selected = plan.id === selectedPlanId;

                  return (
                    <button
                      type="button"
                      key={plan.id}
                      className={
                        selected
                          ? "signup-plan-card is-selected"
                          : "signup-plan-card"
                      }
                      onClick={() => setSelectedPlanId(plan.id)}
                      aria-pressed={selected}
                    >
                      <span className="signup-plan-name">{plan.name}</span>
                      <span className="signup-plan-price">
                        {formatPrice(plan)}
                      </span>
                      <span className="signup-plan-limits">
                        {plan.max_users} users · {plan.max_channel_accounts} channels
                      </span>
                    </button>
                  );
                })}
              </div>
            </>
          ) : null}

          {error ? (
            <div className="login-error">
              {error}
            </div>
          ) : null}

          <button
            type="submit"
            className="primary-action"
            disabled={submitting}
          >
            {submitting ? "Creating your workspace..." : "Create account"}
          </button>
        </form>

        <small className="login-security-note">
          Already have an account?{" "}
          <Link to="/login">Sign in</Link>
        </small>
      </section>
    </main>
  );
}
