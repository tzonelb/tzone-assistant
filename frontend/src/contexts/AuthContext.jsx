import {
  createContext,
  useCallback,
  useContext,
  useEffect,
  useMemo,
  useState,
} from "react";

import {
  clearAccessToken,
  getAccessToken,
  getCurrentUserRequest,
  loginRequest,
  logoutRequest,
  saveAccessToken,
  verifyTwoFactorRequest,
} from "../api/client";


const AuthContext = createContext(null);


export function AuthProvider({ children }) {
  const [user, setUser] = useState(null);
  const [companies, setCompanies] = useState([]);
  const [loading, setLoading] = useState(true);

  const loadCurrentUser = useCallback(async () => {
    const token = getAccessToken();

    if (!token) {
      setUser(null);
      setCompanies([]);
      setLoading(false);
      return;
    }

    try {
      const result = await getCurrentUserRequest();

      setUser(result?.user || null);
      setCompanies(result?.companies || []);
    } catch (error) {
      if (error.status === 401) {
        clearAccessToken();
      }

      setUser(null);
      setCompanies([]);
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => {
    loadCurrentUser();
  }, [loadCurrentUser]);

  const completeLogin = useCallback(async (result) => {
    saveAccessToken(result.access_token);

    const currentUser = await getCurrentUserRequest();

    setUser(currentUser?.user || result?.user || null);
    setCompanies(currentUser?.companies || []);

    return result;
  }, []);

  const login = useCallback(async (company, email, password) => {
    const result = await loginRequest(company, email, password);

    // Account has 2FA enabled — caller must complete the challenge.
    if (result?.twofa_required) {
      return result;
    }

    if (!result?.access_token) {
      throw new Error("The server did not return an access token.");
    }

    return completeLogin(result);
  }, [completeLogin]);

  const verifyTwoFactor = useCallback(async (pendingToken, code) => {
    const result = await verifyTwoFactorRequest(pendingToken, code);

    if (!result?.access_token) {
      throw new Error("The server did not return an access token.");
    }

    return completeLogin(result);
  }, [completeLogin]);

  const logout = useCallback(async () => {
    try {
      if (getAccessToken()) {
        await logoutRequest();
      }
    } catch {
      // The local session is still cleared when the server is unreachable.
    } finally {
      clearAccessToken();
      setUser(null);
      setCompanies([]);
    }
  }, []);

  const value = useMemo(
    () => ({
      user,
      companies,
      loading,
      authenticated: Boolean(user),
      login,
      verifyTwoFactor,
      logout,
      refreshUser: loadCurrentUser,
    }),
    [
      user,
      companies,
      loading,
      login,
      verifyTwoFactor,
      logout,
      loadCurrentUser,
    ],
  );

  return (
    <AuthContext.Provider value={value}>
      {children}
    </AuthContext.Provider>
  );
}


export function useAuth() {
  const context = useContext(AuthContext);

  if (!context) {
    throw new Error("useAuth must be used inside AuthProvider.");
  }

  return context;
}