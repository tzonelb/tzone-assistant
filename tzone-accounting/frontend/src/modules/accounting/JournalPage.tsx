import { useState } from "react";
import { useI18n } from "../../core/i18n";
import { formatAmount, parseAmount, RATE_ONE } from "../../core/money";
import { useBaseCurrency } from "../../core/settings";
import { Card, DataTable, Field, Money, Page, Select, StatusBadge, Toolbar } from "../../core/ui";
import { usePostableAccounts, useJournalEntries, useAccountMap } from "./hooks";
import { postEntry, voidEntry } from "./service";
import { totals } from "./posting";
import type { JournalEntry, JournalLine } from "./types";

function emptyLine(): JournalLine {
  return {
    account_id: "",
    partner_id: null,
    description: "",
    debit: 0,
    credit: 0,
    base_debit: 0,
    base_credit: 0,
  };
}

const today = () => new Date().toISOString().slice(0, 10);

export function JournalPage() {
  const { t, pick } = useI18n();
  const entries = useJournalEntries();
  const accounts = usePostableAccounts();
  const accountMap = useAccountMap();
  const currency = useBaseCurrency();

  const [composing, setComposing] = useState(false);
  const [date, setDate] = useState(today);
  const [memo, setMemo] = useState("");
  const [lines, setLines] = useState<JournalLine[]>([emptyLine(), emptyLine()]);
  const [error, setError] = useState("");
  const [expanded, setExpanded] = useState<string | null>(null);

  const sum = totals(lines);
  const balanced = sum.debit === sum.credit && sum.debit > 0;

  function updateLine(index: number, patch: Partial<JournalLine>) {
    setLines((current) =>
      current.map((line, position) => (position === index ? { ...line, ...patch } : line)),
    );
  }

  function setSide(index: number, side: "debit" | "credit", raw: string) {
    const value = parseAmount(raw, currency.decimals);
    // A line is one-sided by construction: typing into one column clears the other.
    updateLine(index, {
      debit: side === "debit" ? value : 0,
      credit: side === "credit" ? value : 0,
      base_debit: side === "debit" ? value : 0,
      base_credit: side === "credit" ? value : 0,
    });
  }

  function reset() {
    setComposing(false);
    setLines([emptyLine(), emptyLine()]);
    setMemo("");
    setDate(today());
    setError("");
  }

  async function submit(status: "draft" | "posted") {
    setError("");
    try {
      await postEntry(
        {
          date,
          memo,
          currency: currency.code,
          fx_rate: RATE_ONE,
          source_kind: "manual",
          source_id: null,
          lines: lines.filter((line) => line.account_id && (line.debit || line.credit)),
        },
        { status },
      );
      reset();
    } catch (caught) {
      setError(String((caught as Error).message));
    }
  }

  return (
    <Page
      title={t("journal.title")}
      subtitle={t("journal.subtitle")}
      actions={
        !composing ? (
          <button type="button" className="primary" onClick={() => setComposing(true)}>
            {t("journal.new")}
          </button>
        ) : null
      }
    >
      {composing ? (
        <Card title={t("journal.new")}>
          <div className="grid two">
            <Field label={t("journal.date")}>
              <input type="date" value={date} onChange={(e) => setDate(e.target.value)} />
            </Field>
            <Field label={t("journal.memo")}>
              <input value={memo} onChange={(e) => setMemo(e.target.value)} />
            </Field>
          </div>

          <div className="table-scroll">
            <table className="data-table compact">
              <thead>
                <tr>
                  <th>{t("journal.account")}</th>
                  <th>{t("journal.description")}</th>
                  <th className="align-end">{t("journal.debit")}</th>
                  <th className="align-end">{t("journal.credit")}</th>
                  <th />
                </tr>
              </thead>
              <tbody>
                {lines.map((line, index) => (
                  <tr key={index}>
                    <td>
                      <Select
                        value={line.account_id}
                        onChange={(value) => updateLine(index, { account_id: value })}
                        placeholder={t("journal.pickAccount")}
                        options={accounts.map((account) => ({
                          value: account.id,
                          label: `${account.code} — ${pick(account)}`,
                        }))}
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
                        className="amount"
                        inputMode="decimal"
                        value={line.debit ? formatAmount(line.debit, currency.decimals) : ""}
                        onChange={(e) => setSide(index, "debit", e.target.value)}
                      />
                    </td>
                    <td className="align-end">
                      <input
                        className="amount"
                        inputMode="decimal"
                        value={line.credit ? formatAmount(line.credit, currency.decimals) : ""}
                        onChange={(e) => setSide(index, "credit", e.target.value)}
                      />
                    </td>
                    <td className="align-end">
                      <button
                        type="button"
                        onClick={() => setLines(lines.filter((_, i) => i !== index))}
                        disabled={lines.length <= 2}
                      >
                        ✕
                      </button>
                    </td>
                  </tr>
                ))}
              </tbody>
              <tfoot>
                <tr>
                  <td colSpan={2}>
                    <button type="button" onClick={() => setLines([...lines, emptyLine()])}>
                      + {t("journal.addLine")}
                    </button>
                  </td>
                  <td className="align-end">
                    <Money value={sum.debit} currency={currency} />
                  </td>
                  <td className="align-end">
                    <Money value={sum.credit} currency={currency} />
                  </td>
                  <td />
                </tr>
              </tfoot>
            </table>
          </div>

          {!balanced ? (
            <p className="warn-text">
              {t("journal.difference", {
                amount: formatAmount(Math.abs(sum.debit - sum.credit), currency.decimals),
              })}
            </p>
          ) : null}
          {error ? <p className="error">{error}</p> : null}

          <Toolbar>
            <button
              type="button"
              className="primary"
              disabled={!balanced}
              onClick={() => void submit("posted")}
            >
              {t("action.post")}
            </button>
            <button type="button" onClick={() => void submit("draft")}>
              {t("journal.saveDraft")}
            </button>
            <button type="button" onClick={reset}>
              {t("action.cancel")}
            </button>
          </Toolbar>
        </Card>
      ) : null}

      <Card>
        <DataTable
          rows={entries}
          empty={t("journal.empty")}
          onRowClick={(entry) => setExpanded(expanded === entry.id ? null : entry.id)}
          columns={[
            { key: "no", header: t("journal.number"), render: (row) => <code>{row.entry_no}</code> },
            { key: "date", header: t("journal.date"), render: (row) => row.date },
            {
              key: "memo",
              header: t("journal.memo"),
              render: (row) => (
                <div>
                  {row.memo}
                  <div className="muted small">{t(`journal.source.${row.source_kind}`)}</div>
                </div>
              ),
            },
            {
              key: "amount",
              header: t("journal.amount"),
              align: "end",
              render: (row) => <Money value={totals(row.lines ?? []).baseDebit} currency={currency} />,
            },
            { key: "status", header: "", render: (row) => <StatusBadge status={row.status} /> },
            {
              key: "actions",
              header: "",
              align: "end",
              render: (row: JournalEntry) =>
                row.status === "posted" ? (
                  <button
                    type="button"
                    className="danger"
                    onClick={(event) => {
                      event.stopPropagation();
                      void voidEntry(row.id, today());
                    }}
                  >
                    {t("action.void")}
                  </button>
                ) : null,
            },
          ]}
        />

        {expanded ? (
          <div className="expanded-lines">
            <table className="data-table compact">
              <thead>
                <tr>
                  <th>{t("journal.account")}</th>
                  <th>{t("journal.description")}</th>
                  <th className="align-end">{t("journal.debit")}</th>
                  <th className="align-end">{t("journal.credit")}</th>
                </tr>
              </thead>
              <tbody>
                {(entries.find((entry) => entry.id === expanded)?.lines ?? []).map((line, index) => (
                  <tr key={index}>
                    <td>{pick(accountMap.get(line.account_id)) || line.account_id}</td>
                    <td>{line.description}</td>
                    <td className="align-end">
                      {line.base_debit ? <Money value={line.base_debit} currency={currency} /> : ""}
                    </td>
                    <td className="align-end">
                      {line.base_credit ? <Money value={line.base_credit} currency={currency} /> : ""}
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        ) : null}
      </Card>
    </Page>
  );
}
