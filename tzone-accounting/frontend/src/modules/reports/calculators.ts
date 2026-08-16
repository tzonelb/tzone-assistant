/**
 * Report calculators — pure functions over posted journal lines.
 *
 * These run on the device, which is what makes every financial statement available with no
 * network at all. The server mirrors them in SQL for the consolidated, cross-device view
 * (`backend/app/modules/reports/calculators.py`); both are asserted to balance from the same
 * data in their test suites.
 *
 * All arithmetic is on integer minor units of the base currency.
 */

import type { AccountType } from "../../core/types";
import type { JournalEntry } from "../accounting/types";

export const DEBIT_NORMAL: AccountType[] = ["asset", "expense"];

export interface ReportAccount {
  id: string;
  code: string;
  name_en: string;
  name_ar: string;
  type: AccountType;
  is_group: boolean;
  is_cash: boolean;
}

export interface ReportLine {
  entryId: string;
  entryNo: string;
  date: string;
  memo: string;
  sourceKind: string;
  accountId: string;
  partnerId: string | null;
  debit: number;
  credit: number;
}

/** Flatten posted entries into report input. Drafts and voided entries never reach a report. */
export function toReportLines(entries: JournalEntry[]): ReportLine[] {
  const lines: ReportLine[] = [];
  for (const entry of entries) {
    if (entry.status !== "posted" || entry.deleted) continue;
    for (const line of entry.lines ?? []) {
      lines.push({
        entryId: entry.id,
        entryNo: entry.entry_no,
        date: entry.date,
        memo: entry.memo || line.description,
        sourceKind: entry.source_kind,
        accountId: line.account_id,
        partnerId: line.partner_id ?? null,
        debit: line.base_debit,
        credit: line.base_credit,
      });
    }
  }
  return lines;
}

/** Balance on the account's normal side, so a healthy account reads positive. */
export function signed(type: AccountType, debit: number, credit: number): number {
  return DEBIT_NORMAL.includes(type) ? debit - credit : credit - debit;
}

function within(line: ReportLine, from: string | null, to: string): boolean {
  if (from !== null && line.date < from) return false;
  return line.date <= to;
}

export interface TrialBalanceRow {
  account: ReportAccount;
  debit: number;
  credit: number;
  balance: number;
}

export interface TrialBalance {
  rows: TrialBalanceRow[];
  totalDebit: number;
  totalCredit: number;
  balanced: boolean;
}

export function trialBalance(
  lines: ReportLine[],
  accounts: ReportAccount[],
  from: string,
  to: string,
): TrialBalance {
  const index = new Map(accounts.map((account) => [account.id, account]));
  const totals = new Map<string, { debit: number; credit: number }>();

  for (const line of lines) {
    if (!within(line, from, to)) continue;
    const current = totals.get(line.accountId) ?? { debit: 0, credit: 0 };
    current.debit += line.debit;
    current.credit += line.credit;
    totals.set(line.accountId, current);
  }

  const rows: TrialBalanceRow[] = [];
  for (const [accountId, sums] of totals) {
    const account = index.get(accountId);
    if (!account || (sums.debit === 0 && sums.credit === 0)) continue;
    rows.push({
      account,
      debit: sums.debit,
      credit: sums.credit,
      balance: signed(account.type, sums.debit, sums.credit),
    });
  }
  rows.sort((a, b) => a.account.code.localeCompare(b.account.code));

  const totalDebit = rows.reduce((sum, row) => sum + row.debit, 0);
  const totalCredit = rows.reduce((sum, row) => sum + row.credit, 0);
  return { rows, totalDebit, totalCredit, balanced: totalDebit === totalCredit };
}

export interface StatementRow {
  account: ReportAccount;
  amount: number;
}

function byType(
  lines: ReportLine[],
  accounts: ReportAccount[],
  types: AccountType[],
  from: string | null,
  to: string,
): StatementRow[] {
  const index = new Map(accounts.map((account) => [account.id, account]));
  const totals = new Map<string, { debit: number; credit: number }>();

  for (const line of lines) {
    if (!within(line, from, to)) continue;
    const account = index.get(line.accountId);
    if (!account || !types.includes(account.type)) continue;
    const current = totals.get(line.accountId) ?? { debit: 0, credit: 0 };
    current.debit += line.debit;
    current.credit += line.credit;
    totals.set(line.accountId, current);
  }

  const rows: StatementRow[] = [];
  for (const [accountId, sums] of totals) {
    const account = index.get(accountId)!;
    const amount = signed(account.type, sums.debit, sums.credit);
    if (amount !== 0) rows.push({ account, amount });
  }
  return rows.sort((a, b) => a.account.code.localeCompare(b.account.code));
}

