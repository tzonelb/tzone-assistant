import { useState } from "react";
import { Navigate, useLocation, useNavigate } from "react-router-dom";

import { useAuth } from "../../contexts/AuthContext";


export default function LoginPage() {
  const { authenticated, loading, login } = useAuth();

  const navigate = useNavigate();
  const location = useLocation();

  const [workspaceCode, setWorkspaceCode] = useState("");
  const [company, setCompany] = useState("");
  const [email, setEmail] = useState("");
  const [password, setPassword] = useState("");
  const [showPassword, setShowPassword] = useState(false);
  const [submitting, setSubmitting] = useState(false);
  const [error, setError] = useState("");

  if (!loading && authenticated) {
    return <Navigate to="/dashboard" replace />;
  }

  async function handleSubmit(event) {
    event.preventDefault();

    setError("");
    setSubmitting(true);

    try {
      await login(
        company.trim(),
        email.trim(),
        password,
        workspaceCode.trim().toUpperCase(),
      );

      const destination =
        location.state?.from || "/dashboard";

      navigate(destination, {
        replace: true,
      });
    } catch (loginError) {
      setError(
        loginError.message ||
        "Login failed. Please check your information.",
      );
    } finally {
      setSubmitting(false);
    }
  }

  return (
    <main className="login-page">
      <section className="login-presentation">
        <div className="login-logo">T</div>

        <div>
          <span className="login-kicker">
            AI BUSINESS PLATFORM
          </span>

          <h1>
            Manage every customer conversation from one place.
          </h1>

          <p>
            Messaging, artificial intelligence, knowledge,
            customers, appointments and business automation
            inside one secure platform.
          </p>
        </div>
      </section>

      <section className="login-card">
        <div className="login-card-header">
          <span>SECURE COMPANY PORTAL</span>
          <h2>Welcome back</h2>
          <p>
            Sign in with your company account to continue.
          </p>
        </div>

        <form
          className="login-form"
          onSubmit={handleSubmit}
        >
          <label htmlFor="login-workspace-code">
            Workspace code
          </label>

          <input
            id="login-workspace-code"
            type="text"
            value={workspaceCode}
            placeholder="TZ-A1B2-C3D4-E5F6"
            autoComplete="off"
            spellCheck={false}
            onChange={(event) =>
              setWorkspaceCode(event.target.value)
            }
            required
          />

          <label htmlFor="login-company">
            Company name
          </label>

          <input
            id="login-company"
            type="text"
            value={company}
            placeholder="tzone-lb"
            autoComplete="organization"
            onChange={(event) => setCompany(event.target.value)}
            required
          />

          <label htmlFor="login-email">
            Email address
          </label>

          <input
            id="login-email"
            type="email"
            value={email}
            placeholder="name@company.com"
            autoComplete="email"
            onChange={(event) =>
              setEmail(event.target.value)
            }
            required
          />

          <label htmlFor="login-password">
            Password
          </label>

          <div className="password-control">
            <input
              id="login-password"
              type={showPassword ? "text" : "password"}
              value={password}
              placeholder="Enter your password"
              autoComplete="current-password"
              onChange={(event) =>
                setPassword(event.target.value)
              }
              required
            />

            <button
              type="button"
              onClick={() =>
                setShowPassword((current) => !current)
              }
            >
              {showPassword ? "Hide" : "Show"}
            </button>
          </div>

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
            {submitting
              ? "Signing in..."
              : "Sign in"}
          </button>
        </form>

        <small className="login-security-note">
          Protected access · T-ZONE Platform
        </small>
      </section>
    </main>
  );
}