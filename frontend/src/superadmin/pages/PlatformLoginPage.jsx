import { useState } from "react";
import { Navigate, useLocation, useNavigate } from "react-router-dom";

import { CONSOLE_BASE_PATH } from "../platformClient";
import { usePlatformAuth } from "../PlatformAuthContext";
import { ConsoleBanner } from "../components/ConsoleUI";


export default function PlatformLoginPage() {
  const { authenticated, loading, login } = usePlatformAuth();

  const navigate = useNavigate();
  const location = useLocation();

  const [email, setEmail] = useState("");
  const [password, setPassword] = useState("");
  const [showPassword, setShowPassword] = useState(false);
  const [submitting, setSubmitting] = useState(false);
  const [error, setError] = useState("");

  if (!loading && authenticated) {
    return <Navigate to={`${CONSOLE_BASE_PATH}/companies`} replace />;
  }

  async function handleSubmit(event) {
    event.preventDefault();

    setError("");
    setSubmitting(true);

    try {
      await login(email.trim(), password);
      navigate(location.state?.from || `${CONSOLE_BASE_PATH}/companies`, {
        replace: true,
      });
    } catch (loginError) {
      setError(loginError.message || "Sign in failed.");
      setSubmitting(false);
    }
  }

  return (
    <main className="sa-login">
      <section className="sa-login-panel">
        <span className="sa-eyebrow">T-ZONE PLATFORM</span>
        <h1>Control plane sign in</h1>

        <p>
          The control plane creates, configures and suspends companies. It runs
          on a separate credential from a company account and opens no company
          database.
        </p>
      </section>

      <section className="sa-login-card">
        <header>
          <h2>Super Admin</h2>
          <p>Platform administrators only.</p>
        </header>

        <form onSubmit={handleSubmit}>
          <label htmlFor="sa-login-email">Email address</label>

          <input
            id="sa-login-email"
            type="email"
            value={email}
            placeholder="admin@tzone.app"
            autoComplete="email"
            onChange={(event) => setEmail(event.target.value)}
            required
          />

          <label htmlFor="sa-login-password">Password</label>

          <div className="sa-password-control">
            <input
              id="sa-login-password"
              type={showPassword ? "text" : "password"}
              value={password}
              placeholder="Enter your password"
              autoComplete="current-password"
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

          <p className="sa-login-note">
            There is no workspace code here, and none is missing. A workspace
            code unlocks one company&apos;s encrypted database; a platform
            session never opens one, so it has nothing to unlock.
          </p>

          <ConsoleBanner tone="error">{error}</ConsoleBanner>

          <button
            type="submit"
            className="sa-button is-primary sa-login-submit"
            disabled={submitting}
          >
            {submitting ? "Signing in..." : "Sign in to the console"}
          </button>
        </form>
      </section>
    </main>
  );
}
