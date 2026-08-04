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

  const login = useCallback(async (company, email, password) => {
    const result = await loginRequest(company, email, password);

    if (!result?.access_token) {
      throw new Error("The server did not return an access token.");
    }

    saveAccessToken(result.access_token);

    const currentUser = await getCurrentUserRequest();

    setUser(currentUser?.user || result?.user || null);
    setCompanies(currentUser?.companies || []);
    setPermissions(currentUser?.permissions || []);

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

  const hasPermission = useCallback(
    (code) => {
      if (!user) return false;
      if (user.is_super_admin) return true;
      return permissions.includes("*") || permissions.includes(code);
    },
    [user, permissions],
  );

  const value = useMemo(
    () => ({
      user,
      companies,
      permissions,
      loading,
      authenticated: Boolean(user),
      login,
      logout,
      hasPermission,
      refreshUser: loadCurrentUser,
    }),
    [
      user,
      companies,
      permissions,
      loading,
      login,
      logout,
      hasPermission,
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