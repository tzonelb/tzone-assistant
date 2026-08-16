/**
 * Company settings: one replicated document that every module can contribute keys to.
 *
 * A module declares its defaults in `setup()`; the kernel merges them, and backfills any key a
 * newly installed module introduced without disturbing values the user has already changed.
 */

import { useLiveQuery } from "dexie-react-hooks";
import { table } from "./db";
import { getRegistry } from "./registry";
import { save } from "./repository";
import { DEFAULT_CURRENCIES, findCurrency, type Currency } from "./money";

export const COMPANY_ID = "company";

export interface CompanySettings {
  company_name: string;
  base_currency: string;
  currencies: Currency[];
  language: string;
  lock_date: string | null;
  fiscal_year_start: string;
  account_roles: Record<string, string>;
  [key: string]: unknown;
}

interface SettingsRecord {
  id: string;
  payload: CompanySettings;
}

export function defaults(): CompanySettings {
  return getRegistry().settingsDefaults as unknown as CompanySettings;
}

export async function loadSettings(): Promise<CompanySettings> {
  const row = (await table<SettingsRecord>("settings").get(COMPANY_ID)) ?? undefined;
  return { ...defaults(), ...(row?.payload ?? {}) };
}

/** Create the settings document, or backfill keys added by a newly installed module. */
export async function ensureSettings(): Promise<void> {
  const row = (await table<SettingsRecord>("settings").get(COMPANY_ID)) ?? undefined;
  const base = defaults();
  if (!row) {
    await save("settings", { id: COMPANY_ID, payload: base });
    return;
  }
  const missing = Object.entries(base).filter(([key]) => !(key in (row.payload ?? {})));
  if (!missing.length) return;
  await save("settings", {
    id: COMPANY_ID,
    payload: { ...Object.fromEntries(missing), ...row.payload },
  });
}

export async function updateSettings(patch: Partial<CompanySettings>): Promise<void> {
  const current = await loadSettings();
  await save("settings", { id: COMPANY_ID, payload: { ...current, ...patch } });
}

export function useSettings(): CompanySettings {
  const row = useLiveQuery(() => table<SettingsRecord>("settings").get(COMPANY_ID), []);
  return { ...defaults(), ...(row?.payload ?? {}) };
}

export function useBaseCurrency(): Currency {
  const settings = useSettings();
  return findCurrency(settings.currencies ?? DEFAULT_CURRENCIES, settings.base_currency ?? "USD");
}

/**
 * Look up an account by the role it plays (`receivable`, `tax_payable`, …) rather than by code.
 * A company can renumber its chart without any module changing.
 */
export function accountForRole(settings: CompanySettings, role: string): string | undefined {
  return settings.account_roles?.[role];
}
