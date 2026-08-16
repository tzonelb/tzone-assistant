import { useState, type FormEvent } from "react";
import { useI18n } from "../../core/i18n";
import { useAuth } from "./auth";

export function LoginPage() {
  const { t } = useI18n();
  const { login } = useAuth();
  const [username, setUsername] = useState("admin");
  const [password, setPassword] = useState("");
  const [error, setError] = useState("");
  const [busy, setBusy] = useState(false);

  async function submit(event: FormEvent) {
    event.preventDefault();
    setBusy(true);
    setError("");
    try {
      await login(username, password);
    } catch (caught) {
      // The first sign-in needs the server; after that the token works offline.
      setError((caught as Error).message === "offline-login" ? t("login.offline") : t("login.failed"));
    } finally {
      setBusy(false);
    }
  }

  return (
    <div className="login-screen">
      <form className="login-card" onSubmit={submit}>
        <div className="brand-mark large">T</div>
        <h1>{t("app.name")}</h1>
        <p className="muted">{t("login.subtitle")}</p>

        <label className="field">
          <span className="field-label">{t("login.username")}</span>
          <input value={username} onChange={(e) => setUsername(e.target.value)} autoFocus />
        </label>
        <label className="field">
          <span className="field-label">{t("login.password")}</span>
          <input
            type="password"
            value={password}
            onChange={(e) => setPassword(e.target.value)}
          />
        </label>

        {error ? <p className="error">{error}</p> : null}

        <button type="submit" className="primary" disabled={busy}>
          {busy ? t("login.working") : t("login.submit")}
        </button>
        <p className="hint">{t("login.hint")}</p>
      </form>
    </div>
  );
}
