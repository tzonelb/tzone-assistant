import type { ModuleManifest } from "../../core/types";
import type { DocumentTypeDef } from "../documents/types";
import { buildPaymentEntry, buildReceiptEntry } from "./posting";
import { SettlementPage } from "./SettlementPage";

export const RECEIPT: DocumentTypeDef = {
  key: "receipt",
  prefix: "RC",
  labelKey: "payments.receipt.title",
  module: "payments",
  settles: "receivable",
  role: "settlement",
  sequence: 30,
  buildEntry: buildReceiptEntry,
};

export const PAYMENT: DocumentTypeDef = {
  key: "payment",
  prefix: "PM",
  labelKey: "payments.payment.title",
  module: "payments",
  settles: "payable",
  role: "settlement",
  sequence: 40,
  buildEntry: buildPaymentEntry,
};

const ReceiptPage = () => <SettlementPage docType="receipt" />;
const PaymentPage = () => <SettlementPage docType="payment" />;

export const paymentsModule: ModuleManifest = {
  key: "payments",
  name: "Payments",
  nameAr: "المقبوضات والمدفوعات",
  version: "1.0.0",
  summary:
    "Receipts and payments that settle open invoices. Declaring role='settlement' is what makes their allocations reduce the aging reports.",
  category: "Accounting",
  depends: ["documents", "accounting", "partners"],
  sequence: 50,

  setup(ctx) {
    ctx.on("document_types", () => RECEIPT);
    ctx.on("document_types", () => PAYMENT);

    ctx.addRoute({ path: "/receipts", element: ReceiptPage });
    ctx.addRoute({ path: "/payments", element: PaymentPage });

    ctx.addMenu({
      path: "/receipts",
      labelKey: "payments.receipt.title",
      icon: "💵",
      section: "sales",
      sequence: 50,
    });
    ctx.addMenu({
      path: "/payments",
      labelKey: "payments.payment.title",
      icon: "💸",
      section: "sales",
      sequence: 60,
    });

    ctx.addTranslations({
      en: {
        "payments.receipt.title": "Receipts",
        "payments.receipt.subtitle": "Money coming in, matched against open sales invoices.",
        "payments.receipt.new": "New receipt",
        "payments.payment.title": "Payments",
        "payments.payment.subtitle": "Money going out, matched against open purchase invoices.",
        "payments.payment.new": "New payment",
        "payments.amount": "Amount",
        "payments.cashAccount": "Cash / bank account",
        "payments.defaultCash": "Company default",
        "payments.openDocuments": "Open documents",
        "payments.nothingOpen": "This partner has nothing outstanding.",
        "payments.allocated": "Allocated",
        "payments.allocationHint":
          "The amount is spread over the selected documents, oldest first. Allocation affects aging only — the ledger already has the cash movement.",
        "payments.empty": "Nothing recorded yet.",
      },
      ar: {
        "payments.receipt.title": "سندات القبض",
        "payments.receipt.subtitle": "الأموال الواردة، مطابَقة مع فواتير المبيعات المفتوحة.",
        "payments.receipt.new": "سند قبض جديد",
        "payments.payment.title": "سندات الصرف",
        "payments.payment.subtitle": "الأموال الصادرة، مطابَقة مع فواتير المشتريات المفتوحة.",
        "payments.payment.new": "سند صرف جديد",
        "payments.amount": "المبلغ",
        "payments.cashAccount": "حساب النقدية / البنك",
        "payments.defaultCash": "الافتراضي للشركة",
        "payments.openDocuments": "المستندات المفتوحة",
        "payments.nothingOpen": "لا يوجد رصيد مستحق على هذا الطرف.",
        "payments.allocated": "المخصص",
        "payments.allocationHint":
          "يوزَّع المبلغ على المستندات المختارة من الأقدم للأحدث. التخصيص يؤثر على أعمار الديون فقط — الحركة النقدية مُرحَّلة أصلًا في الدفاتر.",
        "payments.empty": "لا توجد سندات بعد.",
      },
    });
  },
};
