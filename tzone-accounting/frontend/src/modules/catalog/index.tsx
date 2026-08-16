import { useLiveQuery } from "dexie-react-hooks";
import { useState } from "react";
import { table } from "../../core/db";
import { useI18n } from "../../core/i18n";
import { formatAmount, parseAmount } from "../../core/money";
import { remove, save } from "../../core/repository";
import { useBaseCurrency } from "../../core/settings";
import { Card, DataTable, Field, Money, Page, Select, Toolbar } from "../../core/ui";
import type { Envelope, ModuleManifest } from "../../core/types";
import { useAccounts } from "../accounting/hooks";

export interface Item extends Envelope {
  sku: string;
  name_en: string;
  name_ar: string;
  kind: "product" | "service";
  unit: string;
  sale_price: number;
  purchase_price: number;
  /** Basis points: 1500 === 15%. Integer, so tax never drifts. */
  tax_rate_bp: number;
  income_account_id: string | null;
  expense_account_id: string | null;
  is_active: boolean;
}

export function useItems(): Item[] {
  return (
    useLiveQuery(async () => {
      const rows = await table<Item>("items").toArray();
      return rows.filter((row) => !row.deleted).sort((a, b) => a.name_en.localeCompare(b.name_en));
    }, []) ?? []
  );
}

function CatalogPage() {
  const { t, pick } = useI18n();
  const items = useItems();
  const accounts = useAccounts().filter((account) => !account.is_group);
  const currency = useBaseCurrency();
  const [editing, setEditing] = useState<Partial<Item> | null>(null);

  async function submit() {
    if (!editing?.name_en) return;
    await save<Partial<Item>>("item", {
      sku: "",
      name_ar: "",
      kind: "product",
      unit: "pcs",
      sale_price: 0,
      purchase_price: 0,
      tax_rate_bp: 0,
      income_account_id: null,
      expense_account_id: null,
      is_active: true,
      ...editing,
    });
    setEditing(null);
  }

  return (
    <Page
      title={t("catalog.title")}
      subtitle={t("catalog.subtitle")}
      actions={
        <button type="button" className="primary" onClick={() => setEditing({ kind: "product" })}>
          {t("catalog.new")}
        </button>
      }
    >
      <Card>
        <DataTable
          rows={items}
          empty={t("catalog.empty")}
          columns={[
            { key: "sku", header: t("catalog.sku"), render: (row) => <code>{row.sku}</code> },
            { key: "name", header: t("catalog.name"), render: (row) => pick(row) },
            { key: "kind", header: t("catalog.kind"), render: (row) => t(`catalog.kind.${row.kind}`) },
            {
              key: "price",
              header: t("catalog.salePrice"),
              align: "end",
              render: (row) => <Money value={row.sale_price} currency={currency} />,
            },
            {
              key: "tax",
              header: t("catalog.tax"),
              align: "end",
              render: (row) => `${(row.tax_rate_bp / 100).toFixed(2)}%`,
            },
            {
              key: "actions",
              header: "",
              align: "end",
              render: (row) => (
                <span className="row-actions">
                  <button type="button" onClick={() => setEditing(row)}>
                    {t("action.edit")}
                  </button>
                  <button type="button" className="danger" onClick={() => void remove("item", row.id)}>
                    {t("action.delete")}
                  </button>
                </span>
              ),
            },
          ]}
        />
      </Card>

      {editing ? (
        <Card title={editing.id ? t("catalog.edit") : t("catalog.new")}>
          <div className="grid two">
            <Field label={t("catalog.sku")}>
              <input
                value={editing.sku ?? ""}
                onChange={(e) => setEditing({ ...editing, sku: e.target.value })}
              />
            </Field>
            <Field label={t("catalog.kind")}>
              <Select
                value={editing.kind ?? "product"}
                onChange={(value) => setEditing({ ...editing, kind: value })}
                options={[
                  { value: "product" as const, label: t("catalog.kind.product") },
                  { value: "service" as const, label: t("catalog.kind.service") },
                ]}
              />
            </Field>
            <Field label={t("catalog.nameEn")}>
              <input
                value={editing.name_en ?? ""}
                onChange={(e) => setEditing({ ...editing, name_en: e.target.value })}
              />
            </Field>
            <Field label={t("catalog.nameAr")}>
              <input
                value={editing.name_ar ?? ""}
                onChange={(e) => setEditing({ ...editing, name_ar: e.target.value })}
              />
            </Field>
            <Field label={t("catalog.salePrice")}>
              <input
                className="amount"
                inputMode="decimal"
                value={formatAmount(editing.sale_price ?? 0, currency.decimals)}
                onChange={(e) =>
                  setEditing({ ...editing, sale_price: parseAmount(e.target.value, currency.decimals) })
                }
              />
            </Field>
            <Field label={t("catalog.purchasePrice")}>
              <input
                className="amount"
                inputMode="decimal"
                value={formatAmount(editing.purchase_price ?? 0, currency.decimals)}
                onChange={(e) =>
                  setEditing({
                    ...editing,
                    purchase_price: parseAmount(e.target.value, currency.decimals),
                  })
                }
              />
            </Field>
            <Field label={t("catalog.tax")} hint={t("catalog.taxHint")}>
              <input
                className="amount"
                inputMode="decimal"
                value={((editing.tax_rate_bp ?? 0) / 100).toString()}
                onChange={(e) =>
                  setEditing({ ...editing, tax_rate_bp: Math.round(Number(e.target.value || 0) * 100) })
                }
              />
            </Field>
            <Field label={t("catalog.incomeAccount")}>
              <Select
                value={editing.income_account_id ?? ""}
                onChange={(value) => setEditing({ ...editing, income_account_id: value || null })}
                placeholder={t("catalog.defaultAccount")}
                options={accounts
                  .filter((account) => account.type === "income")
                  .map((account) => ({ value: account.id, label: `${account.code} — ${pick(account)}` }))}
              />
            </Field>
            <Field label={t("catalog.expenseAccount")}>
              <Select
                value={editing.expense_account_id ?? ""}
                onChange={(value) => setEditing({ ...editing, expense_account_id: value || null })}
                placeholder={t("catalog.defaultAccount")}
                options={accounts
                  .filter((account) => account.type === "expense" || account.type === "asset")
                  .map((account) => ({ value: account.id, label: `${account.code} — ${pick(account)}` }))}
              />
            </Field>
          </div>
          <Toolbar>
            <button type="button" className="primary" onClick={() => void submit()}>
              {t("action.save")}
            </button>
            <button type="button" onClick={() => setEditing(null)}>
              {t("action.cancel")}
            </button>
          </Toolbar>
        </Card>
      ) : null}
    </Page>
  );
}

