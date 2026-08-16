import type { ModuleManifest } from "../../core/types";

export * from "./hooks";
export * from "./service";
export * from "./types";

export const documentsModule: ModuleManifest = {
  key: "documents",
  name: "Documents",
  nameAr: "المستندات",
  version: "1.0.0",
  summary:
    "The pluggable paperwork layer: one storage model, one posting pipeline, and a registry any module can add a document type to.",
  category: "Accounting",
  depends: ["base", "partners", "accounting"],
  sequence: 30,

  setup(ctx) {
    ctx.addEntity({
      name: "document",
      store: "documents",
      indexes: "id, doc_type, date, partner_id, status, doc_no",
    });

    ctx.addTranslations({
      en: {
        "documents.number": "Number",
        "documents.legalNumber": "Official no.",
        "documents.date": "Date",
        "documents.dueDate": "Due",
        "documents.partner": "Partner",
        "documents.total": "Total",
        "documents.outstanding": "Outstanding",
        "documents.status": "Status",
        "documents.notSyncedYet": "Official number is assigned when this device next syncs.",
      },
      ar: {
        "documents.number": "الرقم",
        "documents.legalNumber": "الرقم الرسمي",
        "documents.date": "التاريخ",
        "documents.dueDate": "الاستحقاق",
        "documents.partner": "الطرف",
        "documents.total": "الإجمالي",
        "documents.outstanding": "المتبقي",
        "documents.status": "الحالة",
        "documents.notSyncedYet": "يُخصَّص الرقم الرسمي عند أول مزامنة لهذا الجهاز.",
      },
    });
  },
};
