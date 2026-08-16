/** Routing, assembled from the installed modules. No screen is named here. */

import { Navigate, Route, Routes } from "react-router-dom";
import { getRegistry } from "./core/registry";
import { AuthProvider, useAuth } from "./modules/base/auth";
import { Layout } from "./modules/base/Layout";

function RequireAuth({ children }: { children: React.ReactNode }) {
  const { signedIn } = useAuth();
  return signedIn ? <>{children}</> : <Navigate to="/login" replace />;
}

function AppRoutes() {
  const registry = getRegistry();
  const standalone = registry.routes.filter((route) => route.standalone);
  const shell = registry.routes.filter((route) => !route.standalone);
  const first = registry.menu[0]?.path ?? "/settings";

  return (
    <Routes>
      {standalone.map((route) => {
        const Element = route.element;
        return <Route key={route.path} path={route.path} element={<Element />} />;
      })}

      <Route
        element={
          <RequireAuth>
            <Layout />
          </RequireAuth>
        }
      >
        {shell.map((route) => {
          const Element = route.element;
          return <Route key={route.path} path={route.path} element={<Element />} />;
        })}
      </Route>

      <Route path="/" element={<Navigate to={first} replace />} />
      <Route path="*" element={<Navigate to={first} replace />} />
    </Routes>
  );
}

export default function App() {
  return (
    <AuthProvider>
      <AppRoutes />
    </AuthProvider>
  );
}
