import { useMemo, useState } from "react";
import { useI18n } from "../../core/i18n";
import { save } from "../../core/repository";
import { Card, Field, Page, Select, Toolbar } from "../../core/ui";
import { useAccounts } from "./hooks";
import type { Account } from "./types";
import type { AccountType } from "../../core/types";

const TYPES: AccountType[] = ["asset", "liability", "equity", "income", "expense"];

interface TreeNode {
  account: Account;
  depth: number;
}

function buildTree(accounts: Account[]): TreeNode[] {
  const children = new Map<string, Account[]>();
  for (const account of accounts) {
    const key = account.parent_id ?? "";
    children.set(key, [...(children.get(key) ?? []), account]);
  }
  const out: TreeNode[] = [];
  const walk = (parent: string, depth: number) => {
    for (const account of (children.get(parent) ?? []).sort((a, b) => a.code.localeCompare(b.code))) {
      out.push({ account, depth });
      walk(account.id, depth + 1);
    }
  };
  walk("", 0);
  return out;
}

export function ChartOfAccountsPage() {
  const { t, pick } = useI18n();
  const accounts = useAccounts();
  const [filter, setFilter] = useState("");
  const [editing, setEditing] = useState<Partial<Account> | null>(null);

  const tree = useMemo(() => buildTree(accounts), [accounts]);
  const visible = filter
    ? tree.filter(
        ({ account }) =>
          account.code.includes(filter) ||
          account.name_en.toLowerCase().includes(filter.toLowerCase()) ||
          account.name_ar.includes(filter),
      )
    : tree;

  async function submit() {
    if (!editing?.code || !editing.name_en) return;
    await save<Partial<Account>>("account", {
      is_group: false,
      is_cash: false,
      is_active: true,
      parent_id: null,
      currency: null,
      name_ar: editing.name_en,
      ...editing,
    });
    setEditing(null);
  }

  return (
    <Page
      title={t("accounts.title")}
      subtitle={t("accounts.subtitle")}
      actions={
        <button type="button" className="primary" onClick={() => setEditing({ type: "asset" })}>
          {t("accounts.new")}
        </button>
      }
    >
      <Card>
        <Toolbar>
          <input
            placeholder={t("accounts.search")}
            value={filter}
            onChange={(e) => setFilter(e.target.value)}
          />
        </Toolbar>

        <div className="table-scroll">
          <table className="data-table">
            <thead>
              <tr>
                <th>{t("accounts.code")}</th>
                <th>{t("accounts.name")}</th>
                <th>{t("accounts.type")}</th>
                <th>{t("accounts.postable")}</th>
                <th />
              </tr>
            </thead>
            <tbody>
              {visible.map(({ account, depth }) => (
                <tr key={account.id} className={account.is_group ? "group-row" : undefined}>
                  <td>
                    <code>{account.code}</code>
                  </td>
                  <td style={{ paddingInlineStart: `${depth * 18 + 12}px` }}>
                    {pick(account)}
                    {account.is_cash ? <span className="chip">{t("accounts.cash")}</span> : null}
                  </td>
                  <td>{t(`accounts.type.${account.type}`)}</td>
                  <td>{account.is_group ? t("accounts.groupOnly") : t("accounts.yes")}</td>
                  <td className="align-end">
                    <button type="button" onClick={() => setEditing(account)}>
                      {t("action.edit")}
                    </button>
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      </Card>

      {editing ? (
        <Card title={editing.id ? t("accounts.edit") : t("accounts.new")}>
          <div className="grid two">
            <Field label={t("accounts.code")}>
              <input
                value={editing.code ?? ""}
                onChange={(e) => setEditing({ ...editing, code: e.target.value })}
              />
            </Field>
            <Field label={t("accounts.type")}>
              <Select
                value={(editing.type ?? "asset") as AccountType}
                onChange={(value) => setEditing({ ...editing, type: value })}
                options={TYPES.map((type) => ({ value: type, label: t(`accounts.type.${type}`) }))}
              />
            </Field>
            <Field label={t("accounts.nameEn")}>
              <input
                value={editing.name_en ?? ""}
                onChange={(e) => setEditing({ ...editing, name_en: e.target.value })}
              />
            </Field>
            <Field label={t("accounts.nameAr")}>
              <input
                value={editing.name_ar ?? ""}
                onChange={(e) => setEditing({ ...editing, name_ar: e.target.value })}
              />
            </Field>
            <Field label={t("accounts.parent")}>
              <Select
                value={editing.parent_id ?? ""}
                onChange={(value) => setEditing({ ...editing, parent_id: value || null })}
                placeholder={t("accounts.noParent")}
                options={accounts
                  .filter((account) => account.is_group)
                  .map((account) => ({ value: account.id, label: `${account.code} — ${pick(account)}` }))}
              />
            </Field>
            <Field label={t("accounts.cash")} hint={t("accounts.cashHint")}>
              <input
                type="checkbox"
                checked={Boolean(editing.is_cash)}
                onChange={(e) => setEditing({ ...editing, is_cash: e.target.checked })}
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
