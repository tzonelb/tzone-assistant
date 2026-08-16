/** Ledger operations the UI and other modules call. The only writer of journal entries. */

import { nextNumber } from "../../core/numbering";
import { getRegistry } from "../../core/registry";
import { get, list, save } from "../../core/repository";
import { loadSettings } from "../../core/settings";
import { condense, PostingError, reverse, validateDraft } from "./posting";
import type { Account, EntryDraft, JournalEntry } from "./types";

export const ENTRY_NUMBER_KIND = "journal_entry";

async function accountIndex(): Promise<Map<string, Account>> {
  const accounts = await list<Account>("account");
  return new Map(accounts.map((account) => [account.id, account]));
}

/**
 * Validate a draft and write it to the ledger.
 *
 * Fires `entry_posted` so any module can react (the audit log on the server does the same with
 * its own hook) without this function knowing who is listening.
 */
export async function postEntry(draft: EntryDraft, options: { status?: "draft" | "posted" } = {}) {
  const status = options.status ?? "posted";
  const settings = await loadSettings();
  const accounts = await accountIndex();

  const prepared: EntryDraft = { ...draft, lines: condense(draft.lines) };
  if (status === "posted") {
    const problems = validateDraft(
      prepared,
      (id) => {
        const account = accounts.get(id);
        return account && { is_group: account.is_group, is_active: account.is_active };
      },
      settings.lock_date,
    );
    if (problems.length) throw new PostingError(problems.join("|"));
  }

  const entry = await save<Partial<JournalEntry>>("journal_entry", {
    entry_no: await nextNumber(ENTRY_NUMBER_KIND, "JV"),
    date: prepared.date,
    memo: prepared.memo,
    currency: prepared.currency,
    fx_rate: prepared.fx_rate,
    status,
    source_kind: prepared.source_kind,
    source_id: prepared.source_id,
    reverses_id: null,
    created_by: "",
    lines: prepared.lines,
  });

  getRegistry().hooks.emit("entry_posted", entry);
  return entry as JournalEntry;
}

/**
 * Void a posted entry by writing its mirror image, then marking the original.
 *
 * Both steps are separate records: the original stays exactly as it was posted, which is the
 * whole point of an audit trail.
 */
export async function voidEntry(entryId: string, onDate: string): Promise<JournalEntry> {
  const original = await get<JournalEntry>("journal_entry", entryId);
  if (!original) throw new PostingError("posting.unknownEntry");
  if (original.status !== "posted") throw new PostingError("posting.notPosted");

  const reversal = await save<Partial<JournalEntry>>("journal_entry", {
    entry_no: await nextNumber(ENTRY_NUMBER_KIND, "JV"),
    date: onDate,
    memo: `Reversal of ${original.entry_no}`,
    currency: original.currency,
    fx_rate: original.fx_rate,
    status: "posted",
    source_kind: original.source_kind,
    source_id: original.source_id,
    reverses_id: original.id,
    created_by: "",
    lines: reverse(original.lines),
  });

  await save<Partial<JournalEntry>>("journal_entry", { ...original, status: "void" });
  getRegistry().hooks.emit("entry_voided", original, reversal);
  return reversal as JournalEntry;
}

export async function postedEntries(): Promise<JournalEntry[]> {
  const entries = await list<JournalEntry>("journal_entry");
  return entries.filter((entry) => entry.status === "posted");
}

/** Every posted line, flattened — the input to every report. */
export interface FlatLine {
  entry: JournalEntry;
  account_id: string;
  partner_id: string | null;
  description: string;
  base_debit: number;
  base_credit: number;
}

export async function ledgerLines(): Promise<FlatLine[]> {
  const entries = await postedEntries();
  const flat: FlatLine[] = [];
  for (const entry of entries) {
    for (const line of entry.lines ?? []) {
      flat.push({
        entry,
        account_id: line.account_id,
        partner_id: line.partner_id ?? null,
        description: line.description,
        base_debit: line.base_debit,
        base_credit: line.base_credit,
      });
    }
  }
  return flat;
}
