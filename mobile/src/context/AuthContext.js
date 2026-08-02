import React, { createContext, useContext, useEffect, useState, useCallback } from "react";
import {
  getToken,
  setToken,
  loginRequest,
  verify2faRequest,
  logoutRequest,
  meRequest,
  setUnauthorizedHandler,
} from "../api/client";

const AuthContext = createContext(null);

export function AuthProvider({ children }) {
  const [initializing, setInitializing] = useState(true);
  const [authenticated, setAuthenticated] = useState(false);
  const [user, setUser] = useState(null);

  const clearSession = useCallback(() => {
    setToken(null);
    setUser(null);
    setAuthenticated(false);
  }, []);

  useEffect(() => {
    setUnauthorizedHandler(clearSession);
    (async () => {
      try {
        const token = await getToken();
        if (token) {
          const me = await meRequest();
          setUser(me.user);
          setAuthenticated(true);
        }
      } catch (_) {
        // Stored token invalid or server unreachable — fall back to login.
      } finally {
        setInitializing(false);
      }
    })();
  }, [clearSession]);

  const completeLogin = useCallback(async (loginResponse) => {
    await setToken(loginResponse.access_token);
    setUser(loginResponse.user);
    setAuthenticated(true);
  }, []);

  const login = useCallback(
    async ({ company, email, password }) => {
      const res = await loginRequest({ company, email, password });
      if (res.twofa_required) {
        return { twofaRequired: true, pendingToken: res.pending_token };
      }
      await completeLogin(res);
      return { twofaRequired: false };
    },
    [completeLogin]
  );

  const verify2fa = useCallback(
    async ({ pendingToken, code }) => {
      const res = await verify2faRequest({ pendingToken, code });
      await completeLogin(res);
    },
    [completeLogin]
  );

  const logout = useCallback(async () => {
    try {
      await logoutRequest();
    } catch (_) {
      // Revoking server-side failed (offline etc.) — still clear locally.
    }
    clearSession();
  }, [clearSession]);

  return (
    <AuthContext.Provider value={{ initializing, authenticated, user, login, verify2fa, logout }}>
      {children}
    </AuthContext.Provider>
  );
}

export function useAuth() {
  return useContext(AuthContext);
}
