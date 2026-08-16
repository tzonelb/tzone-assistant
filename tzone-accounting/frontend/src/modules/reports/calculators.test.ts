/** Report calculators, over the same books the backend suite uses — the two must agree. */

import { describe, expect, it } from "vitest";
import type { AccountType } from "../../core/types";
import type { JournalEntry } from "../accounting/types";
import {
  aging,
  balanceSheet,
  bucketFor,
  cashPosition,
  generalLedger,
  profitAndLoss,
  toReportLines,
  trialBalance,
  type ReportAccount,
} from "./calculators";

const account = (
  id: string,
  code: string,
  type: AccountType,
  extra: Partial<ReportAccount> = {},
): ReportAccount => ({
  id,
  code,
  name_en: code,
  name_ar: code,
  type,
  is_group: false,
  is_cash: false,
  ...extra,
});

const ACCOUNTS: ReportAccount[] = [
  account("acc-1120", "1120", "asset", { is_cash: true }),
  account("acc-1130", "1130", "asset"),
  account("acc-3100", "3100", "equity"),
  account("acc-4100", "4100", "income"),
  account("acc-5300", "5300", "expense"),
];

function entry(id: string, date: string, pairs: Array<[string, number, number]>): JournalEntry {
  return {
    id,
    entry_no: `JV-${id}`,
    date,
    memo: id,
    currency: "USD",
    fx_rate: 1_000_000,
    status: "posted",
    source_kind: "manual",
    source_id: null,
    reverses_id: null,
    created_by: "",
    lines: pairs.map(([accountId, debit, credit]) => ({
      account_id: accountId,
      partner_id: null,
      description: "",
      debit,
      credit,
      base_debit: debit,
      base_credit: credit,
    })),
    rev: 1,
    updated_at: `${date}T00:00:00.000Z`,
    deleted: false,
    origin: "test",
  };
}

// Capital in, a credit sale, a collection, rent paid — the same month as the backend tests.
const BOOKS: JournalEntry[] = [
  entry("cap", "2026-03-01", [["acc-1120", 100_000, 0], ["acc-3100", 0, 100_000]]),
  entry("sale", "2026-03-05", [["acc-1130", 50_000, 0], ["acc-4100", 0, 50_000]]),
  entry("collect", "2026-03-10", [["acc-1120", 30_000, 0], ["acc-1130", 0, 30_000]]),
  entry("rent", "2026-03-15", [["acc-5300", 12_000, 0], ["acc-1120", 0, 12_000]]),
];

const LINES = toReportLines(BOOKS);

describe("trial balance", () => {
  it("balances", () => {
    const report = trialBalance(LINES, ACCOUNTS, "2026-03-01", "2026-03-31");
    expect(report.balanced).toBe(true);
    expect(report.totalDebit).toBe(192_000);
    expect(report.totalDebit).toBe(report.totalCredit);
  });

  it("excludes drafts and voided entries", () => {
    const withDraft = [...BOOKS, { ...entry("d", "2026-03-02", [["acc-1120", 999, 0], ["acc-4100", 0, 999]]), status: "draft" as const }];
    const report = trialBalance(toReportLines(withDraft), ACCOUNTS, "2026-03-01", "2026-03-31");
    expect(report.totalDebit).toBe(192_000);
  });
});

describe("profit and loss", () => {
  it("nets income against expenses for the period", () => {
    const report = profitAndLoss(LINES, ACCOUNTS, "2026-03-01", "2026-03-31");
    expect(report.totalIncome).toBe(50_000);
    expect(report.totalExpense).toBe(12_000);
    expect(report.netProfit).toBe(38_000);
  });

  it("is empty outside the period", () => {
    expect(profitAndLoss(LINES, ACCOUNTS, "2026-04-01", "2026-04-30").netProfit).toBe(0);
  });
});

