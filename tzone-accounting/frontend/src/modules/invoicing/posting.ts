/**
 * Invoice posting rules — pure functions, unit tested in posting.test.ts.
 *
 * docs/ACCOUNTING_MODEL.md §3 states these in words; this is the same thing in code, and the
 * tests assert the two agree by checking the entries balance and hit the expected accounts.
 */

import { taxOf } from "../../core/money";
import { creditLine, debitLine } from "../accounting/posting";
import type { EntryDraft, JournalLine } from "../accounting/types";
import { lineNet } from "../documents/service";
import type { BusinessDocument, PostingContext } from "../documents/types";

/**
 * Sales invoice:
 *   Dr  Accounts receivable            total
 *       Cr  Income (per line)                    line net
 *       Cr  Tax payable                          tax total
 */
export function buildSalesInvoiceEntry(
  document: BusinessDocument,
  context: PostingContext,
): EntryDraft {
  const rate = document.fx_rate;
  const lines: JournalLine[] = [];
  const receivable = context.partnerControl(document.partner_id, "receivable");

  lines.push(
    debitLine(receivable, document.total, rate, document.doc_no, document.partner_id ?? null),
  );

  let tax = 0;
  for (const line of document.payload.lines ?? []) {
    const net = lineNet(line);
    tax += taxOf(net, line.tax_rate_bp);
    const account = line.account_id ?? context.itemAccount(line.item_id, "income");
    if (!account) continue;
    lines.push(creditLine(account, net, rate, line.description));
  }

  if (tax > 0 && context.accountRoles.tax_payable) {
    lines.push(creditLine(context.accountRoles.tax_payable, tax, rate, "Tax"));
  }

  return {
    date: document.date,
    memo: document.memo || document.doc_no,
    currency: document.currency,
    fx_rate: rate,
    source_kind: "sales_invoice",
    source_id: document.id || null,
    lines,
  };
}

/**
 * Purchase invoice:
 *   Dr  Expense / inventory (per line)  line net
 *   Dr  Tax receivable                  tax total
 *       Cr  Accounts payable                     total
 */
export function buildPurchaseInvoiceEntry(
  document: BusinessDocument,
  context: PostingContext,
): EntryDraft {
  const rate = document.fx_rate;
  const lines: JournalLine[] = [];
  const payable = context.partnerControl(document.partner_id, "payable");

  let tax = 0;
  for (const line of document.payload.lines ?? []) {
    const net = lineNet(line);
    tax += taxOf(net, line.tax_rate_bp);
    const account = line.account_id ?? context.itemAccount(line.item_id, "expense");
    if (!account) continue;
    lines.push(debitLine(account, net, rate, line.description));
  }

  if (tax > 0 && context.accountRoles.tax_receivable) {
    lines.push(debitLine(context.accountRoles.tax_receivable, tax, rate, "Tax"));
  }

  lines.push(
    creditLine(payable, document.total, rate, document.doc_no, document.partner_id ?? null),
  );

  return {
    date: document.date,
    memo: document.memo || document.doc_no,
    currency: document.currency,
    fx_rate: rate,
    source_kind: "purchase_invoice",
    source_id: document.id || null,
    lines,
  };
}
