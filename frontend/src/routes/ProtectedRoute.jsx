import { Navigate, useLocation } from "react-router-dom";

import { useAuth } from "../contexts/AuthContext";


export default function ProtectedRoute({ children }) {
  const { authenticated, loading } = useAuth();
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

  return children;
}