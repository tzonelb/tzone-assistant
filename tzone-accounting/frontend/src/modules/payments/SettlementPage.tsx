import { useMemo, useState } from "react";
import { useI18n } from "../../core/i18n";
import { allocate, formatAmount, parseAmount, RATE_ONE } from "../../core/money";
import { useBaseCurrency, useSettings } from "../../core/settings";
import { Card, DataTable, Field, Money, Page, Select, StatusBadge, Toolbar } from "../../core/ui";
import { useAccounts } from "../accounting/hooks";
import { useDocuments, useOpenDocuments } from "../documents/hooks";
import { postDocument, voidDocument } from "../documents/service";
import type { Allocation } from "../documents/types";
import { usePartnerMap, usePartners } from "../partners";

const today = () => new Date().toISOString().slice(0, 10);

/** One screen for both directions: receipts settle receivables, payments settle payables. */
export function SettlementPage({ docType }: { docType: "receipt" | "payment" }) {
  const { t, pick } = useI18n();
  const currency = useBaseCurrency();
  const settings = useSettings();
  const documents = useDocuments([docType]);
  const partners = usePartners();
  const partnerMap = usePartnerMap();
  const cashAccounts = useAccounts().filter((account) => account.is_cash);

  const isReceipt = docType === "receipt";
  const openCharges = useOpenDocuments([isReceipt ? "sales_invoice" : "purchase_invoice"]);

  const [composing, setComposing] = useState(false);
  const [date, setDate] = useState(today);
  const [partnerId, setPartnerId] = useState("");
  const [amountText, setAmountText] = useState("");
  const [cashAccountId, setCashAccountId] = useState("");
  const [memo, setMemo] = useState("");
  const [picked, setPicked] = useState<Record<string, boolean>>({});
  const [error, setError] = useState("");

  const amount = parseAmount(amountText, currency.decimals);
  const partnerCharges = useMemo(
    () => openCharges.filter((document) => document.partner_id === partnerId),
    [openCharges, partnerId],
  );
  const selected = partnerCharges.filter((document) => picked[document.id]);

  /**
   * Spread the amount over the chosen invoices, oldest first, so the allocated parts always sum
   * back to exactly the amount received — no rounding remainder left stranded.
   */
  const allocations: Allocation[] = useMemo(() => {
    if (!selected.length || amount <= 0) return [];
    const ordered = [...selected].sort((a, b) => a.date.localeCompare(b.date));
    const shares = allocate(
      Math.min(amount, ordered.reduce((sum, document) => sum + document.base_total, 0)),
      ordered.map((document) => document.base_total),
    );
    return ordered.map((document, index) => ({
      document_id: document.id,
      amount: shares[index],
      base_amount: shares[index],
    }));
  }, [selected, amount]);

  function reset() {
    setComposing(false);
    setPartnerId("");
    setAmountText("");
    setMemo("");
    setPicked({});
    setDate(today());
    setError("");
  }

  async function submit() {
    setError("");
    try {
      await postDocument({
        doc_type: docType,
        date,
        partner_id: partnerId || null,
        currency: currency.code,
        fx_rate: RATE_ONE,
        memo,
        amount,
        allocations,
        cash_account_id: cashAccountId || settings.account_roles?.cash || null,
      });
      reset();
    } catch (caught) {
      setError(String((caught as Error).message));
    }
  }

  return (
    <Page
      title={t(`payments.${docType}.title`)}
      subtitle={t(`payments.${docType}.subtitle`)}
      actions={
        !composing ? (
          <button type="button" className="primary" onClick={() => setComposing(true)}>
            {t(`payments.${docType}.new`)}
          </button>
        ) : null
      }
    >
      {composing ? (
        <Card title={t(`payments.${docType}.new`)}>
          <div className="grid three">
            <Field label={t("documents.partner")}>
              <Select
                value={partnerId}
                onChange={(value) => {
                  setPartnerId(value);
                  setPicked({});
                }}
                placeholder={t("invoicing.pickPartner")}
                options={partners
                  .filter((partner) =>
                    isReceipt ? partner.kind !== "supplier" : partner.kind !== "customer",
                  )
                  .map((partner) => ({ value: partner.id, label: partner.name }))}
              />
            </Field>
            <Field label={t("documents.date")}>
              <input type="date" value={date} onChange={(e) => setDate(e.target.value)} />
            </Field>
            <Field label={t("payments.amount")}>
              <input
                className="amount"
                inputMode="decimal"
                value={amountText}
                onChange={(e) => setAmountText(e.target.value)}
              />
            </Field>
            <Field label={t("payments.cashAccount")}>
              <Select
                value={cashAccountId}
                onChange={setCashAccountId}
                placeholder={t("payments.defaultCash")}
                options={cashAccounts.map((account) => ({
                  value: account.id,
                  label: `${account.code} — ${pick(account)}`,
                }))}
              />
            </Field>
            <Field label={t("invoicing.memo")}>
              <input value={memo} onChange={(e) => setMemo(e.target.value)} />
            </Field>
          </div>

          {partnerId ? (
            <>
              <h3 className="section-title">{t("payments.openDocuments")}</h3>
              <DataTable
                rows={partnerCharges}
                empty={t("payments.nothingOpen")}
                columns={[
                  {
                    key: "pick",
                    header: "",
                    render: (row) => (
                      <input
                        type="checkbox"
                        checked={Boolean(picked[row.id])}
                        onChange={(e) => setPicked({ ...picked, [row.id]: e.target.checked })}
                      />
                    ),
                  },
                  {
                    key: "no",
                    header: t("documents.number"),
                    render: (row) => <code>{row.legal_no ?? row.doc_no}</code>,
                  },
                  { key: "date", header: t("documents.date"), render: (row) => row.date },
                  {
                    key: "total",
                    header: t("documents.total"),
                    align: "end",
                    render: (row) => <Money value={row.base_total} currency={currency} />,
                  },
                  {
                    key: "allocated",
                    header: t("payments.allocated"),
                    align: "end",
                    render: (row) => {
                      const allocation = allocations.find((entry) => entry.document_id === row.id);
                      return allocation ? (
                        <Money value={allocation.base_amount} currency={currency} />
                      ) : (
                        <span className="muted">—</span>
                      );
                    },
                  },
                ]}
              />
              <p className="muted">{t("payments.allocationHint")}</p>
            </>
          ) : null}

          {error ? <p className="error">{error}</p> : null}

          <Toolbar>
            <button
              type="button"
              className="primary"
              disabled={!partnerId || amount <= 0}
              onClick={() => void submit()}
            >
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
          empty={t("payments.empty")}
          columns={[
            {
              key: "no",
              header: t("documents.number"),
              render: (row) => <code>{row.legal_no ?? row.doc_no}</code>,
            },
            { key: "date", header: t("documents.date"), render: (row) => row.date },
            {
              key: "partner",
              header: t("documents.partner"),
              render: (row) => partnerMap.get(row.partner_id ?? "")?.name ?? "—",
            },
            {
              key: "amount",
              header: t("payments.amount"),
              align: "end",
              render: (row) => <Money value={row.base_total} currency={currency} />,
            },
            {
              key: "allocations",
              header: t("payments.allocated"),
              align: "end",
              render: (row) => (row.payload.allocations ?? []).length,
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

export function formatForDisplay(amount: number, decimals: number): string {
  return formatAmount(amount, decimals);
}
