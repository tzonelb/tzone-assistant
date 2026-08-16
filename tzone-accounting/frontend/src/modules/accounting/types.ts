import type { AccountType, Envelope, EntryStatus } from "../../core/types";

export interface Account extends Envelope {
  code: string;
  name_en: string;
  name_ar: string;
  type: AccountType;
  parent_id: string | null;
  is_group: boolean;
  is_cash: boolean;
  currency: string | null;
  is_active: boolean;
}

export interface JournalLine {
  account_id: string;
  partner_id?: string | null;
  description: string;
  /** Transaction currency, minor units. A line is either a debit or a credit, never both. */
  debit: number;
  credit: number;
  /** The same amounts in base currency, fixed at the document's FX rate. */
  base_debit: number;
  base_credit: number;
}

export interface JournalEntry extends Envelope {
  entry_no: string;
  date: string;
  memo: string;
  currency: string;
  /** Micro-units: 1_000_000 === 1.0 */
  fx_rate: number;
  status: EntryStatus;
  /** Which module produced this entry, and from which document. */
  source_kind: string;
  source_id: string | null;
  reverses_id: string | null;
  created_by: string;
  lines: JournalLine[];
}

/** What a posting rule returns: the ledger effect of a document, before numbering and stamping. */
export interface EntryDraft {
  date: string;
  memo: string;
  currency: string;
  fx_rate: number;
  source_kind: string;
  source_id: string | null;
  lines: JournalLine[];
}
