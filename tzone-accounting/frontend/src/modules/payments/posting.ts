/**
 * Settlement posting rules.
 *
 * Allocations against specific invoices deliberately produce **no extra journal lines** — the
 * cash movement below already reflects the money. Allocations exist so aging and document
 * status know which invoice was settled (docs/ACCOUNTING_MODEL.md §3).
 */

import { creditLine, debitLine } from "../accounting/posting";
import type { EntryDraft } from "../accounting/types";
import type { BusinessDocument, PostingContext } from "../documents/types";

function cashAccount(document: BusinessDocument, context: PostingContext): string {
  return (
    document.payload.cash_account_id ??
    context.accountRoles.cash ??
    context.accountRoles.bank ??
    ""
  );
}

/**
 * Receipt (money in from a customer):
 *   Dr  Cash / bank                amount
 *       Cr  Accounts receivable              amount
 */
export function buildReceiptEntry(
  document: BusinessDocument,
  context: PostingContext,
): EntryDraft {
  const rate = document.fx_rate;
  const receivable = context.partnerControl(document.partner_id, "receivable");
  return {
    date: document.date,
    memo: document.memo || document.doc_no,
    currency: document.currency,
    fx_rate: rate,
    source_kind: "receipt",
    source_id: document.id || null,
    lines: [
      debitLine(cashAccount(document, context), document.total, rate, document.doc_no),
      creditLine(receivable, document.total, rate, document.doc_no, document.partner_id ?? null),
    ],
  };
}

/**
 * Payment (money out to a supplier):
 *   Dr  Accounts payable           amount
 *       Cr  Cash / bank                      amount
 */
export function buildPaymentEntry(
  document: BusinessDocument,
  context: PostingContext,
): EntryDraft {
  const rate = document.fx_rate;
  const payable = context.partnerControl(document.partner_id, "payable");
  return {
    date: document.date,
    memo: document.memo || document.doc_no,
    currency: document.currency,
    fx_rate: rate,
    source_kind: "payment",
    source_id: document.id || null,
    lines: [
      debitLine(payable, document.total, rate, document.doc_no, document.partner_id ?? null),
      creditLine(cashAccount(document, context), document.total, rate, document.doc_no),
    ],
  };
}
