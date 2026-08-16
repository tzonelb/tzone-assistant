/**
 * Turning paperwork into ledger movements.
 *
 * This file knows nothing about invoices or receipts. It looks up the document type another
 * module registered, asks it for the journal entry, posts it, and links the two. Adding a
 * document type therefore requires no change here.
 */

import { nextNumber } from "../../core/numbering";
import { getRegistry } from "../../core/registry";
import { get, list, save } from "../../core/repository";
import { loadSettings } from "../../core/settings";
import { taxOf, toBase } from "../../core/money";
import { postEntry, voidEntry } from "../accounting/service";
import type { Item } from "../catalog";
import type { Partner } from "../partners";
import type {
  Allocation,
  BusinessDocument,
  DocumentLine,
  DocumentTypeDef,
  PostingContext,
} from "./types";

export function documentTypes(): Map<string, DocumentTypeDef> {
  const collected = getRegistry().hooks.collect<DocumentTypeDef>("document_types");
  return new Map(
    collected
      .sort((a, b) => (a.sequence ?? 100) - (b.sequence ?? 100))
      .map((definition) => [definition.key, definition]),
  );
}

export function documentType(key: string): DocumentTypeDef {
  const definition = documentTypes().get(key);
  if (!definition) throw new Error(`no installed module provides document type '${key}'`);
  return definition;
}

/** Line arithmetic. Integer throughout, so the printed total always equals the posted total. */
export function lineNet(line: DocumentLine): number {
  return line.quantity * line.unit_price;
}

export function documentTotals(lines: DocumentLine[]) {
  let net = 0;
  let tax = 0;
  for (const line of lines) {
    const lineTotal = lineNet(line);
    net += lineTotal;
    tax += taxOf(lineTotal, line.tax_rate_bp);
  }
  return { net, tax, total: net + tax };
}

async function buildContext(): Promise<PostingContext> {
  const settings = await loadSettings();
  const roles = settings.account_roles ?? {};
  const items = new Map((await list<Item>("item")).map((item) => [item.id, item]));
  const partners = new Map((await list<Partner>("partner")).map((partner) => [partner.id, partner]));

  return {
    accountRoles: roles,
    itemAccount(itemId, side) {
      const item = itemId ? items.get(itemId) : undefined;
      const override = side === "income" ? item?.income_account_id : item?.expense_account_id;
      return override ?? roles[side === "income" ? "sales" : "cogs"] ?? null;
    },
    partnerControl(partnerId, side) {
      const partner = partnerId ? partners.get(partnerId) : undefined;
      const override =
        side === "receivable" ? partner?.receivable_account_id : partner?.payable_account_id;
      return override ?? roles[side] ?? "";
    },
  };
}

export interface DocumentDraft {
  id?: string;
  doc_type: string;
  date: string;
  due_date?: string | null;
  partner_id?: string | null;
  currency: string;
  fx_rate: number;
  memo?: string;
  lines?: DocumentLine[];
  allocations?: Allocation[];
  cash_account_id?: string | null;
  /** Settlement documents carry an amount instead of lines. */
  amount?: number;
}

/**
 * Post a document: number it, derive its totals, build and post its journal entry, then store
 * the document pointing at that entry.
 *
 * The document is written after the entry so a failure mid-way leaves an orphan entry (visible
 * and correctable in the journal) rather than a document that claims to be posted but moved no
 * money.
 */
export async function postDocument(draft: DocumentDraft): Promise<BusinessDocument> {
  const definition = documentType(draft.doc_type);
  const context = await buildContext();
  const lines = draft.lines ?? [];
  const sums = lines.length
    ? documentTotals(lines)
    : { net: draft.amount ?? 0, tax: 0, total: draft.amount ?? 0 };

  const document: BusinessDocument = {
    id: draft.id ?? "",
    doc_type: draft.doc_type,
    doc_no: await nextNumber(draft.doc_type, definition.prefix),
    legal_no: null,
    date: draft.date,
    due_date: draft.due_date ?? null,
    partner_id: draft.partner_id ?? null,
    currency: draft.currency,
    fx_rate: draft.fx_rate,
    total: sums.total,
    base_total: toBase(sums.total, draft.fx_rate),
    status: "posted",
    journal_entry_id: null,
    memo: draft.memo ?? "",
    payload: {
      lines,
      allocations: draft.allocations ?? [],
      net_total: sums.net,
      tax_total: sums.tax,
      cash_account_id: draft.cash_account_id ?? null,
    },
    rev: 0,
    updated_at: "",
    deleted: false,
    origin: "",
  };

  const entry = await postEntry(definition.buildEntry(document, context));
  const stored = await save<Partial<BusinessDocument>>("document", {
    ...document,
    id: draft.id || undefined,
    journal_entry_id: entry.id,
  });

  await refreshSettlementStatuses(draft.allocations ?? []);
  getRegistry().hooks.emit("document_posted", stored);
  return stored as BusinessDocument;
}

/** Void a document by reversing its entry; the document itself is marked, never deleted. */
export async function voidDocument(documentId: string, onDate: string): Promise<void> {
  const document = await get<BusinessDocument>("document", documentId);
  if (!document) return;
  if (document.journal_entry_id) await voidEntry(document.journal_entry_id, onDate);
  await save<Partial<BusinessDocument>>("document", { ...document, status: "void" });
  await refreshSettlementStatuses(document.payload.allocations ?? []);
}

/**
 * Recompute `posted → partial → paid` on the charge documents a settlement touched.
 *
 * The status is presentation only — the ledger already reflects the money. Keeping it derived
 * rather than typed means it cannot drift away from the allocations.
 */
export async function refreshSettlementStatuses(allocations: Allocation[]): Promise<void> {
  if (!allocations.length) return;
  const documents = await list<BusinessDocument>("document");
  const settled = new Map<string, number>();
  for (const document of documents) {
    if (document.status === "void") continue;
    for (const allocation of document.payload.allocations ?? []) {
      settled.set(
        allocation.document_id,
        (settled.get(allocation.document_id) ?? 0) + allocation.base_amount,
      );
    }
  }

  for (const allocation of allocations) {
    const target = documents.find((candidate) => candidate.id === allocation.document_id);
    if (!target || target.status === "void" || target.status === "draft") continue;
    const paid = settled.get(target.id) ?? 0;
    const status = paid >= target.base_total ? "paid" : paid > 0 ? "partial" : "posted";
    if (status !== target.status) {
      await save<Partial<BusinessDocument>>("document", { ...target, status });
    }
  }
}

/** Outstanding balance of a charge document, in base currency. */
export function outstandingOf(document: BusinessDocument, allDocuments: BusinessDocument[]): number {
  let settled = 0;
  for (const candidate of allDocuments) {
    if (candidate.status === "void") continue;
    for (const allocation of candidate.payload.allocations ?? []) {
      if (allocation.document_id === document.id) settled += allocation.base_amount;
    }
  }
  return document.base_total - settled;
}
