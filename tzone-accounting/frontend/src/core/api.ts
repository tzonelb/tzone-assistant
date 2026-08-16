/** HTTP client. The only place the app talks to the network. */

import { clearSetting, readSetting, writeSetting } from "./storage";

export const TOKEN_KEY = "tzone.auth.token";
export const USER_KEY = "tzone.auth.user";

const BASE_URL = (import.meta.env?.VITE_API_URL as string | undefined) ?? "http://127.0.0.1:8010";

export class ApiError extends Error {
  constructor(
    message: string,
    readonly status: number,
  ) {
    super(message);
  }
}

/** Distinguishes "the server said no" from "there is no server right now" — offline is normal. */
export class OfflineError extends Error {}

export function token(): string | null {
  return readSetting(TOKEN_KEY);
}

export function setToken(value: string | null): void {
  if (value === null) clearSetting(TOKEN_KEY);
  else writeSetting(TOKEN_KEY, value);
}

export async function request<T>(
  path: string,
  options: { method?: string; body?: unknown; auth?: boolean } = {},
): Promise<T> {
  const { method = "GET", body, auth = true } = options;
  const headers: Record<string, string> = { "Content-Type": "application/json" };
  const bearer = token();
  if (auth && bearer) headers.Authorization = `Bearer ${bearer}`;

  let response: Response;
  try {
    response = await fetch(`${BASE_URL}${path}`, {
      method,
      headers,
      body: body === undefined ? undefined : JSON.stringify(body),
    });
  } catch {
    throw new OfflineError(`cannot reach ${BASE_URL}`);
  }

  if (!response.ok) {
    let detail = response.statusText;
    try {
      const payload = await response.json();
      detail = payload.detail ?? detail;
    } catch {
      /* keep the status text */
    }
    throw new ApiError(detail, response.status);
  }

  if (response.status === 204) return undefined as T;
  return (await response.json()) as T;
}

export const apiBaseUrl = BASE_URL;