describe("balance sheet", () => {
  it("balances and carries the period profit into equity", () => {
    const report = balanceSheet(LINES, ACCOUNTS, "2026-03-31");
    expect(report.totalAssets).toBe(138_000); // bank 118,000 + receivable 20,000
    expect(report.retainedEarnings).toBe(38_000);
    expect(report.totalEquity).toBe(138_000);
    expect(report.balanced).toBe(true);
  });

  it("still balances mid-period", () => {
    expect(balanceSheet(LINES, ACCOUNTS, "2026-03-07").balanced).toBe(true);
  });
});

describe("general ledger", () => {
  it("carries a running balance", () => {
    const report = generalLedger(LINES, ACCOUNTS, "acc-1120", "2026-03-01", "2026-03-31");
    expect(report.opening).toBe(0);
    expect(report.rows.map((row) => row.balance)).toEqual([100_000, 130_000, 118_000]);
    expect(report.closing).toBe(118_000);
  });

  it("puts everything before the period into the opening balance", () => {
    const report = generalLedger(LINES, ACCOUNTS, "acc-1120", "2026-03-10", "2026-03-31");
    expect(report.opening).toBe(100_000);
    expect(report.closing).toBe(118_000);
  });

  it("shows a credit-normal account as positive when it is in credit", () => {
    const report = generalLedger(LINES, ACCOUNTS, "acc-4100", "2026-03-01", "2026-03-31");
    expect(report.closing).toBe(50_000);
  });
});

describe("cash position", () => {
  it("reports the balance and the period flows of cash accounts", () => {
    const report = cashPosition(LINES, ACCOUNTS, "2026-03-01", "2026-03-31");
    expect(report.total).toBe(118_000);
    expect(report.rows[0].inflow).toBe(130_000);
    expect(report.rows[0].outflow).toBe(12_000);
  });
});

describe("aging", () => {
  const invoice = (id: string, date: string, due: string, total: number) => ({
    id,
    doc_type: "sales_invoice",
    doc_no: id,
    legal_no: null,
    date,
    due_date: due,
    partner_id: "p1",
    base_total: total,
    status: "posted",
    payload: {},
  });

  const receipt = (id: string, date: string, target: string, amount: number) => ({
    id,
    doc_type: "receipt",
    doc_no: id,
    legal_no: null,
    date,
    due_date: null,
    partner_id: "p1",
    base_total: amount,
    status: "posted",
    payload: { allocations: [{ document_id: target, base_amount: amount }] },
  });

  it("buckets by days past due", () => {
    expect(bucketFor(0)).toBe("current");
    expect(bucketFor(-5)).toBe("current");
    expect(bucketFor(1)).toBe("d1_30");
    expect(bucketFor(30)).toBe("d1_30");
    expect(bucketFor(31)).toBe("d31_60");
    expect(bucketFor(91)).toBe("d90_plus");
  });

  it("subtracts settlements from the charge", () => {
    const report = aging(
      [invoice("i1", "2026-01-10", "2026-01-20", 40_000), receipt("r1", "2026-02-01", "i1", 15_000)],
      ["sales_invoice"],
      ["receipt"],
      "2026-03-01",
    );
    expect(report.total).toBe(25_000);
    expect(report.buckets.d31_60).toBe(25_000);
    expect(report.items[0].daysLate).toBe(40);
  });

  it("drops a fully settled document", () => {
    const report = aging(
      [invoice("i1", "2026-01-10", "2026-01-20", 10_000), receipt("r1", "2026-02-01", "i1", 10_000)],
      ["sales_invoice"],
      ["receipt"],
      "2026-03-01",
    );
    expect(report.items).toEqual([]);
  });

  it("ignores a settlement of the other side", () => {
    const report = aging(
      [invoice("i1", "2026-01-10", "2026-01-20", 10_000), receipt("r1", "2026-02-01", "i1", 10_000)],
      ["sales_invoice"],
      [], // payments module not installed for this side
      "2026-03-01",
    );
    expect(report.total).toBe(10_000);
  });

  it("ignores documents dated after the report date", () => {
    const report = aging([invoice("i1", "2026-04-10", "2026-04-20", 10_000)], ["sales_invoice"], [], "2026-03-01");
    expect(report.items).toEqual([]);
  });
});
