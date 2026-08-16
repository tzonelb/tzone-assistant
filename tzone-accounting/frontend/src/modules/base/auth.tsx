/**
 * Authentication that survives being offline.
 *
 * The token is stored locally and trusted for entry into the app: a shop must be able to open
 * its books when the internet is down, and a login screen that needs the server would make the
 * whole offline design pointless. The server still authenticates every sync request, so an
 * expired or revoked token stops replication — it just does not lock the user out of their own
 * local data.
 */

import { createContext, useCallback, useContext, useMemo, useState } from "react";
import type { ReactNode } from "react";
import { OfflineError, request, setToken, token } from "../../core/api";
import { deviceId as ensureDeviceId, DEVICE_CODE_KEY } from "../../core/repository";
import { clearSetting, readSetting, writeSetting } from "../../core/storage";
import { USER_KEY } from "../../core/api";

export interface User {
  id: string;
  username: string;
  display_name: string;
  role: string;
}

interface LoginResponse {
  token: string;
  user: User;
  device: { id: string; device_code: string } | null;
}

interface AuthValue {
  user: User | null;
  deviceCode: string;
  signedIn: boolean;
  login(username: string, password: string): Promise<void>;
  logout(): void;
}

const AuthContext = createContext<AuthValue | null>(null);

function storedUser(): User | null {
  const raw = readSetting(USER_KEY);
  if (!raw) return null;
  try {
    return JSON.parse(raw) as User;
  } catch {
    return null;
  }
}

export function AuthProvider({ children }: { children: ReactNode }) {
  const [user, setUser] = useState<User | null>(storedUser);
  const [deviceCode, setDeviceCode] = useState(readSetting(DEVICE_CODE_KEY) ?? "");

  const login = useCallback(async (username: string, password: string) => {
    const deviceId = ensureDeviceId();
    try {
      const response = await request<LoginResponse>("/api/auth/login", {
        method: "POST",
        auth: false,
        body: {
          username,
          password,
          device_id: deviceId,
          device_label: navigator.userAgent.slice(0, 80),
        },
      });
      setToken(response.token);
      writeSetting(USER_KEY, JSON.stringify(response.user));
      setUser(response.user);
      if (response.device) {
        writeSetting(DEVICE_CODE_KEY, response.device.device_code);
        setDeviceCode(response.device.device_code);
      }
    } catch (error) {
      if (error instanceof OfflineError) {
        throw new Error("offline-login");
      }
      throw error;
    }
  }, []);

  const logout = useCallback(() => {
    setToken(null);
    clearSetting(USER_KEY);
    setUser(null);
  }, []);

  const value = useMemo<AuthValue>(
    () => ({ user, deviceCode, signedIn: Boolean(user && token()), login, logout }),
    [user, deviceCode, login, logout],
  );

  return <AuthContext.Provider value={value}>{children}</AuthContext.Provider>;
}

export function useAuth(): AuthValue {
  const value = useContext(AuthContext);
  if (!value) throw new Error("useAuth must be used inside <AuthProvider>");
  return value;
}
