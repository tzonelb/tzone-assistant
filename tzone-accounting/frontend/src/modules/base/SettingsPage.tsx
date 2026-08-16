import { useLiveQuery } from "dexie-react-hooks";
import { useI18n } from "../../core/i18n";
import { db } from "../../core/db";
import { getRegistry } from "../../core/registry";
import { updateSettings, useSettings } from "../../core/settings";
import { syncEngine } from "../../core/sync";
import { Card, DataTable, Field, Page, Select } from "../../core/ui";

export function SettingsPage() {
  const { t, locale, setLocale } = useI18n();
  const settings = useSettings();
  const panels = getRegistry().settingsPanels;
  const failed = useLiveQuery(() => db().outbox.where("status").equals("failed").toArray(), []) ?? [];

  return (
    <Page title={t("settings.title")} subtitle={t("settings.subtitle")}>
      <Card title={t("settings.company")}>
        <div className="grid two">
          <Field label={t("settings.companyName")}>
            <input
              value={settings.company_name ?? ""}
              onChange={(e) => void updateSettings({ company_name: e.target.value })}
            />
          </Field>
          <Field label={t("settings.baseCurrency")} hint={t("settings.baseCurrencyHint")}>
            <Select
              value={settings.base_currency ?? "USD"}
              onChange={(value) => void updateSettings({ base_currency: value })}
              options={(settings.currencies ?? []).map((currency) => ({
                value: currency.code,
                label: `${currency.code} — ${currency.symbol}`,
              }))}
            />
          </Field>
          <Field label={t("settings.lockDate")} hint={t("settings.lockDateHint")}>
            <input
              type="date"
              value={settings.lock_date ?? ""}
              onChange={(e) => void updateSettings({ lock_date: e.target.value || null })}
            />
          </Field>
          <Field label={t("settings.language")}>
            <Select
              value={locale}
              onChange={(value) => setLocale(value as "ar" | "en")}
              options={[
                { value: "ar", label: "العربية" },
                { value: "en", label: "English" },
              ]}
            />
          </Field>
        </div>
      </Card>

      {panels.map((panel) => {
        const Render = panel.render;
        return (
          <Card key={panel.id} title={t(panel.titleKey)}>
            <Render />
          </Card>
        );
      })}

      <Card title={t("settings.rejected")}>
        <p className="muted">{t("settings.rejectedHint")}</p>
        <DataTable
          rows={failed}
          empty={t("settings.noRejected")}
          columns={[
            { key: "entity", header: t("settings.entity"), render: (row) => row.entity },
            { key: "id", header: t("settings.record"), render: (row) => row.entityId },
            { key: "error", header: t("settings.reason"), render: (row) => row.error ?? "" },
            {
              key: "actions",
              header: "",
              render: (row) => (
                <span className="row-actions">
                  <button type="button" onClick={() => void syncEngine.retryFailed(row.seq!)}>
                    {t("action.retry")}
                  </button>
                  <button
                    type="button"
                    className="danger"
                    onClick={() => void syncEngine.discardFailed(row.seq!)}
                  >
                    {t("action.discard")}
                  </button>
                </span>
              ),
            },
          ]}
        />
      </Card>
    </Page>
  );
}
