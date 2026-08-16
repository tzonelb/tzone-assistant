/**
 * Install the default chart of accounts locally.
 *
 * Ids are derived from the account code (`acc-1130`), exactly as the backend seed does. That is
 * what lets a browser that seeded its chart offline converge with a freshly seeded server on
 * first sync, instead of ending up with two parallel trees of the same accounts.
 */

import chart from "@shared/chart-of-accounts.json";
import { table } from "../../core/db";
import { save } from "../../core/repository";
import type { Account } from "./types";

interface ChartEntry {
  code: string;
  name_en: string;
  name_ar: string;
  type: Account["type"];
  parent?: string | null;
  is_group?: boolean;
  is_cash?: boolean;
  role?: string;
}

export function accountIdFor(code: string): string {
  return `acc-${code}`;
}

export async function seedChartOfAccounts(): Promise<void> {
  const existing = await table<Account>("accounts").count();
  if (existing > 0) return;

  for (const entry of (chart as { accounts: ChartEntry[] }).accounts) {
    await save<Partial<Account>>("account", {
      id: accountIdFor(entry.code),
      code: entry.code,
      name_en: entry.name_en,
      name_ar: entry.name_ar,
      type: entry.type,
      parent_id: entry.parent ? accountIdFor(entry.parent) : null,
      is_group: Boolean(entry.is_group),
      is_cash: Boolean(entry.is_cash),
      currency: null,
      is_active: true,
    });
  }
}
