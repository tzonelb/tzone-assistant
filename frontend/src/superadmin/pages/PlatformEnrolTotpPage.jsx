import { useEffect, useRef, useState } from "react";
import { Navigate, useNavigate } from "react-router-dom";

import { CONSOLE_BASE_PATH } from "../platformClient";
import {
  platformTotpBeginRequest,
  platformTotpConfirmRequest,
} from "../platformClient";
import { usePlatformAuth } from "../PlatformAuthContext";
import { ConsoleBanner, ConsoleButton, ConsoleLoading } from "../components/ConsoleUI";


/*
 * Second-factor enrolment for the platform console.
 *
 * A super admin sign-in is one factor by design (no company, so no workspace
 * code), and it is the account that suspends companies and rotates workspace
 * codes -- so the server refuses every console route until a second factor is
 * on. This is the one screen that turns it on: scan the QR, prove the app makes
 * a code from it, and save the recovery codes that are shown exactly once.
 */
export default function PlatformEnrolTotpPage() {
  const { loading, authenticated, enrolmentPending, finishEnrolment, logout } =
    usePlatformAuth();

  const navigate = useNavigate();

  const startedRef = useRef(false);
  const [secret, setSecret] = useState("");
  const [qrSvg, setQrSvg] = useState("");
  const [beginError, setBeginError] = useState("");

  const [code, setCode] = useState("");
  const [confirming, setConfirming] = useState(false);
  const [confirmError, setConfirmError] = useState("");

  const [recoveryCodes, setRecoveryCodes] = useState(null);
  const [savedAcknowledged, setSavedAcknowledged] = useState(false);
  const [finishing, setFinishing] = useState(false);

  // Issue a secret + QR once, when the enrolment screen opens.
  useEffect(() => {
    if (!enrolmentPending || startedRef.current) {
      return;
    }

    startedRef.current = true;

    platformTotpBeginRequest()
      .then((result) => {
        setSecret(result?.secret || "");
        setQrSvg(result?.qr_svg || "");
      })
      .catch((error) => {
        setBeginError(
          error?.message || "Could not start two-factor setup. Try again.",
        );
      });
  }, [enrolmentPending]);

  // Already enrolled, or no pending session: this screen has no business
  // showing. Send the browser where it belongs.
  if (!loading && !enrolmentPending) {
    return (
      <Navigate
        to={`${CONSOLE_BASE_PATH}/${authenticated ? "companies" : "login"}`}
        replace
      />
    );
  }

  if (loading) {
    return (
      <main className="sa-fullscreen">
        <ConsoleLoading label="Preparing two-factor setup..." />
      </main>
    );
  }

  async function handleConfirm(event) {
    event.preventDefault();

    setConfirmError("");
    setConfirming(true);

    try {
      const result = await platformTotpConfirmRequest(code.trim());
      setRecoveryCodes(result?.recovery_codes || []);
    } catch (error) {
      setConfirmError(
        error?.message || "That code did not match. Try the next one.",
      );
      setConfirming(false);
    }
  }

  async function handleFinish() {
    setFinishing(true);

    try {
      await finishEnrolment();
      navigate(`${CONSOLE_BASE_PATH}/companies`, { replace: true });
    } catch {
      // The factor is on regardless; a reload will pick up the session.
      navigate(`${CONSOLE_BASE_PATH}/companies`, { replace: true });
    }
  }

  return (
    <main className="sa-login">
      <section className="sa-login-panel">
        <span className="sa-eyebrow">T-ZONE PLATFORM</span>
        <h1>Secure the control plane</h1>

        <p>
          This account has nobody above it and opens every company&apos;s door,
          so a password alone is not enough. Add an authenticator now; you will
          enter a 6-digit code from it every time you sign in.
        </p>
      </section>

      <section className="sa-login-card">
        {recoveryCodes ? (
          <>
            <header>
              <h2>Save your recovery codes</h2>
              <p>
                Shown once. Each code signs you in one time if you lose your
                authenticator. Store them offline, not on this machine.
              </p>
            </header>

            <ul className="sa-totp-recovery" aria-label="Recovery codes">
              {recoveryCodes.map((recoveryCode) => (
                <li key={recoveryCode}>{recoveryCode}</li>
              ))}
            </ul>

            <label className="sa-totp-ack">
              <input
                type="checkbox"
                checked={savedAcknowledged}
                onChange={(event) => setSavedAcknowledged(event.target.checked)}
              />
              I have saved these codes somewhere safe and offline.
            </label>

            <ConsoleButton
              variant="primary"
              className="sa-login-submit"
              disabled={!savedAcknowledged}
              loading={finishing}
              onClick={handleFinish}
            >
              Enter the console
            </ConsoleButton>
          </>
        ) : (
          <>
            <header>
              <h2>Set up two-factor authentication</h2>
              <p>Step 1 of 2 — scan, then confirm.</p>
            </header>

            {beginError ? (
              <ConsoleBanner tone="error">{beginError}</ConsoleBanner>
            ) : !qrSvg ? (
              <ConsoleLoading label="Generating your QR code..." />
            ) : (
              <>
                <ol className="sa-totp-steps">
                  <li>
                    Open an authenticator app (Google Authenticator, Microsoft
                    Authenticator, Authy, 1Password).
                  </li>
                  <li>Scan this code, or type the key below into it by hand.</li>
                </ol>

                <div
                  className="sa-totp-qr"
                  /* segno returns a self-contained SVG; it carries the same
                   * secret shown below, nothing more. */
                  dangerouslySetInnerHTML={{ __html: qrSvg }}
                />

                {secret ? (
                  <p className="sa-totp-secret">
                    <span>Manual key</span>
                    <code>{secret}</code>
                  </p>
                ) : null}

                <form onSubmit={handleConfirm}>
                  <label htmlFor="sa-totp-code">
                    Enter the 6-digit code from the app
                  </label>

                  <input
                    id="sa-totp-code"
                    inputMode="numeric"
                    autoComplete="one-time-code"
                    pattern="[0-9]*"
                    maxLength={6}
                    placeholder="123456"
                    value={code}
                    onChange={(event) =>
                      setCode(event.target.value.replace(/\D/g, ""))
                    }
                    required
                  />

                  <ConsoleBanner tone="error">{confirmError}</ConsoleBanner>

                  <ConsoleButton
                    type="submit"
                    variant="primary"
                    className="sa-login-submit"
                    disabled={code.length !== 6}
                    loading={confirming}
                  >
                    Turn on two-factor
                  </ConsoleButton>
                </form>
              </>
            )}

            <button
              type="button"
              className="sa-totp-signout"
              onClick={logout}
            >
              Sign out
            </button>
          </>
        )}
      </section>
    </main>
  );
}
