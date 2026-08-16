/**
 * The posting engine — pure functions over integers, no I/O, no React.
 *
 * This is where documents become ledger movements. It runs on the device, because an accounting
 * program that can only post when the server answers is not an offline program. The server
 * re-checks the invariants below before it stores anything (docs/ACCOUNTING_MODEL.md §5).
 */

import { toBase } from "../../core/money";
import type { EntryDraft, JournalLine } from "./types";

export class PostingError extends Error {}

export function debitLine(
  account_id: string,
  amount: number,
  rate: number,
  description = "",
  partner_id: string | null = null,
): JournalLine {
  return {
    account_id,
    partner_id,
    description,
    debit: amount,
    credit: 0,
    base_debit: toBase(amount, rate),
    base_credit: 0,
  };
}

export function creditLine(
  account_id: string,
  amount: number,
  rate: number,
  description = "",
  partner_id: string | null = null,
): JournalLine {
  return {
    account_id,
    partner_id,
    description,
    debit: 0,
    credit: amount,
    base_debit: 0,
    base_credit: toBase(amount, rate),
  };
}

export function totals(lines: JournalLine[]) {
  return lines.reduce(
    (acc, line) => ({
      debit: acc.debit + line.debit,
      credit: acc.credit + line.credit,
      baseDebit: acc.baseDebit + line.base_debit,
      baseCredit: acc.baseCredit + line.base_credit,
    }),
    { debit: 0, credit: 0, baseDebit: 0, baseCredit: 0 },
  );
}

/**
 * Merge lines that hit the same account and partner.
 *
 * An invoice with eight lines of the same service should not produce eight identical credits in
 * the general ledger. Netting also removes lines that cancel out entirely.
 */
export function condense(lines: JournalLine[]): JournalLine[] {
  const merged = new Map<string, JournalLine>();
  for (const line of lines) {
    const key = `${line.account_id}|${line.partner_id ?? ""}`;
    const existing = merged.get(key);
    if (!existing) {
      merged.set(key, { ...line });
      continue;
    }
    existing.debit += line.debit;
    existing.credit += line.credit;
    existing.base_debit += line.base_debit;
    existing.base_credit += line.base_credit;
  }

  const result: JournalLine[] = [];
  for (const line of merged.values()) {
    // Net the two sides so a line is one-sided, as the invariants require.
    const net = line.debit - line.credit;
    const baseNet = line.base_debit - line.base_credit;
    if (net === 0 && baseNet === 0) continue;
    result.push({
      ...line,
      debit: net > 0 ? net : 0,
      credit: net < 0 ? -net : 0,
      base_debit: baseNet > 0 ? baseNet : 0,
      base_credit: baseNet < 0 ? -baseNet : 0,
    });
  }
  return result;
}

export interface AccountLookup {
  (accountId: string): { is_group: boolean; is_active: boolean } | undefined;
}

/**
 * The invariants of docs/ACCOUNTING_MODEL.md §2. Returns the problems rather than throwing, so
 * a form can show all of them at once.
 */
export function validateDraft(
  draft: EntryDraft,
  lookup?: AccountLookup,
  lockDate?: string | null,
): string[] {
  const problems: string[] = [];
  const lines = draft.lines;

  if (lines.length < 2) problems.push("posting.needsTwoLines");

  lines.forEach((line, index) => {
    if (!line.account_id) problems.push(`posting.lineNoAccount:${index + 1}`);
    if (line.debit < 0 || line.credit < 0) problems.push(`posting.lineNegative:${index + 1}`);
    if (line.debit && line.credit) problems.push(`posting.lineTwoSided:${index + 1}`);
    if (!line.debit && !line.credit) problems.push(`posting.lineZero:${index + 1}`);

    const account = lookup?.(line.account_id);
    if (lookup && !account) problems.push(`posting.lineUnknownAccount:${index + 1}`);
    if (account?.is_group) problems.push(`posting.lineGroupAccount:${index + 1}`);
    if (account && !account.is_active) problems.push(`posting.lineInactive:${index + 1}`);
  });

  const sum = totals(lines);
  if (sum.debit !== sum.credit) problems.push("posting.unbalanced");
  if (sum.baseDebit !== sum.baseCredit) problems.push("posting.unbalancedBase");
  if (lockDate && draft.date <= lockDate) problems.push("posting.locked");

  return problems;
}

export function assertBalanced(draft: EntryDraft): void {
  const problems = validateDraft(draft);
  if (problems.length) throw new PostingError(problems.join(", "));
}

/**
 * The reversing entry of a void.
 *
 * A posted entry is never edited or deleted — the correction is a new, mirrored entry dated on
 * the day the mistake was found. That is what keeps the audit trail honest, and it is why every
 * report can be reproduced exactly as it looked at the time.
 */
export function reverse(lines: JournalLine[]): JournalLine[] {
  return lines.map((line) => ({
    ...line,
    debit: line.credit,
    credit: line.debit,
    base_debit: line.base_credit,
    base_credit: line.base_debit,
  }));
}
