import { useLiveQuery } from "dexie-react-hooks";
import { useState } from "react";
import { table } from "../../core/db";
import { useI18n } from "../../core/i18n";
import { remove, save } from "../../core/repository";
import { Card, DataTable, Field, Page, Select, Toolbar } from "../../core/ui";
import type { Envelope, ModuleManifest } from "../../core/types";

export interface Partner extends Envelope {
  code: string;
  name: string;
  kind: "customer" | "supplier" | "both";
  phone: string;
  email: string;
  tax_number: string;
  address: string;
  receivable_account_id: string | null;
  payable_account_id: string | null;
  credit_limit: number;
  is_active: boolean;
}

export function usePartners(): Partner[] {
  return (
    useLiveQuery(async () => {
      const rows = await table<Partner>("partners").toArray();
      return rows.filter((row) => !row.deleted).sort((a, b) => a.name.localeCompare(b.name));
    }, []) ?? []
  );
}

export function usePartnerMap(): Map<string, Partner> {
  return new Map(usePartners().map((partner) => [partner.id, partner]));
}

function PartnersPage() {
  const { t } = useI18n();
  const partners = usePartners();
  const [editing, setEditing] = useState<Partial<Partner> | null>(null);
  const [search, setSearch] = useState("");

  const visible = partners.filter(
    (partner) =>
      !search ||
      partner.name.toLowerCase().includes(search.toLowerCase()) ||
      partner.phone.includes(search),
  );

  async function submit() {
    if (!editing?.name) return;
    await save<Partial<Partner>>("partner", {
      kind: "customer",
      code: "",
      phone: "",
      email: "",
      tax_number: "",
      address: "",
      receivable_account_id: null,
      payable_account_id: null,
      credit_limit: 0,
      is_active: true,
      ...editing,
    });
    setEditing(null);
  }

  return (
    <Page
      title={t("partners.title")}
      subtitle={t("partners.subtitle")}
      actions={
        <button type="button" className="primary" onClick={() => setEditing({ kind: "customer" })}>
          {t("partners.new")}
        </button>
      }
    >
      <Card>
        <Toolbar>
          <input
            placeholder={t("partners.search")}
            value={search}
            onChange={(e) => setSearch(e.target.value)}
          />
        </Toolbar>
        <DataTable
          rows={visible}
          empty={t("partners.empty")}
          columns={[
            { key: "name", header: t("partners.name"), render: (row) => row.name },
            { key: "kind", header: t("partners.kind"), render: (row) => t(`partners.kind.${row.kind}`) },
            { key: "phone", header: t("partners.phone"), render: (row) => row.phone },
            { key: "tax", header: t("partners.taxNumber"), render: (row) => row.tax_number },
            {
              key: "actions",
              header: "",
              align: "end",
              render: (row) => (
                <span className="row-actions">
                  <button type="button" onClick={() => setEditing(row)}>
                    {t("action.edit")}
                  </button>
                  <button type="button" className="danger" onClick={() => void remove("partner", row.id)}>
                    {t("action.delete")}
                  </button>
                </span>
              ),
            },
          ]}
        />
      </Card>

      {editing ? (
        <Card title={editing.id ? t("partners.edit") : t("partners.new")}>
          <div className="grid two">
            <Field label={t("partners.name")}>
              <input
                value={editing.name ?? ""}
                onChange={(e) => setEditing({ ...editing, name: e.target.value })}
              />
            </Field>
            <Field label={t("partners.kind")} hint={t("partners.kindHint")}>
              <Select
                value={editing.kind ?? "customer"}
                onChange={(value) => setEditing({ ...editing, kind: value })}
                options={[
                  { value: "customer" as const, label: t("partners.kind.customer") },
                  { value: "supplier" as const, label: t("partners.kind.supplier") },
                  { value: "both" as const, label: t("partners.kind.both") },
                ]}
              />
            </Field>
            <Field label={t("partners.phone")}>
              <input
                value={editing.phone ?? ""}
                onChange={(e) => setEditing({ ...editing, phone: e.target.value })}
              />
            </Field>
            <Field label={t("partners.email")}>
              <input
                value={editing.email ?? ""}
                onChange={(e) => setEditing({ ...editing, email: e.target.value })}
              />
            </Field>
            <Field label={t("partners.taxNumber")}>
              <input
                value={editing.tax_number ?? ""}
                onChange={(e) => setEditing({ ...editing, tax_number: e.target.value })}
              />
            </Field>
            <Field label={t("partners.address")}>
              <input
                value={editing.address ?? ""}
                onChange={(e) => setEditing({ ...editing, address: e.target.value })}
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

export const partnersModule: ModuleManifest = {
  key: "partners",
  name: "Partners",
  nameAr: "العملاء والموردون",
  version: "1.0.0",
  summary: "Customers and suppliers, and the control accounts their balances live in.",
  category: "Accounting",
  depends: ["base", "accounting"],
  sequence: 20,

  setup(ctx) {
    ctx.addEntity({ name: "partner", store: "partners", indexes: "id, name, kind" });
    ctx.addRoute({ path: "/partners", element: PartnersPage });
    ctx.addMenu({ path: "/partners", labelKey: "menu.partners", icon: "👥", section: "sales", sequence: 10 });
    ctx.addTranslations({
      en: {
        "menu.partners": "Customers & suppliers",
        "partners.title": "Customers & suppliers",
        "partners.subtitle": "One list — most real counterparties are both.",
        "partners.new": "New partner",
        "partners.edit": "Edit partner",
        "partners.search": "Search by name or phone",
        "partners.empty": "No customers or suppliers yet.",
        "partners.name": "Name",
        "partners.kind": "Type",
        "partners.kindHint": "Decides which control account their balance lands in.",
        "partners.kind.customer": "Customer",
        "partners.kind.supplier": "Supplier",
        "partners.kind.both": "Both",
        "partners.phone": "Phone",
        "partners.email": "Email",
        "partners.taxNumber": "Tax number",
        "partners.address": "Address",
      },
      ar: {
        "menu.partners": "العملاء والموردون",
        "partners.title": "العملاء والموردون",
        "partners.subtitle": "قائمة واحدة — أغلب الأطراف في الواقع عميل ومورّد في آنٍ واحد.",
        "partners.new": "طرف جديد",
        "partners.edit": "تعديل الطرف",
        "partners.search": "بحث بالاسم أو الهاتف",
        "partners.empty": "لا يوجد عملاء أو موردون بعد.",
        "partners.name": "الاسم",
        "partners.kind": "النوع",
        "partners.kindHint": "يحدد الحساب الوسيط الذي يقع فيه رصيده.",
        "partners.kind.customer": "عميل",
        "partners.kind.supplier": "مورّد",
        "partners.kind.both": "الاثنان",
        "partners.phone": "الهاتف",
        "partners.email": "البريد",
        "partners.taxNumber": "الرقم الضريبي",
        "partners.address": "العنوان",
      },
    });
  },
};
