import { useState } from "react";
import { useNavigate, useParams } from "react-router-dom";

import { resetPasswordRequest } from "../../api/client";


const MIN_PASSWORD_LENGTH = 10;


export default function ResetPasswordPage() {
  const { token } = useParams();
  const navigate = useNavigate();

  const [newPassword, setNewPassword] = useState("");
  const [confirmPassword, setConfirmPassword] = useState("");
  const [showPassword, setShowPassword] = useState(false);
  const [submitting, setSubmitting] = useState(false);
  const [error, setError] = useState("");

  async function handleSubmit(event) {
    event.preventDefault();

    if (newPassword !== confirmPassword) {
      setError("The two passwords do not match.");
      return;
    }

    setError("");
    setSubmitting(true);

    try {
      const result = await resetPasswordRequest(token, newPassword);

      navigate("/login", {
        replace: true,
        state: {
          notice: result?.message || "Password set. You can sign in now.",
        },
      });
    } catch (resetError) {
      setError(
        resetError.message ||
        "The password could not be set.",
      );
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
            Set a new password for your account.
          </h1>

          <p>
            This link works once and expires. Setting a password here also
            clears a lockout, so you can sign in straight afterwards.
          </p>
        </div>
      </section>

      <section className="login-card">
        <div className="login-card-header">
          <span>SECURE COMPANY PORTAL</span>
          <h2>New password</h2>
          <p>
            Choose a password only you know. You will sign in with it on the
            next screen.
          </p>
        </div>

        <form
          className="login-form"
          onSubmit={handleSubmit}
        >
          <label htmlFor="reset-new-password">
            New password
          </label>

          <div className="password-control">
            <input
              id="reset-new-password"
              type={showPassword ? "text" : "password"}
              value={newPassword}
              placeholder={`At least ${MIN_PASSWORD_LENGTH} characters`}
              autoComplete="new-password"
              minLength={MIN_PASSWORD_LENGTH}
              onChange={(event) =>
                setNewPassword(event.target.value)
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

          <label htmlFor="reset-confirm-password">
            Repeat new password
          </label>

          <input
            id="reset-confirm-password"
            type={showPassword ? "text" : "password"}
            value={confirmPassword}
            placeholder="Type it again"
            autoComplete="new-password"
            minLength={MIN_PASSWORD_LENGTH}
            onChange={(event) =>
              setConfirmPassword(event.target.value)
            }
            required
          />

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
              ? "Setting password..."
              : "Set password"}
          </button>
        </form>

        <small className="login-security-note">
          Protected access · T-ZONE Platform
        </small>
      </section>
    </main>
  );
}
