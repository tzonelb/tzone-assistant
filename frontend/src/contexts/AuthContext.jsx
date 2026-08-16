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
} from "../api/client";


const AuthContext = createContext(null);


export function AuthProvider({ children }) {
  const [user, setUser] = useState(null);
  const [companies, setCompanies] = useState([]);
  const [permissions, setPermissions] = useState([]);
  const [loading, setLoading] = useState(true);

  const loadCurrentUser = useCallback(async () => {
    const token = getAccessToken();

    if (!token) {
      setUser(null);
      setCompanies([]);
      setPermissions([]);
      setLoading(false);
      return;
    }

    try {
      const result = await getCurrentUserRequest();

      setUser(result?.user || null);
      setCompanies(result?.companies || []);
      setPermissions(result?.permissions || []);
    } catch (error) {
      if (error.status === 401) {
        clearAccessToken();
      }

      setUser(null);
      setCompanies([]);
      setPermissions([]);
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => {
    loadCurrentUser();
  }, [loadCurrentUser]);

  const login = useCallback(async (company, email, password, workspaceCode) => {
    const result = await loginRequest(company, email, password, workspaceCode);

    if (!result?.access_token) {
      throw new Error("The server did not return an access token.");
    }

    saveAccessToken(result.access_token);

    const currentUser = await getCurrentUserRequest();

    setUser(currentUser?.user || result?.user || null);
    setCompanies(currentUser?.companies || []);
    setPermissions(currentUser?.permissions || result?.permissions || []);

    return result;
  }, []);

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
      setPermissions([]);
    }
  }, []);

  const value = useMemo(
    () => ({
      user,
      companies,
      permissions,
      loading,
      authenticated: Boolean(user),
      // A super admin holds every permission implicitly, exactly as the API
      // decides it; the screen must not disagree with the server about that.
      can: (code) =>
        Boolean(user?.is_super_admin) || permissions.includes(code),
      login,
      logout,
      refreshUser: loadCurrentUser,
    }),
    [
      user,
      companies,
      permissions,
      loading,
      login,
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