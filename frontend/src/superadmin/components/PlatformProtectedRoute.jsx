import { Navigate, useLocation } from "react-router-dom";

import { CONSOLE_BASE_PATH } from "../platformClient";
import { usePlatformAuth } from "../PlatformAuthContext";
import { ConsoleLoading } from "./ConsoleUI";


export default function PlatformProtectedRoute({ children }) {
  const { authenticated, loading, enrolmentPending } = usePlatformAuth();
  const location = useLocation();

  if (loading) {
    return (
      <main className="sa-fullscreen">
        <ConsoleLoading label="Verifying platform session..." />
      </main>
    );
  }

  // A valid session that still owes its second factor cannot enter the console.
  if (enrolmentPending) {
    return <Navigate to={`${CONSOLE_BASE_PATH}/enroll`} replace />;
  }

  if (!authenticated) {
    return (
      <Navigate
        to={`${CONSOLE_BASE_PATH}/login`}
        replace
        state={{ from: location.pathname }}
      />
    );
  }

  return children;
}
