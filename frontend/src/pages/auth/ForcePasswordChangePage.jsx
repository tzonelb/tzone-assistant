import { useState } from "react";
import { useNavigate } from "react-router-dom";

import { changeOwnPasswordRequest } from "../../api/client";
import { useAuth } from "../../contexts/AuthContext";


const MIN_PASSWORD_LENGTH = 10;


export default function ForcePasswordChangePage() {
  const { endLocalSession } = useAuth();
  const navigate = useNavigate();

  const [currentPassword, setCurrentPassword] = useState("");
  const [newPassword, setNewPassword] = useState("");
  const [confirmPassword, setConfirmPassword] = useState("");
  const [showPasswords, setShowPasswords] = useState(false);
  const [submitting, setSubmitting] = useState(false);
  const [error, setError] = useState("");

  async function handleSubmit(event) {
    event.preventDefault();

    if (newPassword !== confirmPassword) {
      setError("The two new passwords do not match.");
      return;
    }

    setError("");
    setSubmitting(true);

    try {
      const result = await changeOwnPasswordRequest(
        currentPassword,
        newPassword,
      );

      // The token that made this request was revoked by it, so there is no
      // session left to close politely — clear it here and send them to sign
      // in again.
      endLocalSession();

      navigate("/login", {
        replace: true,
        state: {
          notice:
            result?.message ||
            "Password changed. Sign in again with the new one.",
        },
      });
    } catch (changeError) {
      setError(
        changeError.message ||
        "The password could not be changed.",
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
            PASSWORD CHANGE REQUIRED
          </span>

          <h1>
            Choose a new password to continue.
          </h1>

          <p>
            An administrator at your company asked for this. The rest of the
            platform stays closed until it is done.
          </p>
        </div>
      </section>

      <section className="login-card">
        <div className="login-card-header">
          <span>YOUR ACCOUNT</span>
          <h2>New password</h2>
          <p>
            Changing your password ends every session, including this one. You
            will be signed out and asked to sign in again with the new
            password.
          </p>
        </div>

        <form
          className="login-form"
          onSubmit={handleSubmit}
        >
          <label htmlFor="change-current-password">
            Current password
          </label>

          <input
            id="change-current-password"
            type={showPasswords ? "text" : "password"}
            value={currentPassword}
            placeholder="The password you signed in with"
            autoComplete="current-password"
            onChange={(event) =>
              setCurrentPassword(event.target.value)
            }
            required
          />

          <label htmlFor="change-new-password">
            New password
          </label>

          <div className="password-control">
            <input
              id="change-new-password"
              type={showPasswords ? "text" : "password"}
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
                setShowPasswords((current) => !current)
              }
            >
              {showPasswords ? "Hide" : "Show"}
            </button>
          </div>

          <label htmlFor="change-confirm-password">
            Repeat new password
          </label>

          <input
            id="change-confirm-password"
            type={showPasswords ? "text" : "password"}
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
              ? "Changing password..."
              : "Change password and sign out"}
          </button>
        </form>

        <small className="login-security-note">
          Protected access · T-ZONE Platform
        </small>
      </section>
    </main>
  );
}
