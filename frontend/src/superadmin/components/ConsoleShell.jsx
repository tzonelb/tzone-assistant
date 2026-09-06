import { useState } from "react";
import { NavLink, Outlet, useNavigate } from "react-router-dom";
import {
  DomainOutlined,
  FactCheckOutlined,
  LocalOfferOutlined,
  LogoutOutlined,
  MonitorHeartOutlined,
  ShieldOutlined,
} from "@mui/icons-material";

import { CONSOLE_BASE_PATH } from "../platformClient";
import { usePlatformAuth } from "../PlatformAuthContext";
import { ConsoleBanner } from "./ConsoleUI";


const NAVIGATION = [
  ["companies", "Companies", DomainOutlined],
  ["plans", "Plans", LocalOfferOutlined],
  ["admins", "Platform admins", ShieldOutlined],
  ["audit", "Audit log", FactCheckOutlined],
  ["health", "Health", MonitorHeartOutlined],
];


export default function ConsoleShell() {
  const { admin, logout } = usePlatformAuth();
  const navigate = useNavigate();

  const [signingOut, setSigningOut] = useState(false);
  const [error, setError] = useState("");

  async function handleSignOut() {
    setSigningOut(true);
    setError("");

    try {
      await logout();
      navigate(`${CONSOLE_BASE_PATH}/login`, { replace: true });
    } catch (signOutError) {
      setError(signOutError.message || "Sign out failed.");
      setSigningOut(false);
    }
  }

  return (
    <div className="sa-shell">
      <header className="sa-topbar">
        <div className="sa-brand">
          <span className="sa-brand-mark">TZ</span>

          <div>
            <strong>Platform Control Plane</strong>
            <span>Super Admin console</span>
          </div>
        </div>

        <div className="sa-topbar-right">
          <div className="sa-identity">
            <strong>{admin?.full_name || admin?.email || "Platform administrator"}</strong>
            <span>{admin?.email}</span>
          </div>

          <button
            type="button"
            className="sa-signout"
            onClick={handleSignOut}
            disabled={signingOut}
          >
            <LogoutOutlined fontSize="small" />
            {signingOut ? "Signing out..." : "Sign out"}
          </button>
        </div>
      </header>

      <div className="sa-body">
        <nav className="sa-nav" aria-label="Console sections">
          {NAVIGATION.map(([path, label, Icon]) => (
            <NavLink
              key={path}
              to={`${CONSOLE_BASE_PATH}/${path}`}
              className={({ isActive }) =>
                `sa-nav-link ${isActive ? "is-active" : ""}`
              }
            >
              <Icon fontSize="small" />
              <span>{label}</span>
            </NavLink>
          ))}

          <p className="sa-nav-note">
            This console administers companies. It has no access to any
            company&apos;s conversations, messages or customers.
          </p>
        </nav>

        <main className="sa-main">
          <ConsoleBanner tone="error">{error}</ConsoleBanner>
          <Outlet />
        </main>
      </div>
    </div>
  );
}
