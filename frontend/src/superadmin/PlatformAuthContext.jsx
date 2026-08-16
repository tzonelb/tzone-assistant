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
  savePlatformToken,
} from "./platformClient";


const PlatformAuthContext = createContext(null);


export function PlatformAuthProvider({ children }) {
  const [admin, setAdmin] = useState(null);
  const [loading, setLoading] = useState(true);

  const loadCurrentAdmin = useCallback(async () => {
    const token = getPlatformToken();

    if (!token) {
      setAdmin(null);
      setLoading(false);
      return;
    }

    try {
      const result = await platformMeRequest();
      setAdmin(result?.user || null);
    } catch (error) {
      // A revoked or expired platform token must not bounce the browser out of
      // the console here: the guard renders the login screen instead.
      if (error.status === 401 || error.status === 403) {
        clearPlatformToken();
      }

      setAdmin(null);
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => {
    loadCurrentAdmin();
  }, [loadCurrentAdmin]);

  const login = useCallback(async (email, password) => {
    const result = await platformLoginRequest(email, password);

    if (!result?.access_token) {
      throw new Error("The server did not return a platform access token.");
    }

    savePlatformToken(result.access_token);

    const current = await platformMeRequest();
    setAdmin(current?.user || result?.user || null);

    return result;
  }, []);

  const logout = useCallback(async () => {
    try {
      await platformLogoutRequest();
    } finally {
      clearPlatformToken();
      setAdmin(null);
    }
  }, []);

  const value = useMemo(
    () => ({
      admin,
      loading,
      authenticated: Boolean(admin),
      login,
      logout,
    }),
    [admin, loading, login, logout],
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
