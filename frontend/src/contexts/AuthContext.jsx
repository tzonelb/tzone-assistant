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


// A user whose administrator forced a reset is refused by every route except
// the one that changes the password — `/api/auth/me` included. The refusal is
// therefore the only way to learn the state, and treating it as a dead session
// would bounce them back to the login screen they just came from.
function passwordChangeRequired(error) {
  return (
    error?.status === 403 &&
    error?.data?.detail?.code === "password_change_required"
  );
}


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
      if (passwordChangeRequired(error)) {
        // Nothing else about them is readable until they change it, so the
        // flag is all the interface gets — and all it needs to route them.
        setUser({ must_change_password: true });
        setCompanies([]);
        setPermissions([]);
        return;
      }

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

  const login = useCallback(async (company, email, password, totpCode = "") => {
    const result = await loginRequest(company, email, password, totpCode);

    if (!result?.access_token) {
      throw new Error("The server did not return an access token.");
    }

    saveAccessToken(result.access_token);

    let currentUser;

    try {
      currentUser = await getCurrentUserRequest();
    } catch (error) {
      if (!passwordChangeRequired(error)) {
        throw error;
      }

      // The credentials were right and the token is real; the account is just
      // held at the change-password screen. Sign them in with what the login
      // response already said about them, and grant nothing.
      setUser(result?.user || null);
      setCompanies([]);
      setPermissions([]);

      return result;
    }

    setUser(currentUser?.user || result?.user || null);
    setCompanies(currentUser?.companies || []);
    setPermissions(currentUser?.permissions || result?.permissions || []);

    return result;
  }, []);

  const endLocalSession = useCallback(() => {
    clearAccessToken();
    setUser(null);
    setCompanies([]);
    setPermissions([]);
  }, []);

  const logout = useCallback(async () => {
    try {
      if (getAccessToken()) {
        await logoutRequest();
      }
    } catch {
      // The local session is still cleared when the server is unreachable.
    } finally {
      endLocalSession();
    }
  }, [endLocalSession]);

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
      // For the case where the server has already ended the session: changing
      // a password revokes every token, so calling /api/auth/logout afterwards
      // answers 401 and hard-redirects the browser mid-navigation.
      endLocalSession,
      refreshUser: loadCurrentUser,
    }),
    [
      user,
      companies,
      permissions,
      loading,
      login,
      logout,
      endLocalSession,
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