export const catalogModule: ModuleManifest = {
  key: "catalog",
  name: "Catalog",
  nameAr: "الأصناف والخدمات",
  version: "1.0.0",
  summary: "Products and services with default prices, tax rates and posting accounts.",
  category: "Accounting",
  depends: ["accounting"],
  sequence: 25,

  setup(ctx) {
    ctx.addEntity({ name: "item", store: "items", indexes: "id, sku, kind" });
    ctx.addRoute({ path: "/catalog", element: CatalogPage });
    ctx.addMenu({ path: "/catalog", labelKey: "menu.catalog", icon: "📦", section: "sales", sequence: 20 });
    ctx.addTranslations({
      en: {
        "menu.catalog": "Items & services",
        "catalog.title": "Items & services",
        "catalog.subtitle": "What you sell and buy, and where each line posts.",
        "catalog.new": "New item",
        "catalog.edit": "Edit item",
        "catalog.empty": "No items yet.",
        "catalog.sku": "Code",
        "catalog.name": "Name",
        "catalog.nameEn": "Name (English)",
        "catalog.nameAr": "Name (Arabic)",
        "catalog.kind": "Type",
        "catalog.kind.product": "Product",
        "catalog.kind.service": "Service",
        "catalog.salePrice": "Sale price",
        "catalog.purchasePrice": "Purchase price",
        "catalog.tax": "Tax %",
        "catalog.taxHint": "Applied per line on invoices.",
        "catalog.incomeAccount": "Income account",
        "catalog.expenseAccount": "Expense / inventory account",
        "catalog.defaultAccount": "Use the company default",
      },
      ar: {
        "menu.catalog": "الأصناف والخدمات",
        "catalog.title": "الأصناف والخدمات",
        "catalog.subtitle": "ما تبيعه وتشتريه، والحساب الذي يُرحَّل إليه كل سطر.",
        "catalog.new": "صنف جديد",
        "catalog.edit": "تعديل الصنف",
        "catalog.empty": "لا توجد أصناف بعد.",
        "catalog.sku": "الرمز",
        "catalog.name": "الاسم",
        "catalog.nameEn": "الاسم (إنجليزي)",
        "catalog.nameAr": "الاسم (عربي)",
        "catalog.kind": "النوع",
        "catalog.kind.product": "منتج",
        "catalog.kind.service": "خدمة",
        "catalog.salePrice": "سعر البيع",
        "catalog.purchasePrice": "سعر الشراء",
        "catalog.tax": "الضريبة %",
        "catalog.taxHint": "تُطبَّق على كل سطر في الفواتير.",
        "catalog.incomeAccount": "حساب الإيراد",
        "catalog.expenseAccount": "حساب المصروف / المخزون",
        "catalog.defaultAccount": "استخدام الافتراضي للشركة",
      },
    });
  },
};
