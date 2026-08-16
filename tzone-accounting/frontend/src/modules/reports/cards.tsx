/**
 * Dashboard cards contributed by the reports module.
 *
 * They are registered through `ctx.addDashboardCard`, so the dashboard module never learns that
 * accounting exists — and a future module can add its own card the same way.
 */

import { useMemo } from "react";
import { useI18n } from "../../core/i18n";
import { useBaseCurrency } from "../../core/settings";
import { Money } from "../../core/ui";
import { useJournalEntries, useAccounts } from "../accounting/hooks";
import { useDocuments } from "../documents/hooks";
import { documentTypes } from "../documents/service";
import { aging, cashPosition, profitAndLoss, toReportLines, type ReportAccount } from "./calculators";

const today = () => new Date().toISOString().slice(0, 10);

function monthStart(): string {
  const now = new Date();
  return new Date(Date.UTC(now.getUTCFullYear(), now.getUTCMonth(), 1)).toISOString().slice(0, 10);
}

function Stat({ label, children, hint }: { label: string; children: React.ReactNode; hint?: string }) {
  return (
    <div className="stat-card">
      <span className="stat-label">{label}</span>
      <span className="stat-value">{children}</span>
      {hint ? <span className="stat-hint">{hint}</span> : null}
    </div>
  );
}

export function ProfitCard() {
  const { t } = useI18n();
  const currency = useBaseCurrency();
  const entries = useJournalEntries();
  const accounts = useAccounts() as unknown as ReportAccount[];
  const report = useMemo(
    () => profitAndLoss(toReportLines(entries), accounts, monthStart(), today()),
    [entries, accounts],
  );

  return (
    <Stat label={t("dashboard.monthProfit")} hint={t("dashboard.thisMonth")}>
      <Money value={report.netProfit} currency={currency} />
    </Stat>
  );
}

export function CashCard() {
  const { t } = useI18n();
  const currency = useBaseCurrency();
  const entries = useJournalEntries();
  const accounts = useAccounts() as unknown as ReportAccount[];
  const report = useMemo(
    () => cashPosition(toReportLines(entries), accounts, monthStart(), today()),
    [entries, accounts],
  );

  return (
    <Stat label={t("dashboard.cash")} hint={t("dashboard.allCashAccounts")}>
      <Money value={report.total} currency={currency} />
    </Stat>
  );
}

function AgingCard({ side }: { side: "receivable" | "payable" }) {
  const { t } = useI18n();
  const currency = useBaseCurrency();
  const documents = useDocuments();
  const types = useMemo(() => [...documentTypes().values()], []);

  const report = useMemo(
    () =>
      aging(
        documents as never,
        types.filter((type) => type.settles === side && (type.role ?? "charge") === "charge").map((t) => t.key),
        types.filter((type) => type.settles === side && type.role === "settlement").map((t) => t.key),
        today(),
      ),
    [documents, types, side],
  );

  const overdue = report.items
    .filter((item) => item.daysLate > 0)
    .reduce((sum, item) => sum + item.outstanding, 0);

  return (
    <Stat
      label={t(side === "receivable" ? "dashboard.receivable" : "dashboard.payable")}
      hint={t("dashboard.overdue", { amount: overdue ? "•" : "" })}
    >
      <Money value={report.total} currency={currency} />
    </Stat>
  );
}

export const ReceivableCard = () => <AgingCard side="receivable" />;
export const PayableCard = () => <AgingCard side="payable" />;
