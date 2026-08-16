/** Posting rules: does each document type produce the entry docs/ACCOUNTING_MODEL.md §3 promises? */

import { describe, expect, it } from "vitest";
import { RATE_ONE } from "../../core/money";
import { totals, validateDraft } from "../accounting/posting";
import type { BusinessDocument, PostingContext } from "../documents/types";
import { buildReceiptEntry, buildPaymentEntry } from "../payments/posting";
import { buildPurchaseInvoiceEntry, buildSalesInvoiceEntry } from "./posting";

const context: PostingContext = {
  accountRoles: {
    receivable: "acc-1130",
    payable: "acc-2110",
    tax_payable: "acc-2120",
    tax_receivable: "acc-1150",
    sales: "acc-4100",
    cogs: "acc-5100",
    cash: "acc-1110",
  },
  itemAccount: (_id, side) => (side === "income" ? "acc-4100" : "acc-5100"),
  partnerControl: (_id, side) => (side === "receivable" ? "acc-1130" : "acc-2110"),
};

function document(overrides: Partial<BusinessDocument> = {}): BusinessDocument {
  return {
    id: "doc-1",
    doc_type: "sales_invoice",
    doc_no: "SI-A7-000001",
    legal_no: null,
    date: "2026-03-01",
    due_date: "2026-03-31",
    partner_id: "p1",
    currency: "USD",
    fx_rate: RATE_ONE,
    total: 0,
    base_total: 0,
    status: "posted",
    journal_entry_id: null,
    memo: "",
    payload: {},
    rev: 1,
    updated_at: "2026-03-01T00:00:00.000Z",
    deleted: false,
    origin: "test",
    ...overrides,
  };
}

const line = (net: number, taxBp = 0) => ({
  item_id: "i1",
  description: "widget",
  quantity: 1,
  unit_price: net,
  tax_rate_bp: taxBp,
});

function balanced(draftLines: ReturnType<typeof buildSalesInvoiceEntry>["lines"]) {
  const sum = totals(draftLines);
  return sum.debit === sum.credit && sum.baseDebit === sum.baseCredit;
}

describe("sales invoice", () => {
  it("debits receivable and credits income, and balances", () => {
    const draft = buildSalesInvoiceEntry(
      document({ total: 10_000, payload: { lines: [line(10_000)] } }),
      context,
    );
    expect(balanced(draft.lines)).toBe(true);
    expect(draft.lines.find((l) => l.account_id === "acc-1130")?.debit).toBe(10_000);
    expect(draft.lines.find((l) => l.account_id === "acc-4100")?.credit).toBe(10_000);
  });

  it("splits tax onto the tax payable account", () => {
    const draft = buildSalesInvoiceEntry(
      document({ total: 11_500, payload: { lines: [line(10_000, 1_500)] } }),
      context,
    );
    expect(balanced(draft.lines)).toBe(true);
    expect(draft.lines.find((l) => l.account_id === "acc-2120")?.credit).toBe(1_500);
    expect(draft.lines.find((l) => l.account_id === "acc-1130")?.debit).toBe(11_500);
  });

  it("balances across many lines with different tax rates", () => {
    const lines = [line(3_333, 1_500), line(1_111, 500), line(7_777, 0)];
    const tax = 500 + 56 + 0; // 15% of 33.33, 5% of 11.11
    const draft = buildSalesInvoiceEntry(
      document({ total: 3_333 + 1_111 + 7_777 + tax, payload: { lines } }),
      context,
    );
    expect(balanced(draft.lines)).toBe(true);
  });

  it("produces an entry that passes the shared invariants", () => {
    const draft = buildSalesInvoiceEntry(
      document({ total: 10_000, payload: { lines: [line(10_000)] } }),
      context,
    );
    expect(validateDraft(draft)).toEqual([]);
  });
});

describe("purchase invoice", () => {
  it("debits the expense account and credits payable", () => {
    const draft = buildPurchaseInvoiceEntry(
      document({
        doc_type: "purchase_invoice",
        total: 10_000,
        payload: { lines: [line(10_000)] },
      }),
      context,
    );
    expect(balanced(draft.lines)).toBe(true);
    expect(draft.lines.find((l) => l.account_id === "acc-5100")?.debit).toBe(10_000);
    expect(draft.lines.find((l) => l.account_id === "acc-2110")?.credit).toBe(10_000);
  });

  it("puts input tax on the receivable side", () => {
    const draft = buildPurchaseInvoiceEntry(
      document({
        doc_type: "purchase_invoice",
        total: 11_500,
        payload: { lines: [line(10_000, 1_500)] },
      }),
      context,
    );
    expect(draft.lines.find((l) => l.account_id === "acc-1150")?.debit).toBe(1_500);
    expect(balanced(draft.lines)).toBe(true);
  });
});

describe("settlements", () => {
  it("a receipt moves money from receivable into cash", () => {
    const draft = buildReceiptEntry(
      document({ doc_type: "receipt", total: 5_000, payload: { allocations: [] } }),
      context,
    );
    expect(draft.lines.find((l) => l.account_id === "acc-1110")?.debit).toBe(5_000);
    expect(draft.lines.find((l) => l.account_id === "acc-1130")?.credit).toBe(5_000);
    expect(balanced(draft.lines)).toBe(true);
  });

  it("a payment moves money from cash into payable", () => {
    const draft = buildPaymentEntry(
      document({ doc_type: "payment", total: 5_000 }),
      context,
    );
    expect(draft.lines.find((l) => l.account_id === "acc-2110")?.debit).toBe(5_000);
    expect(draft.lines.find((l) => l.account_id === "acc-1110")?.credit).toBe(5_000);
  });

  it("allocations add no journal lines — the cash movement already says it", () => {
    const withAllocations = buildReceiptEntry(
      document({
        doc_type: "receipt",
        total: 5_000,
        payload: { allocations: [{ document_id: "x", amount: 5_000, base_amount: 5_000 }] },
      }),
      context,
    );
    expect(withAllocations.lines).toHaveLength(2);
  });
});

describe("foreign currency", () => {
  it("keeps the entry balanced in both the document and base currency", () => {
    const draft = buildSalesInvoiceEntry(
      document({ total: 10_000, fx_rate: 1_500_000, payload: { lines: [line(10_000)] } }),
      context,
    );
    const sum = totals(draft.lines);
    expect(sum.debit).toBe(10_000);
    expect(sum.baseDebit).toBe(15_000);
    expect(balanced(draft.lines)).toBe(true);
  });
});
