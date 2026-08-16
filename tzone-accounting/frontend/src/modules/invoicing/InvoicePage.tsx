import { useState } from "react";
import { useI18n } from "../../core/i18n";
import { formatAmount, parseAmount, RATE_ONE } from "../../core/money";
import { useBaseCurrency } from "../../core/settings";
import {
  Card,
  DataTable,
  Field,
  Money,
  Page,
  Select,
  StatusBadge,
  Toolbar,
} from "../../core/ui";
import { useItems } from "../catalog";
import { usePartnerMap, usePartners } from "../partners";
import { useDocuments } from "../documents/hooks";
import { documentTotals, postDocument, voidDocument } from "../documents/service";
import type { DocumentLine } from "../documents/types";

const today = () => new Date().toISOString().slice(0, 10);

function emptyLine(): DocumentLine {
  return { item_id: null, description: "", quantity: 1, unit_price: 0, tax_rate_bp: 0 };
}

/** One screen serves both invoice directions — they differ only in wording and partner side. */
export function InvoicePage({ docType }: { docType: "sales_invoice" | "purchase_invoice" }) {
  const { t, pick } = useI18n();
  const currency = useBaseCurrency();
  const documents = useDocuments([docType]);
  const partners = usePartners();
  const partnerMap = usePartnerMap();
  const items = useItems();

  const [composing, setComposing] = useState(false);
  const [date, setDate] = useState(today);
  const [dueDate, setDueDate] = useState(today);
  const [partnerId, setPartnerId] = useState("");
  const [memo, setMemo] = useState("");
  const [lines, setLines] = useState<DocumentLine[]>([emptyLine()]);
  const [error, setError] = useState("");

  const sums = documentTotals(lines.filter((line) => line.quantity && line.unit_price));
  const isSales = docType === "sales_invoice";

  function updateLine(index: number, patch: Partial<DocumentLine>) {
    setLines((current) =>
      current.map((line, position) => (position === index ? { ...line, ...patch } : line)),
    );
  }

  function pickItem(index: number, itemId: string) {
    const item = items.find((candidate) => candidate.id === itemId);
    if (!item) return updateLine(index, { item_id: null });
    updateLine(index, {
      item_id: item.id,
      description: pick(item),
      unit_price: isSales ? item.sale_price : item.purchase_price,
      tax_rate_bp: item.tax_rate_bp,
    });
  }

  function reset() {
    setComposing(false);
    setLines([emptyLine()]);
    setPartnerId("");
    setMemo("");
    setDate(today());
    setDueDate(today());
    setError("");
  }

  async function submit() {
    setError("");
    try {
      await postDocument({
        doc_type: docType,
        date,
        due_date: dueDate,
        partner_id: partnerId || null,
        currency: currency.code,
        fx_rate: RATE_ONE,
        memo,
        lines: lines.filter((line) => line.quantity && line.unit_price),
      });
      reset();
    } catch (caught) {
      setError(String((caught as Error).message));
    }
  }

  const canPost = Boolean(partnerId) && sums.total > 0;

  return (
    <Page
      title={t(`invoicing.${docType}.title`)}
      subtitle={t(`invoicing.${docType}.subtitle`)}
      actions={
        !composing ? (
          <button type="button" className="primary" onClick={() => setComposing(true)}>
            {t(`invoicing.${docType}.new`)}
          </button>
        ) : null
      }
    >
      {composing ? (
        <Card title={t(`invoicing.${docType}.new`)}>
          <div className="grid three">
            <Field label={t("documents.partner")}>
              <Select
                value={partnerId}
                onChange={setPartnerId}
                placeholder={t("invoicing.pickPartner")}
                options={partners
                  .filter((partner) =>
                    isSales
                      ? partner.kind !== "supplier"
                      : partner.kind !== "customer",
                  )
                  .map((partner) => ({ value: partner.id, label: partner.name }))}
              />
            </Field>
            <Field label={t("documents.date")}>
              <input type="date" value={date} onChange={(e) => setDate(e.target.value)} />
            </Field>
            <Field label={t("documents.dueDate")}>
              <input type="date" value={dueDate} onChange={(e) => setDueDate(e.target.value)} />
            </Field>
          </div>

          <div className="table-scroll">
            <table className="data-table compact">
              <thead>
                <tr>
                  <th>{t("invoicing.item")}</th>
                  <th>{t("invoicing.description")}</th>
                  <th className="align-end">{t("invoicing.quantity")}</th>
                  <th className="align-end">{t("invoicing.unitPrice")}</th>
                  <th className="align-end">{t("invoicing.taxRate")}</th>
                  <th className="align-end">{t("invoicing.lineTotal")}</th>
                  <th />
                </tr>
              </thead>
              <tbody>
                {lines.map((line, index) => (
                  <tr key={index}>
                    <td>
                      <Select
                        value={line.item_id ?? ""}
                        onChange={(value) => pickItem(index, value)}
                        placeholder={t("invoicing.freeText")}
                        options={items.map((item) => ({ value: item.id, label: pick(item) }))}
                      />
                    </td>
                    <td>
                      <input
                        value={line.description}
                        onChange={(e) => updateLine(index, { description: e.target.value })}
                      />
                    </td>
                    <td className="align-end">
                      <input
                        className="amount narrow"
                        inputMode="decimal"
                        value={line.quantity}
                        onChange={(e) =>
                          updateLine(index, { quantity: Number(e.target.value || 0) })
                        }
                      />
                    </td>
                    <td className="align-end">
                      <input
                        className="amount"
                        inputMode="decimal"
                        value={formatAmount(line.unit_price, currency.decimals)}
                        onChange={(e) =>
                          updateLine(index, {
                            unit_price: parseAmount(e.target.value, currency.decimals),
                          })
                        }
                      />
                    </td>
                    <td className="align-end">
                      <input
                        className="amount narrow"
                        inputMode="decimal"
                        value={(line.tax_rate_bp / 100).toString()}
                        onChange={(e) =>
                          updateLine(index, {
                            tax_rate_bp: Math.round(Number(e.target.value || 0) * 100),
                          })
                        }
                      />
                    </td>
                    <td className="align-end">
                      <Money value={line.quantity * line.unit_price} currency={currency} />
                    </td>
                    <td className="align-end">
                      <button
                        type="button"
                        onClick={() => setLines(lines.filter((_, i) => i !== index))}
                        disabled={lines.length <= 1}
                      >
                        ✕
                      </button>
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>

          <Toolbar>
            <button type="button" onClick={() => setLines([...lines, emptyLine()])}>
              + {t("invoicing.addLine")}
            </button>
          </Toolbar>

          <div className="totals">
            <div>
              <span>{t("invoicing.net")}</span>
              <Money value={sums.net} currency={currency} />
            </div>
            <div>
              <span>{t("invoicing.tax")}</span>
              <Money value={sums.tax} currency={currency} />
            </div>
            <div className="grand">
              <span>{t("documents.total")}</span>
              <Money value={sums.total} currency={currency} />
            </div>
          </div>

          <Field label={t("invoicing.memo")}>
            <input value={memo} onChange={(e) => setMemo(e.target.value)} />
          </Field>

          {error ? <p className="error">{error}</p> : null}

          <Toolbar>
            <button type="button" className="primary" disabled={!canPost} onClick={() => void submit()}>
              {t("action.post")}
            </button>
            <button type="button" onClick={reset}>
              {t("action.cancel")}
            </button>
          </Toolbar>
        </Card>
      ) : null}

      <Card>
        <DataTable
          rows={documents}
          empty={t("invoicing.empty")}
          columns={[
            {
              key: "no",
              header: t("documents.number"),
              render: (row) => (
                <div>
                  <code>{row.doc_no}</code>
                  <div className="muted small">
                    {row.legal_no ?? t("documents.notSyncedYet")}
                  </div>
                </div>
              ),
            },
            { key: "date", header: t("documents.date"), render: (row) => row.date },
            {
              key: "partner",
              header: t("documents.partner"),
              render: (row) => partnerMap.get(row.partner_id ?? "")?.name ?? "—",
            },
            {
              key: "total",
              header: t("documents.total"),
              align: "end",
              render: (row) => <Money value={row.base_total} currency={currency} />,
            },
            { key: "status", header: "", render: (row) => <StatusBadge status={row.status} /> },
            {
              key: "actions",
              header: "",
              align: "end",
              render: (row) =>
                row.status !== "void" ? (
                  <button
                    type="button"
                    className="danger"
                    onClick={() => void voidDocument(row.id, today())}
                  >
                    {t("action.void")}
                  </button>
                ) : null,
            },
          ]}
        />
      </Card>
    </Page>
  );
}
