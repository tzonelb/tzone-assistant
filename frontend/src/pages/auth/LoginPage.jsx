import { useState } from "react";
import { Link, Navigate, useLocation, useNavigate } from "react-router-dom";

import { useAuth } from "../../contexts/AuthContext";
import { getServerUrl, saveServerUrl } from "../../api/client";
import tzoneLogo from "../../assets/tzone-logo.png";

const IS_DESKTOP = typeof window !== "undefined" && Boolean(window.tzoneDesktop);

// The Android/iOS app needs the same runtime server switch as desktop —
// the APK ships with a baked-in default that must be changeable when the
// real server goes live, without republishing the app.
const IS_NATIVE_APP =
  typeof window !== "undefined" &&
  Boolean(window.Capacitor?.isNativePlatform?.());

const SHOW_SERVER_SETTINGS = IS_DESKTOP || IS_NATIVE_APP;


export default function LoginPage() {
  const { authenticated, loading, login, verifyTwoFactor } = useAuth();

  const navigate = useNavigate();
  const location = useLocation();

  const [company, setCompany] = useState("");
  const [email, setEmail] = useState("");
  const [password, setPassword] = useState("");
  const [showPassword, setShowPassword] = useState(false);
  const [submitting, setSubmitting] = useState(false);
  const [error, setError] = useState("");
  const [pendingToken, setPendingToken] = useState("");
  const [twoFactorCode, setTwoFactorCode] = useState("");
  const [showServerSettings, setShowServerSettings] = useState(false);
  const [serverUrl, setServerUrl] = useState(() => getServerUrl());

  if (!loading && authenticated) {
    return <Navigate to="/dashboard" replace />;
  }

  function goToDashboard() {
    const destination = location.state?.from || "/dashboard";
    navigate(destination, { replace: true });
  }

  async function handleSubmit(event) {
    event.preventDefault();

    setError("");
    setSubmitting(true);

    try {
      const result = await login(
        company.trim(),
        email.trim(),
        password,
      );

      if (result?.twofa_required) {
        setPendingToken(result.pending_token);
        setTwoFactorCode("");
        return;
      }

      goToDashboard();
    } catch (loginError) {
      setError(
        loginError.message ||
        "Login failed. Please check your information.",
      );
    } finally {
      setSubmitting(false);
    }
  }

  function handleSaveServerUrl() {
    saveServerUrl(serverUrl);
    // API base URL is read once at app start, so apply it with a reload.
    window.location.reload();
  }

  async function handleTwoFactorSubmit(event) {
    event.preventDefault();

    setError("");
    setSubmitting(true);

    try {
      await verifyTwoFactor(pendingToken, twoFactorCode.trim());
      goToDashboard();
    } catch (verifyError) {
      setError(
        verifyError.message ||
        "That code was not accepted. Please try again.",
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

        {pendingToken ? (
          <form className="login-form" onSubmit={handleTwoFactorSubmit}>
            <label htmlFor="login-2fa-code">
              Authentication code
            </label>
            <p className="login-security-note">
              Enter the 6-digit code from your authenticator app.
            </p>
            <input
              id="login-2fa-code"
              type="text"
              inputMode="numeric"
              autoComplete="one-time-code"
              maxLength={6}
              value={twoFactorCode}
              placeholder="123456"
              onChange={(event) =>
                setTwoFactorCode(event.target.value.replace(/\D/g, ""))
              }
              required
            />

            {error ? (
              <div className="login-error">{error}</div>
            ) : null}

            <button
              type="submit"
              className="primary-action"
              disabled={submitting || twoFactorCode.length !== 6}
            >
              {submitting ? "Verifying..." : "Verify and sign in"}
            </button>

            <button
              type="button"
              className="secondary-action"
              onClick={() => {
                setPendingToken("");
                setTwoFactorCode("");
                setError("");
              }}
            >
              Back
            </button>
          </form>
        ) : (
        <form
          className="login-form"
          onSubmit={handleSubmit}
        >
          <label htmlFor="login-company">
            Company name or workspace code
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
        )}

        <small className="login-security-note">
          Don&apos;t have an account?{" "}
          <Link to="/signup">Create an account</Link>
        </small>

        {SHOW_SERVER_SETTINGS ? (
          <div className="login-server-settings">
            <button
              type="button"
              className="secondary-action"
              onClick={() =>
                setShowServerSettings((current) => !current)
              }
            >
              {showServerSettings
                ? "Hide server settings"
                : "Server settings"}
            </button>

            {showServerSettings ? (
              <>
                <label htmlFor="login-server-url">
                  Server address
                </label>
                <input
                  id="login-server-url"
                  type="url"
                  value={serverUrl}
                  placeholder="http://127.0.0.1:8000"
                  onChange={(event) =>
                    setServerUrl(event.target.value)
                  }
                />
                <button
                  type="button"
                  className="primary-action"
                  onClick={handleSaveServerUrl}
                >
                  Save and reconnect
                </button>
              </>
            ) : null}
          </div>
        ) : null}

        <small className="login-security-note">
          Protected access · T-ZONE Platform
        </small>
      </section>
    </main>
  );
}