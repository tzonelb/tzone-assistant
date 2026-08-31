import { useState } from "react";
import { Link } from "react-router-dom";

import { forgotPasswordRequest } from "../../api/client";


export default function ForgotPasswordPage() {
  const [email, setEmail] = useState("");
  const [submitting, setSubmitting] = useState(false);
  const [sent, setSent] = useState(false);
  const [message, setMessage] = useState("");

  async function handleSubmit(event) {
    event.preventDefault();

    setSubmitting(true);

    try {
      const result = await forgotPasswordRequest(email.trim());
      setMessage(
        result?.message ||
        "If that email is registered, a reset link is on its way.",
      );
    } catch {
      // The endpoint answers the same way whether or not the address exists, so
      // there is nothing useful to show on failure either -- keep it uniform.
      setMessage(
        "If that email is registered, a reset link is on its way.",
      );
    } finally {
      setSent(true);
      setSubmitting(false);
    }
  }

  return (
    <main className="login-page">
      <section className="login-presentation">
        <div className="login-logo">T</div>

        <div>
          <span className="login-kicker">
            PASSWORD RESET
          </span>

          <h1>
            Forgot your password? We&apos;ll email you a link.
          </h1>

          <p>
            Enter the email you sign in with. If it belongs to an account, a
            single-use link to set a new password is on its way.
          </p>
        </div>
      </section>

      <section className="login-card">
        <div className="login-card-header">
          <span>SECURE COMPANY PORTAL</span>
          <h2>Reset your password</h2>
          <p>
            We send a link that works once and expires shortly.
          </p>
        </div>

        {sent ? (
          <div className="login-notice" role="status">
            {message}
          </div>
        ) : (
          <form
            className="login-form"
            onSubmit={handleSubmit}
          >
            <label htmlFor="forgot-email">
              Email address
            </label>

            <input
              id="forgot-email"
              type="email"
              value={email}
              placeholder="name@company.com"
              autoComplete="email"
              onChange={(event) => setEmail(event.target.value)}
              required
            />

            <button
              type="submit"
              className="primary-action"
              disabled={submitting}
            >
              {submitting ? "Sending..." : "Send reset link"}
            </button>
          </form>
        )}

        <div className="login-form-row">
          <Link className="login-link" to="/login">
            Back to sign in
          </Link>
        </div>

        <small className="login-security-note">
          Protected access · T-ZONE Platform
        </small>
      </section>
    </main>
  );
}
