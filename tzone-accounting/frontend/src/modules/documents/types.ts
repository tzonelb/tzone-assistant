import type { DocumentStatus, Envelope } from "../../core/types";
import type { EntryDraft } from "../accounting/types";

export interface DocumentLine {
  item_id?: string | null;
  description: string;
  quantity: number;
  /** Unit price and tax rate are per line; totals are derived, never typed by hand. */
  unit_price: number;
  tax_rate_bp: number;
  account_id?: string | null;
}

export interface Allocation {
  document_id: string;
  amount: number;
  base_amount: number;
}

export interface DocumentPayload {
  lines?: DocumentLine[];
  allocations?: Allocation[];
  net_total?: number;
  tax_total?: number;
  cash_account_id?: string | null;
  [key: string]: unknown;
}

export interface BusinessDocument extends Envelope {
  doc_type: string;
  doc_no: string;
  legal_no: string | null;
  date: string;
  due_date: string | null;
  partner_id: string | null;
  currency: string;
  fx_rate: number;
  total: number;
  base_total: number;
  status: DocumentStatus;
  journal_entry_id: string | null;
  memo: string;
  payload: DocumentPayload;
}

/**
 * A document type contributed by a module.
 *
 * `settles` and `role` are what put a type into the aging reports; `buildEntry` is what turns it
 * into ledger movements. Registering one is the whole cost of adding a new kind of paperwork.
 */
export interface DocumentTypeDef {
  key: string;
  prefix: string;
  labelKey: string;
  module: string;
  settles?: "receivable" | "payable";
  role?: "charge" | "settlement";
  sequence?: number;
  buildEntry(document: BusinessDocument, context: PostingContext): EntryDraft;
}

/** What a posting rule is allowed to look at. Passed in, so rules stay pure and testable. */
export interface PostingContext {
  accountRoles: Record<string, string>;
  /** Item-level overrides, falling back to the company role accounts. */
  itemAccount(itemId: string | null | undefined, side: "income" | "expense"): string | null;
  partnerControl(partnerId: string | null, side: "receivable" | "payable"): string;
}
