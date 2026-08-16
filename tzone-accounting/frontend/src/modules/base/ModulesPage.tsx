/** What is installed, what it contributed, and in what order it loaded.
 *
 * This screen exists because a hundred-module system is only maintainable if you can see the
 * graph without reading the code. */

import { useI18n } from "../../core/i18n";
import { getRegistry } from "../../core/registry";
import { Card, DataTable, Page } from "../../core/ui";

export function ModulesPage() {
  const { t, locale } = useI18n();
  const registry = getRegistry();
  const description = registry.describe();

  return (
    <Page title={t("modules.title")} subtitle={t("modules.subtitle", { count: description.modules.length })}>
      <Card>
        <DataTable
          rows={description.modules}
          empty={t("modules.empty")}
          columns={[
            {
              key: "order",
              header: "#",
              render: (row) => description.installOrder.indexOf(row.key) + 1,
            },
            {
              key: "name",
              header: t("modules.name"),
              render: (row) => (
                <div>
                  <strong>{locale === "ar" ? row.nameAr || row.name : row.name}</strong>
                  <div className="muted small">{row.key}</div>
                </div>
              ),
            },
            { key: "category", header: t("modules.category"), render: (row) => row.category },
            {
              key: "depends",
              header: t("modules.depends"),
              render: (row) =>
                row.depends.length ? (
                  <span className="chips">
                    {row.depends.map((dependency) => (
                      <span key={dependency} className="chip">
                        {dependency}
                      </span>
                    ))}
                  </span>
                ) : (
                  <span className="muted">—</span>
                ),
            },
            {
              key: "entities",
              header: t("modules.entities"),
              render: (row) =>
                row.entities.length ? row.entities.join(", ") : <span className="muted">—</span>,
            },
            { key: "summary", header: t("modules.summary"), render: (row) => row.summary },
          ]}
        />
      </Card>

      <Card title={t("modules.hooks")}>
        <p className="muted">{t("modules.hooksHint")}</p>
        <DataTable
          rows={Object.entries(description.hooks).map(([hook, handlers]) => ({ hook, handlers }))}
          empty={t("modules.empty")}
          columns={[
            { key: "hook", header: t("modules.hook"), render: (row) => <code>{row.hook}</code> },
            {
              key: "handlers",
              header: t("modules.handlers"),
              render: (row) => row.handlers.join(" → "),
            },
          ]}
        />
      </Card>
    </Page>
  );
}
