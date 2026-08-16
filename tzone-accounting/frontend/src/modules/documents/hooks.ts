import { useLiveQuery } from "dexie-react-hooks";
import { table } from "../../core/db";
import type { BusinessDocument } from "./types";

export function useDocuments(docTypes?: string[]): BusinessDocument[] {
  return (
    useLiveQuery(async () => {
      const rows = await table<BusinessDocument>("documents").toArray();
      return rows
        .filter((row) => !row.deleted && (!docTypes || docTypes.includes(row.doc_type)))
        .sort((a, b) => b.date.localeCompare(a.date) || b.doc_no.localeCompare(a.doc_no));
    }, [docTypes?.join(",") ?? ""]) ?? []
  );
}

/** Charge documents of a side that still have a balance — what a settlement screen offers. */
export function useOpenDocuments(chargeTypes: string[]): BusinessDocument[] {
  const all = useDocuments();
  const settled = new Map<string, number>();
  for (const document of all) {
    if (document.status === "void") continue;
    for (const allocation of document.payload.allocations ?? []) {
      settled.set(
        allocation.document_id,
        (settled.get(allocation.document_id) ?? 0) + allocation.base_amount,
      );
    }
  }
  return all.filter(
    (document) =>
      chargeTypes.includes(document.doc_type) &&
      document.status !== "void" &&
      document.status !== "draft" &&
      document.base_total - (settled.get(document.id) ?? 0) > 0,
  );
}