export interface ProfitAndLoss {
  income: StatementRow[];
  expenses: StatementRow[];
  totalIncome: number;
  totalExpense: number;
  netProfit: number;
}

export function profitAndLoss(
  lines: ReportLine[],
  accounts: ReportAccount[],
  from: string,
  to: string,
): ProfitAndLoss {
  const income = byType(lines, accounts, ["income"], from, to);
  const expenses = byType(lines, accounts, ["expense"], from, to);
  const totalIncome = income.reduce((sum, row) => sum + row.amount, 0);
  const totalExpense = expenses.reduce((sum, row) => sum + row.amount, 0);
  return { income, expenses, totalIncome, totalExpense, netProfit: totalIncome - totalExpense };
}

export interface BalanceSheet {
  assets: StatementRow[];
  liabilities: StatementRow[];
  equity: StatementRow[];
  retainedEarnings: number;
  totalAssets: number;
  totalLiabilities: number;
  totalEquity: number;
  balanced: boolean;
}

export function balanceSheet(
  lines: ReportLine[],
  accounts: ReportAccount[],
  asOf: string,
): BalanceSheet {
  const assets = byType(lines, accounts, ["asset"], null, asOf);
  const liabilities = byType(lines, accounts, ["liability"], null, asOf);
  const equity = byType(lines, accounts, ["equity"], null, asOf);

  // Retained earnings are not an account balance — they are the cumulative P&L to date.
  const performance = profitAndLoss(lines, accounts, "0000-01-01", asOf);
  const retainedEarnings = performance.netProfit;

  const totalAssets = assets.reduce((sum, row) => sum + row.amount, 0);
  const totalLiabilities = liabilities.reduce((sum, row) => sum + row.amount, 0);
  const totalEquity = equity.reduce((sum, row) => sum + row.amount, 0) + retainedEarnings;

  return {
    assets,
    liabilities,
    equity,
    retainedEarnings,
    totalAssets,
    totalLiabilities,
    totalEquity,
    balanced: totalAssets === totalLiabilities + totalEquity,
  };
}

export interface LedgerRow extends ReportLine {
  balance: number;
}

export interface GeneralLedger {
  account: ReportAccount | undefined;
  opening: number;
  rows: LedgerRow[];
  closing: number;
}

export function generalLedger(
  lines: ReportLine[],
  accounts: ReportAccount[],
  accountId: string,
  from: string,
  to: string,
): GeneralLedger {
  const account = accounts.find((candidate) => candidate.id === accountId);
  if (!account) return { account: undefined, opening: 0, rows: [], closing: 0 };

  const sign = DEBIT_NORMAL.includes(account.type) ? 1 : -1;
  const forAccount = lines.filter((line) => line.accountId === accountId);

  let balance = 0;
  for (const line of forAccount) {
    if (line.date < from) balance += sign * (line.debit - line.credit);
  }
  const opening = balance;

  const rows: LedgerRow[] = [];
  const period = forAccount
    .filter((line) => line.date >= from && line.date <= to)
    .sort((a, b) => a.date.localeCompare(b.date) || a.entryNo.localeCompare(b.entryNo));

  for (const line of period) {
    balance += sign * (line.debit - line.credit);
    rows.push({ ...line, balance });
  }

  return { account, opening, rows, closing: balance };
}

export interface PartnerStatementRow extends LedgerRow {}

/** A partner's movement in its control account, with a running balance. */
export function partnerStatement(
  lines: ReportLine[],
  partnerId: string,
  from: string,
  to: string,
): { opening: number; rows: PartnerStatementRow[]; closing: number } {
  const forPartner = lines.filter((line) => line.partnerId === partnerId);
  let balance = 0;
  for (const line of forPartner) {
    if (line.date < from) balance += line.debit - line.credit;
  }
  const opening = balance;

  const rows: PartnerStatementRow[] = [];
  for (const line of forPartner
    .filter((line) => line.date >= from && line.date <= to)
    .sort((a, b) => a.date.localeCompare(b.date))) {
    balance += line.debit - line.credit;
    rows.push({ ...line, balance });
  }
  return { opening, rows, closing: balance };
}

