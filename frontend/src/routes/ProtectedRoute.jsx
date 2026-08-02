import { Navigate, useLocation } from "react-router-dom";

import { useAuth } from "../contexts/AuthContext";


export default function ProtectedRoute({ children, requireSuperAdmin = false }) {
  const { authenticated, loading, user } = useAuth();
  const location = useLocation();

  if (loading) {
    return (
      <main className="full-screen-state">
        <div className="loading-spinner" />
        <strong>Loading T-ZONE Platform...</strong>
      </main>
    );
  }

  if (!authenticated) {
    return (
      <Navigate
        to="/login"
        replace
        state={{
          from: location.pathname,
        }}
      />
    );
  }

  // Every action on this route already 403s server-side for a non-super-
  // admin, but letting them land on a fully-rendered, clickable admin
  // shell first is confusing - redirect before it ever renders.
  if (requireSuperAdmin && !user?.is_super_admin) {
    return <Navigate to="/dashboard" replace />;
  }

  return children;
}