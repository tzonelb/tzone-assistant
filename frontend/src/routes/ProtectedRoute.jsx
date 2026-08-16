import { Navigate, useLocation } from "react-router-dom";

import { useAuth } from "../contexts/AuthContext";
import ForcePasswordChangePage from "../pages/auth/ForcePasswordChangePage";


export default function ProtectedRoute({ children }) {
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

  // Ahead of every protected screen rather than on one route of its own: the
  // API refuses all of them until the password is changed, so anything else
  // rendered here would be a page of failed requests.
  if (user?.must_change_password) {
    return <ForcePasswordChangePage />;
  }

  return children;
}