import { useEffect, useState } from "react";
import { isModuleVisible } from "../config/platformDefaults";
import { getCurrentUserRequest } from "../api/client";
import { usePlatformTheme } from "../contexts/ThemeContext";

// Route-level access guard that mirrors SidebarV2's isAllowed + isModuleVisible
// logic, so a module/permission hidden from the nav is ALSO unreachable by a
// direct URL. Without this, a hidden page still renders fully and then dies
// piecemeal on 403s from its API calls — a confusing half-broken screen. This
// closes that gap by showing one clean "not available" message instead.
export default function RequireAccess({ permissions, moduleKey, children }) {
  const { modules } = usePlatformTheme();
  const [state, setState] = useState({ loading: true, isSuperAdmin: false, isOwner: false, codes: [] });

  useEffect(() => {
    let cancelled = false;
    getCurrentUserRequest()
      .then((response) => {
        if (cancelled) return;
        const activeCompanyId = response?.user?.active_company_id;
        const companies = Array.isArray(response?.companies) ? response.companies : [];
        const active = companies.find((company) => company.id === activeCompanyId) || companies[0];
        setState({
          loading: false,
          isSuperAdmin: Boolean(response?.user?.is_super_admin),
          isOwner: active?.role_code === "owner",
          // `permissions` is where GET /api/auth/me actually puts the active
          // company's codes; `permission_codes` on the company row is the
          // shape this guard was written against and the server has never
          // sent. Reading only that one left `codes` empty for everybody, so
          // the guard passed on `isOwner` alone — and a permission an owner
          // had deliberately granted to a manager opened nothing. Both are
          // read so neither side has to move.
          codes: response?.permissions || active?.permission_codes || [],
        });
      })
      .catch(() => { if (!cancelled) setState((s) => ({ ...s, loading: false })); });
    return () => { cancelled = true; };
  }, []);

  if (state.loading) {
    return <div className="tz-screen" style={{ padding: "var(--space-6)" }}>Loading…</div>;
  }

  const permitted = !permissions
    || state.isSuperAdmin || state.isOwner
    || permissions.some((code) => state.codes.includes(code));
  const moduleVisible = !moduleKey || isModuleVisible(modules, moduleKey);

  if (!permitted || !moduleVisible) {
    return (
      <div className="tz-screen" style={{ padding: "var(--space-6)", maxWidth: 520 }}>
        <h2>This feature isn’t available</h2>
        <p>
          {moduleVisible
            ? "You don’t have permission to view this section. Ask your workspace owner if you need access."
            : "This feature isn’t enabled for your workspace."}
        </p>
      </div>
    );
  }

  return children;
}
