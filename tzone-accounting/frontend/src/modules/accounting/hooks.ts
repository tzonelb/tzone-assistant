/** Live-query helpers over the local ledger. Shared by this module's screens and by reports. */

import { useLiveQuery } from "dexie-react-hooks";
import { table } from "../../core/db";
import type { Account, JournalEntry } from "./types";

export function useAccounts(): Account[] {
  return (
    useLiveQuery(async () => {
      const rows = await table<Account>("accounts").toArray();
      return rows.filter((row) => !row.deleted).sort((a, b) => a.code.localeCompare(b.code));
    }, []) ?? []
  );
}

export function usePostableAccounts(): Account[] {
  return useAccounts().filter((account) => !account.is_group && account.is_active);
}

export function useAccountMap(): Map<string, Account> {
  const accounts = useAccounts();
  return new Map(accounts.map((account) => [account.id, account]));
}

export function useJournalEntries(): JournalEntry[] {
  return (
    useLiveQuery(async () => {
      const rows = await table<JournalEntry>("journal_entries").toArray();
      return rows
        .filter((row) => !row.deleted)
        .sort((a, b) => b.date.localeCompare(a.date) || b.entry_no.localeCompare(a.entry_no));
    }, []) ?? []
  );
}
