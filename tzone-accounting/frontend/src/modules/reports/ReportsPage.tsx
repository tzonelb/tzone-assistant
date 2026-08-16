import { useMemo, useState } from "react";
import { useI18n } from "../../core/i18n";
import { useBaseCurrency } from "../../core/settings";
import { Card, DataTable, EmptyState, Field, Money, Page, Select, Toolbar } from "../../core/ui";
import { useAccounts, useJournalEntries } from "../accounting/hooks";
import { useDocuments } from "../documents/hooks";
import { documentTypes } from "../documents/service";
import { usePartnerMap } from "../partners";
import {
  aging,
  AGING_BUCKETS,
  balanceSheet,
  cashPosition,
  generalLedger,
  profitAndLoss,
  toReportLines,
  trialBalance,
  type ReportAccount,
} from "./calculators";

type Tab = "trial" | "pl" | "bs" | "ledger" | "aging" | "cash";
const TABS: Tab[] = ["trial", "pl", "bs", "ledger", "aging", "cash"];

function monthStart(): string {
  const now = new Date();
  return new Date(Date.UTC(now.getUTCFullYear(), now.getUTCMonth(), 1)).toISOString().slice(0, 10);
}
const today = () => new Date().toISOString().slice(0, 10);

export function ReportsPage() {
  const { t, pick } = useI18n();
  const currency = useBaseCurrency();
  const entries = useJournalEntries();
  const accounts = useAccounts();
  const documents = useDocuments();
  const partnerMap = usePartnerMap();

  const [tab, setTab] = useState<Tab>("trial");
  const [from, setFrom] = useState(monthStart);
  const [to, setTo] = useState(today);
  const [accountId, setAccountId] = useState("");
  const [agingKind, setAgingKind] = useState<"receivable" | "payable">("receivable");

  const lines = useMemo(() => toReportLines(entries), [entries]);
  const reportAccounts = accounts as unknown as ReportAccount[];

  const types = useMemo(() => [...documentTypes().values()], []);
  const chargeTypes = types
    .filter((type) => type.settles === agingKind && (type.role ?? "charge") === "charge")
    .map((type) => type.key);
  const settlementTypes = types
    .filter((type) => type.settles === agingKind && type.role === "settlement")
    .map((type) => type.key);

  return (
    <Page title={t("reports.title")} subtitle={t("reports.subtitle")}>
      <Card>
        <Toolbar>
          <div className="tabs">
            {TABS.map((candidate) => (
              <button
                key={candidate}
                type="button"
                className={candidate === tab ? "tab active" : "tab"}
                onClick={() => setTab(candidate)}
              >
                {t(`reports.tab.${candidate}`)}
              </button>
            ))}
          </div>
        </Toolbar>

        <div className="grid three">
          {tab !== "bs" && tab !== "aging" ? (
            <Field label={t("reports.from")}>
              <input type="date" value={from} onChange={(e) => setFrom(e.target.value)} />
            </Field>
          ) : null}
          <Field label={tab === "bs" || tab === "aging" ? t("reports.asOf") : t("reports.to")}>
            <input type="date" value={to} onChange={(e) => setTo(e.target.value)} />
          </Field>
          {tab === "ledger" ? (
            <Field label={t("reports.account")}>
              <Select
                value={accountId}
                onChange={setAccountId}
                placeholder={t("journal.pickAccount")}
                options={accounts
                  .filter((account) => !account.is_group)
                  .map((account) => ({
                    value: account.id,
                    label: `${account.code} — ${pick(account)}`,
                  }))}
              />
            </Field>
          ) : null}
          {tab === "aging" ? (
            <Field label={t("reports.agingKind")}>
              <Select
                value={agingKind}
                onChange={(value) => setAgingKind(value as "receivable" | "payable")}
                options={[
                  { value: "receivable", label: t("reports.receivable") },
                  { value: "payable", label: t("reports.payable") },
                ]}
              />
            </Field>
          ) : null}
        </div>
      </Card>

      {tab === "trial" ? (
        (() => {
          const report = trialBalance(lines, reportAccounts, from, to);
          return (
            <Card title={t("reports.tab.trial")}>
              <DataTable
                rows={report.rows}
                empty={t("reports.empty")}
                columns={[
                  { key: "code", header: t("accounts.code"), render: (row) => <code>{row.account.code}</code> },
                  { key: "name", header: t("accounts.name"), render: (row) => pick(row.account) },
                  {
                    key: "debit",
                    header: t("journal.debit"),
                    align: "end",
                    render: (row) => <Money value={row.debit} currency={currency} />,
                  },
                  {
                    key: "credit",
                    header: t("journal.credit"),
                    align: "end",
                    render: (row) => <Money value={row.credit} currency={currency} />,
                  },
                ]}
              />
              <div className="totals">
                <div className="grand">
                  <span>{t("journal.debit")}</span>
                  <Money value={report.totalDebit} currency={currency} />
                </div>
                <div className="grand">
                  <span>{t("journal.credit")}</span>
                  <Money value={report.totalCredit} currency={currency} />
                </div>
              </div>
              {!report.balanced ? <p className="error">{t("reports.notBalanced")}</p> : null}
            </Card>
          );
        })()
      ) : null}

      {tab === "pl" ? (
        (() => {
          const report = profitAndLoss(lines, reportAccounts, from, to);
          return (
            <div className="grid two">
              <Card title={t("reports.income")}>
                <DataTable
                  rows={report.income}
                  empty={t("reports.empty")}
                  columns={[
                    { key: "name", header: t("accounts.name"), render: (row) => pick(row.account) },
                    {
                      key: "amount",
                      header: t("reports.amount"),
                      align: "end",
                      render: (row) => <Money value={row.amount} currency={currency} />,
                    },
                  ]}
                />
                <div className="totals">
                  <div className="grand">
                    <span>{t("reports.totalIncome")}</span>
                    <Money value={report.totalIncome} currency={currency} />
                  </div>
                </div>
              </Card>
              <Card title={t("reports.expenses")}>
                <DataTable
                  rows={report.expenses}
                  empty={t("reports.empty")}
                  columns={[
                    { key: "name", header: t("accounts.name"), render: (row) => pick(row.account) },
                    {
                      key: "amount",
                      header: t("reports.amount"),
                      align: "end",
                      render: (row) => <Money value={row.amount} currency={currency} />,
                    },
                  ]}
                />
                <div className="totals">
                  <div className="grand">
                    <span>{t("reports.totalExpense")}</span>
                    <Money value={report.totalExpense} currency={currency} />
                  </div>
                  <div className="grand highlight">
                    <span>{t("reports.netProfit")}</span>
                    <Money value={report.netProfit} currency={currency} />
                  </div>
                </div>
              </Card>
            </div>
          );
        })()
      ) : null}

      {tab === "bs" ? (
        (() => {
          const report = balanceSheet(lines, reportAccounts, to);
          const section = (title: string, rows: typeof report.assets, total: number) => (
            <Card title={title}>
              <DataTable
                rows={rows}
                empty={t("reports.empty")}
                columns={[
                  { key: "name", header: t("accounts.name"), render: (row) => pick(row.account) },
                  {
                    key: "amount",
                    header: t("reports.amount"),
                    align: "end",
                    render: (row) => <Money value={row.amount} currency={currency} />,
                  },
                ]}
              />
              <div className="totals">
                <div className="grand">
                  <span>{t("reports.total")}</span>
                  <Money value={total} currency={currency} />
                </div>
              </div>
            </Card>
          );
          return (
            <>
              <div className="grid two">
                {section(t("reports.assets"), report.assets, report.totalAssets)}
                <div>
                  {section(t("reports.liabilities"), report.liabilities, report.totalLiabilities)}
                  <Card title={t("reports.equity")}>
                    <DataTable
                      rows={report.equity}
                      empty={t("reports.empty")}
                      columns={[
                        { key: "name", header: t("accounts.name"), render: (row) => pick(row.account) },
                        {
                          key: "amount",
                          header: t("reports.amount"),
                          align: "end",
                          render: (row) => <Money value={row.amount} currency={currency} />,
                        },
                      ]}
                    />
                    <div className="totals">
                      <div>
                        <span>{t("reports.retained")}</span>
                        <Money value={report.retainedEarnings} currency={currency} />
                      </div>
                      <div className="grand">
                        <span>{t("reports.total")}</span>
                        <Money value={report.totalEquity} currency={currency} />
                      </div>
                    </div>
                  </Card>
                </div>
              </div>
              {!report.balanced ? <p className="error">{t("reports.notBalanced")}</p> : null}
            </>
          );
        })()
      ) : null}

      {tab === "ledger" ? (
        (() => {
          if (!accountId) return <EmptyState message={t("reports.pickAccount")} />;
          const report = generalLedger(lines, reportAccounts, accountId, from, to);
          return (
            <Card title={pick(report.account)}>
              <div className="totals">
                <div>
                  <span>{t("reports.opening")}</span>
                  <Money value={report.opening} currency={currency} />
                </div>
              </div>
              <DataTable
                rows={report.rows}
                empty={t("reports.empty")}
                columns={[
                  { key: "date", header: t("journal.date"), render: (row) => row.date },
                  { key: "no", header: t("journal.number"), render: (row) => <code>{row.entryNo}</code> },
                  { key: "memo", header: t("journal.memo"), render: (row) => row.memo },
                  {
                    key: "debit",
                    header: t("journal.debit"),
                    align: "end",
                    render: (row) => (row.debit ? <Money value={row.debit} currency={currency} /> : ""),
                  },
                  {
                    key: "credit",
                    header: t("journal.credit"),
                    align: "end",
                    render: (row) => (row.credit ? <Money value={row.credit} currency={currency} /> : ""),
                  },
                  {
                    key: "balance",
                    header: t("reports.balance"),
                    align: "end",
                    render: (row) => <Money value={row.balance} currency={currency} />,
                  },
                ]}
              />
              <div className="totals">
                <div className="grand">
                  <span>{t("reports.closing")}</span>
                  <Money value={report.closing} currency={currency} />
                </div>
              </div>
            </Card>
          );
        })()
      ) : null}

      {tab === "aging" ? (
        (() => {
          const report = aging(documents as never, chargeTypes, settlementTypes, to);
          return (
            <Card title={t(`reports.${agingKind}`)}>
              <div className="bucket-row">
                {AGING_BUCKETS.map((bucket) => (
                  <div key={bucket} className="bucket">
                    <span>{t(`reports.bucket.${bucket}`)}</span>
                    <Money value={report.buckets[bucket]} currency={currency} />
                  </div>
                ))}
              </div>
              <DataTable
                rows={report.items}
                empty={t("reports.nothingOutstanding")}
                columns={[
                  { key: "no", header: t("documents.number"), render: (row) => <code>{row.docNo}</code> },
                  {
                    key: "partner",
                    header: t("documents.partner"),
                    render: (row) => partnerMap.get(row.partnerId ?? "")?.name ?? "—",
                  },
                  { key: "due", header: t("documents.dueDate"), render: (row) => row.dueDate },
                  { key: "late", header: t("reports.daysLate"), align: "end", render: (row) => row.daysLate },
                  {
                    key: "outstanding",
                    header: t("documents.outstanding"),
                    align: "end",
                    render: (row) => <Money value={row.outstanding} currency={currency} />,
                  },
                ]}
              />
              <div className="totals">
                <div className="grand">
                  <span>{t("reports.total")}</span>
                  <Money value={report.total} currency={currency} />
                </div>
              </div>
            </Card>
          );
        })()
      ) : null}

      {tab === "cash" ? (
        (() => {
          const report = cashPosition(lines, reportAccounts, from, to);
          return (
            <Card title={t("reports.tab.cash")}>
              <DataTable
                rows={report.rows}
                empty={t("reports.empty")}
                columns={[
                  { key: "name", header: t("accounts.name"), render: (row) => pick(row.account) },
                  {
                    key: "in",
                    header: t("reports.inflow"),
                    align: "end",
                    render: (row) => <Money value={row.inflow} currency={currency} />,
                  },
                  {
                    key: "out",
                    header: t("reports.outflow"),
                    align: "end",
                    render: (row) => <Money value={row.outflow} currency={currency} />,
                  },
                  {
                    key: "balance",
                    header: t("reports.balance"),
                    align: "end",
                    render: (row) => <Money value={row.balance} currency={currency} />,
                  },
                ]}
              />
              <div className="totals">
                <div className="grand">
                  <span>{t("reports.total")}</span>
                  <Money value={report.total} currency={currency} />
                </div>
              </div>
            </Card>
          );
        })()
      ) : null}
    </Page>
  );
}