export interface CashPositionRow {
  account: ReportAccount;
  balance: number;
  inflow: number;
  outflow: number;
}

export function cashPosition(
  lines: ReportLine[],
  accounts: ReportAccount[],
  from: string,
  to: string,
): { rows: CashPositionRow[]; total: number } {
  const cashAccounts = accounts.filter((account) => account.is_cash);
  const rows = cashAccounts.map((account) => {
    let balance = 0;
    let inflow = 0;
    let outflow = 0;
    for (const line of lines) {
      if (line.accountId !== account.id || line.date > to) continue;
      balance += line.debit - line.credit;
      if (line.date >= from) {
        inflow += line.debit;
        outflow += line.credit;
      }
    }
    return { account, balance, inflow, outflow };
  });
  return { rows, total: rows.reduce((sum, row) => sum + row.balance, 0) };
}

// --------------------------------------------------------------------------- aging

export const AGING_BUCKETS = ["current", "d1_30", "d31_60", "d61_90", "d90_plus"] as const;
export type AgingBucket = (typeof AGING_BUCKETS)[number];

export function bucketFor(daysLate: number): AgingBucket {
  if (daysLate <= 0) return "current";
  if (daysLate <= 30) return "d1_30";
  if (daysLate <= 60) return "d31_60";
  if (daysLate <= 90) return "d61_90";
  return "d90_plus";
}

export function daysBetween(from: string, to: string): number {
  return Math.round(
    (Date.parse(`${to}T00:00:00Z`) - Date.parse(`${from}T00:00:00Z`)) / 86_400_000,
  );
}

export interface AgingItem {
  documentId: string;
  docNo: string;
  docType: string;
  partnerId: string | null;
  date: string;
  dueDate: string;
  daysLate: number;
  outstanding: number;
  bucket: AgingBucket;
}

export interface AgingReport {
  items: AgingItem[];
  buckets: Record<AgingBucket, number>;
  total: number;
}

interface AgingDocument {
  id: string;
  doc_type: string;
  doc_no: string;
  legal_no: string | null;
  date: string;
  due_date: string | null;
  partner_id: string | null;
  base_total: number;
  status: string;
  payload: { allocations?: Array<{ document_id: string; base_amount: number }> };
}

/**
 * Aging, computed generically over document types rather than over "invoices".
 *
 * A charge is any type whose `settles` matches the side and whose role is `charge`; a settlement
 * is the same side with role `settlement`. Install a credit-note module tomorrow and it joins
 * this report by declaring its type — nothing here changes.
 */
export function aging(
  documents: AgingDocument[],
  chargeTypes: string[],
  settlementTypes: string[],
  asOf: string,
): AgingReport {
  const settled = new Map<string, number>();
  for (const document of documents) {
    if (!settlementTypes.includes(document.doc_type)) continue;
    if (document.status === "void" || document.date > asOf) continue;
    for (const allocation of document.payload.allocations ?? []) {
      settled.set(
        allocation.document_id,
        (settled.get(allocation.document_id) ?? 0) + allocation.base_amount,
      );
    }
  }

  const buckets = Object.fromEntries(AGING_BUCKETS.map((b) => [b, 0])) as Record<
    AgingBucket,
    number
  >;
  const items: AgingItem[] = [];

  for (const document of documents) {
    if (!chargeTypes.includes(document.doc_type)) continue;
    if (document.status === "void" || document.status === "draft") continue;
    if (document.date > asOf) continue;

    const outstanding = document.base_total - (settled.get(document.id) ?? 0);
    if (outstanding <= 0) continue;

    const dueDate = document.due_date || document.date;
    const daysLate = daysBetween(dueDate, asOf);
    const bucket = bucketFor(daysLate);
    buckets[bucket] += outstanding;
    items.push({
      documentId: document.id,
      docNo: document.legal_no ?? document.doc_no,
      docType: document.doc_type,
      partnerId: document.partner_id,
      date: document.date,
      dueDate,
      daysLate,
      outstanding,
      bucket,
    });
  }

  items.sort((a, b) => b.daysLate - a.daysLate);
  return { items, buckets, total: items.reduce((sum, item) => sum + item.outstanding, 0) };
}
