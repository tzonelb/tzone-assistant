import {
  createContext,
  useCallback,
  useContext,
  useEffect,
  useMemo,
  useState,
} from "react";

import {
  clearPlatformToken,
  getPlatformToken,
  platformLoginRequest,
  platformLogoutRequest,
  platformMeRequest,
  platformTotpStatusRequest,
  savePlatformToken,
} from "./platformClient";


const PlatformAuthContext = createContext(null);


export function PlatformAuthProvider({ children }) {
  const [admin, setAdmin] = useState(null);
  const [loading, setLoading] = useState(true);
  // A super admin who signed in but has not turned on their second factor yet.
  // The session is real, but every console route 403s until enrolment finishes,
  // so the interface must send them to the enrolment screen rather than in.
  const [enrolmentPending, setEnrolmentPending] = useState(false);

  const loadCurrentAdmin = useCallback(async () => {
    const token = getPlatformToken();

    if (!token) {
      setAdmin(null);
      setEnrolmentPending(false);
      setLoading(false);
      return;
    }

    try {
      const result = await platformMeRequest();
      setAdmin(result?.user || null);
      setEnrolmentPending(false);
    } catch (error) {
      // A 403 on /auth/me may just mean "enrol first", not "log out": the
      // session is valid for the enrolment routes. Confirm with the status
      // route (which enrolment is allowed to reach) before clearing anything,
      // so a page refresh mid-enrolment resumes it instead of bouncing to login.
      if (error.status === 403) {
        try {
          const status = await platformTotpStatusRequest();

          if (status?.enrolment_pending) {
            setEnrolmentPending(true);
            setAdmin(null);
            return;
          }
        } catch {
          /* fall through to clearing the token */
        }
      }

      if (error.status === 401 || error.status === 403) {
        clearPlatformToken();
      }

      setAdmin(null);
      setEnrolmentPending(false);
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => {
    loadCurrentAdmin();
  }, [loadCurrentAdmin]);

  const login = useCallback(async (email, password, totpCode = "") => {
    const result = await platformLoginRequest(email, password, totpCode);

    if (!result?.access_token) {
      throw new Error("The server did not return a platform access token.");
    }

    savePlatformToken(result.access_token);

    // An unenrolled super admin gets a session and nothing else. Do NOT call
    // /auth/me here -- it 403s -- and instead route them to enrolment.
    if (result?.totp?.enrolment_pending) {
      setAdmin(null);
      setEnrolmentPending(true);
      return result;
    }

    const current = await platformMeRequest();
    setAdmin(current?.user || result?.user || null);
    setEnrolmentPending(false);

    return result;
  }, []);

  // Called by the enrolment screen once the second factor is confirmed: the
  // session that was enrolment-only is now a full console session.
  const finishEnrolment = useCallback(async () => {
    const current = await platformMeRequest();
    setAdmin(current?.user || null);
    setEnrolmentPending(false);
    return current;
  }, []);

  const logout = useCallback(async () => {
    try {
      await platformLogoutRequest();
    } finally {
      clearPlatformToken();
      setAdmin(null);
      setEnrolmentPending(false);
    }
  }, []);

  const value = useMemo(
    () => ({
      admin,
      loading,
      authenticated: Boolean(admin),
      enrolmentPending,
      login,
      logout,
      finishEnrolment,
    }),
    [admin, loading, enrolmentPending, login, logout, finishEnrolment],
  );

  return (
    <PlatformAuthContext.Provider value={value}>
      {children}
    </PlatformAuthContext.Provider>
  );
}

export function usePlatformAuth() {
  const context = useContext(PlatformAuthContext);

  if (!context) {
    throw new Error(
      "usePlatformAuth must be used inside a PlatformAuthProvider.",
    );
  }

  return context;
}
