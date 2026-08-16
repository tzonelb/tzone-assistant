import type { ModuleManifest } from "../../core/types";
import type { DocumentTypeDef } from "../documents/types";
import { InvoicePage } from "./InvoicePage";
import { buildPurchaseInvoiceEntry, buildSalesInvoiceEntry } from "./posting";

export const SALES_INVOICE: DocumentTypeDef = {
  key: "sales_invoice",
  prefix: "SI",
  labelKey: "invoicing.sales_invoice.title",
  module: "invoicing",
  settles: "receivable",
  role: "charge",
  sequence: 10,
  buildEntry: buildSalesInvoiceEntry,
};

export const PURCHASE_INVOICE: DocumentTypeDef = {
  key: "purchase_invoice",
  prefix: "PI",
  labelKey: "invoicing.purchase_invoice.title",
  module: "invoicing",
  settles: "payable",
  role: "charge",
  sequence: 20,
  buildEntry: buildPurchaseInvoiceEntry,
};

const SalesInvoicePage = () => <InvoicePage docType="sales_invoice" />;
const PurchaseInvoicePage = () => <InvoicePage docType="purchase_invoice" />;

export const invoicingModule: ModuleManifest = {
  key: "invoicing",
  name: "Invoicing",
  nameAr: "الفوترة",
  version: "1.0.0",
  summary:
    "Sales and purchase invoices. Contributes two document types and their posting rules — storage, numbering, sync and aging all come from the documents module.",
  category: "Accounting",
  depends: ["documents", "accounting", "partners", "catalog"],
  sequence: 40,

  setup(ctx) {
    // The entire integration with the rest of the system: declare the types.
    ctx.on("document_types", () => SALES_INVOICE);
    ctx.on("document_types", () => PURCHASE_INVOICE);

    ctx.addRoute({ path: "/sales-invoices", element: SalesInvoicePage });
    ctx.addRoute({ path: "/purchase-invoices", element: PurchaseInvoicePage });

    ctx.addMenu({
      path: "/sales-invoices",
      labelKey: "invoicing.sales_invoice.title",
      icon: "🧾",
      section: "sales",
      sequence: 30,
    });
    ctx.addMenu({
      path: "/purchase-invoices",
      labelKey: "invoicing.purchase_invoice.title",
      icon: "📥",
      section: "sales",
      sequence: 40,
    });

    ctx.addTranslations({
      en: {
        "invoicing.sales_invoice.title": "Sales invoices",
        "invoicing.sales_invoice.subtitle": "What customers owe you, and the revenue behind it.",
        "invoicing.sales_invoice.new": "New sales invoice",
        "invoicing.purchase_invoice.title": "Purchase invoices",
        "invoicing.purchase_invoice.subtitle": "What you owe suppliers, and the cost behind it.",
        "invoicing.purchase_invoice.new": "New purchase invoice",
        "invoicing.pickPartner": "Choose a partner",
        "invoicing.item": "Item",
        "invoicing.freeText": "Free text",
        "invoicing.description": "Description",
        "invoicing.quantity": "Qty",
        "invoicing.unitPrice": "Unit price",
        "invoicing.taxRate": "Tax %",
        "invoicing.lineTotal": "Line total",
        "invoicing.addLine": "Add line",
        "invoicing.net": "Net",
        "invoicing.tax": "Tax",
        "invoicing.memo": "Note",
        "invoicing.empty": "No invoices yet.",
      },
      ar: {
        "invoicing.sales_invoice.title": "فواتير المبيعات",
        "invoicing.sales_invoice.subtitle": "ما على العملاء لك، والإيراد المقابل له.",
        "invoicing.sales_invoice.new": "فاتورة مبيعات جديدة",
        "invoicing.purchase_invoice.title": "فواتير المشتريات",
        "invoicing.purchase_invoice.subtitle": "ما عليك للموردين، والتكلفة المقابلة له.",
        "invoicing.purchase_invoice.new": "فاتورة مشتريات جديدة",
        "invoicing.pickPartner": "اختر الطرف",
        "invoicing.item": "الصنف",
        "invoicing.freeText": "نص حر",
        "invoicing.description": "الوصف",
        "invoicing.quantity": "الكمية",
        "invoicing.unitPrice": "سعر الوحدة",
        "invoicing.taxRate": "الضريبة %",
        "invoicing.lineTotal": "إجمالي السطر",
        "invoicing.addLine": "إضافة سطر",
        "invoicing.net": "الصافي",
        "invoicing.tax": "الضريبة",
        "invoicing.memo": "ملاحظة",
        "invoicing.empty": "لا توجد فواتير بعد.",
      },
    });
  },
};
